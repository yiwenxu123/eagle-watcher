"""菜单栏 App — rumps 扁平菜单"""
import logging
import subprocess
import threading
from pathlib import Path

import rumps

from config import (
    load_config, load_themes, save_themes,
    set_current_theme, get_current_theme, get_theme_names,
)
from eagle_api import EagleAPI
from analyzer import decide
from knowledge import match_by_filename, record_match

_LOG = logging.getLogger("menu")

# 通用箱可能的名称
INBOX_NAMES = ["_通用箱", "通用箱", "_inbox", "inbox"]


class EagleWatcherMenu(rumps.App):
    """菜单栏 App"""

    def __init__(self):
        super().__init__("🖼")
        self.cfg = load_config()
        self.eagle = EagleAPI(
            base_url=self.cfg["eagle"]["host"],
            token=self.cfg["eagle"]["token"],
        )

        theme = get_current_theme() or "无（自动匹配）"
        self.theme_title = rumps.MenuItem(f"📁 当前主题：{theme}")

        # 动态加载主题列表
        self.theme_items = []
        self._load_theme_items()

        self.cancel_btn = rumps.MenuItem("取消 — 自动匹配", callback=self._on_cancel)
        self.today_item = rumps.MenuItem("📥 今日入库：--")
        self.inbox_item = rumps.MenuItem("📦 通用箱：--")
        self.open_btn = rumps.MenuItem("打开 Eagle", callback=self._on_open)
        self.sort_btn = rumps.MenuItem("整理通用箱", callback=self._on_sort)
        self.manage_btn = rumps.MenuItem("管理主题", callback=self._on_manage)
        self.quit_btn = rumps.MenuItem("退出", callback=lambda _: rumps.quit_application())

        self._build_menu()

    def _build_menu(self):
        """构建菜单"""
        self.menu = [
            self.theme_title,
            None,
            *self.theme_items,
            self.cancel_btn,
            None,
            self.today_item,
            self.inbox_item,
            None,
            self.open_btn,
            self.sort_btn,
            None,
            self.manage_btn,
            self.quit_btn,
        ]

        self._mark_theme()
        # 用 rumps.Timer 确保创建在主线程 RunLoop 上，2 秒后执行一次
        rumps.Timer(self._delayed_init, 2.0).start()

    def _load_theme_items(self):
        """动态加载主题列表"""
        themes = get_theme_names()
        self.theme_items = []
        for name in themes:
            item = rumps.MenuItem(f"   {name}")
            item.set_callback(lambda _, n=name: self._on_switch(n))
            self.theme_items.append(item)

    def _delayed_init(self, timer):
        """首次初始化（只执行一次），然后启动 30 秒定时器"""
        try:
            self._refresh_status()
        except Exception:
            pass
        timer.stop()  # 只执行一次
        rumps.Timer(self._tick, 30).start()

    # —— 主题 ——

    def _mark_theme(self):
        cur = get_current_theme()
        for item, name in zip(self.theme_items, get_theme_names()):
            item.title = f"{'✅' if name == cur else '  '} {name}"

    def _refresh_theme(self):
        cur = get_current_theme()
        self.theme_title.title = f"📁 当前主题：{cur or '无（自动匹配）'}"
        self._load_theme_items()
        self._build_menu()
        self._mark_theme()

    def _on_switch(self, name):
        set_current_theme(name)
        self._refresh_theme()
        _notify(f"主题已切换为：{name}")

    def _on_cancel(self, _):
        set_current_theme(None)
        self._refresh_theme()
        _notify("已取消主题，恢复自动匹配模式")

    # —— 状态刷新（在主线程，可直接更新 UI） ——

    def _tick(self, _):
        self._refresh_status()

    def _refresh_status(self):
        try:
            folders = self.eagle.list_folders()
            inbox_id = None
            for f in folders:
                if f.get("name") in INBOX_NAMES:
                    inbox_id = f.get("id")
                    break

            all_items = []
            try:
                all_items = self.eagle.list_items()
            except Exception:
                pass

            from datetime import datetime
            today_date = datetime.now().strftime("%Y-%m-%d")
            inbox_count = 0
            today_count = 0
            for item in all_items:
                btime = item.get("btime", 0) / 1000
                if btime > 0:
                    if datetime.fromtimestamp(btime).strftime("%Y-%m-%d") == today_date:
                        today_count += 1
                folders_list = item.get("folders", [])
                if inbox_id and inbox_id in folders_list:
                    inbox_count += 1

            _LOG.info(f"status: today={today_count} inbox={inbox_count}")
            self.today_item.title = f"📥 今日入库：{today_count}"
            self.inbox_item.title = f"📦 通用箱：{inbox_count} 个待分类"
        except Exception as e:
            _LOG.warning(f"status error: {e}")
            self.today_item.title = "📥 今日入库：--"
            self.inbox_item.title = "📦 通用箱：--"

    # —— 打开 Eagle ——

    def _on_open(self, _):
        """打开 Eagle 应用"""
        def _do():
            try:
                # Eagle API 不支持文件夹导航，直接打开应用
                subprocess.Popen(["open", "-a", "Eagle"])
                _notify("已打开 Eagle，请在侧边栏切换到「通用箱」文件夹")
            except Exception as e:
                _LOG.warning(f"打开 Eagle 失败：{e}")
                _alert(f"打开 Eagle 失败：{e}")
        threading.Thread(target=_do, daemon=True).start()

    # —— 通用箱整理 ——

    def _on_sort(self, _):
        def _do():
            _LOG.info("sort: start")
            import time
            try:
                # 获取所有素材（带重试）
                all_items = []
                for attempt in range(3):
                    try:
                        all_items = self.eagle.list_items()
                        break
                    except Exception as e:
                        _LOG.warning(f"sort: item/list error (attempt {attempt+1}/3): {e}")
                        time.sleep(2)

                if not all_items:
                    rumps.alert("Eagle 接口暂时无响应，请稍后重试")
                    return

                # 查找通用箱文件夹
                folders = self.eagle.list_folders()
                inbox_id = None
                for f in folders:
                    if f.get("name") in INBOX_NAMES:
                        inbox_id = f.get("id")
                        _LOG.info(f"找到通用箱：{f.get('name')} (ID: {inbox_id})")
                        break

                if not inbox_id:
                    # 列出所有文件夹名帮助调试
                    folder_names = [f.get("name") for f in folders]
                    _LOG.warning(f"未找到通用箱，已有文件夹：{folder_names}")
                    rumps.alert(
                        title="整理通用箱",
                        message=f"没有找到通用箱文件夹\n\n已有文件夹：{', '.join(folder_names)}\n\n请在 Eagle 中创建名为「通用箱」的文件夹",
                    )
                    return

                # 筛选通用箱中的素材
                inbox_items = [it for it in all_items if inbox_id in it.get("folders", [])]
                if not inbox_items:
                    rumps.alert(title="整理通用箱", message="通用箱里没有待分类素材 🎉")
                    return

                _LOG.info(f"sort: {len(inbox_items)} items to process")

                # 分析所有待分类素材（使用知识库匹配，不受当前主题影响）
                analyzed_items = []
                for item in inbox_items:
                    filename = item.get("name", "未知")
                    ext = item.get("ext", "")
                    full_name = f"{filename}.{ext}"
                    # 直接用知识库匹配，不使用 decide()（后者会优先用当前主题）
                    kb_match = match_by_filename(full_name)
                    if kb_match:
                        suggestion = kb_match["theme"]
                        tags = kb_match["tags"]
                    else:
                        suggestion = "（未匹配）"
                        tags = []
                    analyzed_items.append({
                        "item": item,
                        "filename": full_name,
                        "suggestion": suggestion,
                        "tags": tags,
                    })

                # 显示批量操作选择
                action = _dialog_sort_batch(len(analyzed_items))
                if not action:
                    return

                confirmed = 0
                skipped = 0

                if action == "all_confirm":
                    # 一键全部确认
                    for analyzed in analyzed_items:
                        if analyzed["suggestion"] == "（未匹配）":
                            skipped += 1
                            continue
                        record_match(
                            analyzed["filename"],
                            Path(analyzed["filename"]).stem,
                            analyzed["suggestion"],
                            analyzed["tags"],
                        )
                        confirmed += 1
                        _LOG.info(f"  ✅ {analyzed['filename']} → {analyzed['suggestion']}")

                elif action == "by_theme":
                    # 按主题批量确认
                    theme_groups = {}
                    for analyzed in analyzed_items:
                        theme = analyzed["suggestion"]
                        if theme not in theme_groups:
                            theme_groups[theme] = []
                        theme_groups[theme].append(analyzed)

                    for theme, items in theme_groups.items():
                        if theme == "（未匹配）":
                            skipped += len(items)
                            continue

                        filenames = [Path(a["filename"]).name for a in items[:10]]
                        if len(items) > 10:
                            filenames.append(f"...还有{len(items)-10}个")

                        result = _dialog_confirm_theme(theme, filenames, len(items))
                        if result:
                            for analyzed in items:
                                record_match(
                                    analyzed["filename"],
                                    Path(analyzed["filename"]).stem,
                                    analyzed["suggestion"],
                                    analyzed["tags"],
                                )
                                confirmed += 1
                        else:
                            skipped += len(items)

                elif action == "one_by_one":
                    # 逐个确认
                    themes = get_theme_names()
                    for analyzed in analyzed_items:
                        result = _dialog_sort_single(
                            Path(analyzed["filename"]).name,
                            analyzed["suggestion"],
                            ", ".join(analyzed["tags"][:6]) if analyzed["tags"] else "—",
                            themes,
                        )
                        if result == "confirm":
                            record_match(
                                analyzed["filename"],
                                Path(analyzed["filename"]).stem,
                                analyzed["suggestion"],
                                analyzed["tags"],
                            )
                            confirmed += 1
                        elif result == "skip":
                            skipped += 1
                        elif result and result != "cancel":
                            # 用户指定了其他主题
                            record_match(
                                analyzed["filename"],
                                Path(analyzed["filename"]).stem,
                                result,
                                [result],
                            )
                            confirmed += 1
                        else:
                            skipped += 1

                rumps.alert(
                    title="整理完成",
                    message=f"已确认 {confirmed} 个 | 跳过 {skipped} 个",
                )
                _LOG.info(f"sort: done (confirmed={confirmed}, skipped={skipped})")
            except Exception as e:
                _LOG.warning(f"sort error: {e}")
                rumps.alert(title="整理失败", message=str(e))

        threading.Thread(target=_do, daemon=True).start()

    # —— 管理主题 ——

    def _on_manage(self, _):
        """管理主题 - 使用原生 rumps 对话框"""
        themes = get_theme_names()

        if not themes:
            # 没有主题，直接创建
            name = _input_text("暂无主题", "输入新主题名称：")
            if name:
                _create_theme(name)
                self._refresh_theme()
            return

        # 显示主题管理菜单
        choice = _theme_manage_menu(themes)
        if not choice:
            return

        if choice == "create":
            name = _input_text("创建主题", "输入新主题名称：")
            if name:
                _create_theme(name)
                self._refresh_theme()

        elif choice.startswith("edit:"):
            theme_name = choice[5:]
            self._edit_theme(theme_name)

        elif choice.startswith("delete:"):
            theme_name = choice[7:]
            if _confirm(f'确定删除主题「{theme_name}」？\n\n此操作不可撤销。'):
                _delete_theme(theme_name)
                self._refresh_theme()

    def _edit_theme(self, theme_name):
        """编辑主题"""
        themes = load_themes()
        theme_info = themes.get("themes", {}).get(theme_name, {})
        if not theme_info:
            rumps.alert(title="错误", message=f"主题 {theme_name} 不存在")
            return

        current_tags = ", ".join(theme_info.get("default_tags", []))
        current_folder = theme_info.get("eagle_folder", theme_name)

        # 选择编辑内容
        choice = _theme_edit_menu(theme_name, current_tags, current_folder)
        if not choice:
            return

        if choice == "tags":
            new_tags = _input_text(
                f"编辑标签 - {theme_name}",
                "输入默认标签（逗号分隔）：",
                default_text=current_tags,
            )
            if new_tags is not None:
                tags_list = [t.strip() for t in new_tags.split(",") if t.strip()]
                themes["themes"][theme_name]["default_tags"] = tags_list
                save_themes(themes)
                _notify(f'主题「{theme_name}」标签已更新')

        elif choice == "folder":
            new_folder = _input_text(
                f"编辑文件夹 - {theme_name}",
                "输入 Eagle 文件夹名：",
                default_text=current_folder,
            )
            if new_folder:
                themes["themes"][theme_name]["eagle_folder"] = new_folder
                save_themes(themes)
                _notify(f'主题「{theme_name}」文件夹已更新')

    def _refresh_menu(self):
        """刷新菜单（在主线程调用）"""
        self._load_theme_items()
        self._build_menu()
        self._mark_theme()


# —— 原生对话框辅助函数 ——

def _notify(msg):
    """发送 macOS 通知"""
    subprocess.run(
        ["osascript", "-e", f'display notification "{msg}" with title "素材管家"'],
        capture_output=True, timeout=5,
    )


def _alert(msg, title="素材管家"):
    """显示警告对话框"""
    rumps.alert(title=title, message=msg)


def _confirm(msg, title="确认") -> bool:
    """显示确认对话框"""
    result = rumps.alert(title=title, message=msg, ok="确定", cancel="取消")
    return result == 1


def _input_text(title, message, default_text="") -> str:
    """显示文本输入对话框，返回用户输入的文本，取消返回 None"""
    win = rumps.Window(
        message=message,
        title=title,
        default_text=default_text,
        ok="确定",
        cancel="取消",
        dimensions=(320, 24),
    )
    result = win.run()
    if result.clicked == 1:
        return result.text.strip()
    return None


def _theme_manage_menu(themes) -> str:
    """主题管理菜单，返回选择的操作"""
    theme_list = "\n".join(f"  • {t}" for t in themes)
    msg = f"当前主题：\n{theme_list}\n\n选择操作："

    # 使用 choose from list 的 osascript（rumps 不支持列表选择）
    items_str = '", "'.join(themes)
    script = (
        'try\n'
        f'  set choices to {{"{items_str}"}}\n'
        '  set c to choose from list choices '
        f'with prompt "{msg}" '
        'with title "素材管家 - 主题管理" '
        'OK button name "编辑" '
        'Cancel button name "其它操作"\n'
        '  if c is false then return "action_menu"\n'
        '  return "edit:" & item 1 of c\n'
        'on error\n'
        '  return "action_menu"\n'
        'end try'
    )
    try:
        r = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=30,
        )
        result = r.stdout.strip()
        if result == "action_menu":
            # 显示操作菜单
            return _theme_action_menu(themes)
        return result
    except Exception as e:
        _LOG.warning(f"theme_manage_menu error: {e}")
        return ""


def _theme_action_menu(themes) -> str:
    """主题操作菜单"""
    script = (
        'try\n'
        '  set c to button returned of '
        '(display dialog "主题管理" buttons {"取消", "删除主题", "创建新主题"} default button 1)\n'
        '  return c\n'
        'on error\n'
        '  return "取消"\n'
        'end try'
    )
    try:
        r = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=15,
        )
        action = r.stdout.strip()
        if action == "创建新主题":
            return "create"
        elif action == "删除主题":
            # 选择要删除的主题
            items_str = '", "'.join(themes)
            del_script = (
                'try\n'
                f'  set choices to {{"{items_str}"}}\n'
                '  set c to choose from list choices '
                'with prompt "选择要删除的主题：" '
                'OK button name "删除" '
                'Cancel button name "取消"\n'
                '  if c is false then return ""\n'
                '  return "delete:" & item 1 of c\n'
                'on error\n'
                '  return ""\n'
                'end try'
            )
            r2 = subprocess.run(
                ["osascript", "-e", del_script],
                capture_output=True, text=True, timeout=15,
            )
            return r2.stdout.strip()
    except Exception as e:
        _LOG.warning(f"theme_action_menu error: {e}")
    return ""


def _theme_edit_menu(theme_name, current_tags, current_folder) -> str:
    """主题编辑菜单"""
    script = (
        'try\n'
        '  set c to button returned of '
        f'(display dialog "编辑主题：{theme_name}" & return & return'
        f' & "当前标签：{current_tags or "无"}" & return'
        f' & "文件夹：{current_folder}" & return & return'
        ' & "选择要编辑的内容："'
        ' buttons {"取消", "编辑标签", "编辑文件夹"} default button 1)\n'
        '  return c\n'
        'on error\n'
        '  return "取消"\n'
        'end try'
    )
    try:
        r = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=15,
        )
        choice = r.stdout.strip()
        if choice == "编辑标签":
            return "tags"
        elif choice == "编辑文件夹":
            return "folder"
    except Exception as e:
        _LOG.warning(f"theme_edit_menu error: {e}")
    return ""


def _dialog_sort_batch(count: int) -> str:
    """显示批量操作选择对话框"""
    result = rumps.alert(
        title="通用箱整理",
        message=f"待分类素材：{count} 个\n\n选择整理方式：",
        ok="一键全部确认",
        cancel="取消",
        other="更多选项",
    )
    if result == 1:
        return "all_confirm"
    elif result == -1:
        # "更多选项"按钮
        script = (
            'try\n'
            '  set c to button returned of '
            '(display dialog "选择整理方式：" '
            'buttons {"取消", "逐个确认", "按主题批量"} default button 1)\n'
            '  return c\n'
            'on error\n'
            '  return "取消"\n'
            'end try'
        )
        try:
            r = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=15,
            )
            choice = r.stdout.strip()
            if choice == "按主题批量":
                return "by_theme"
            elif choice == "逐个确认":
                return "one_by_one"
        except Exception:
            pass
    return ""


def _dialog_confirm_theme(theme: str, filenames: list[str], count: int) -> bool:
    """显示主题批量确认对话框，返回 True 表示确认"""
    file_list = "\n".join(f"  • {name}" for name in filenames)
    msg = f"主题：{theme}\n数量：{count} 个\n\n素材列表：\n{file_list}"
    result = rumps.alert(
        title="主题批量确认",
        message=msg,
        ok="确认全部归入",
        cancel="跳过",
    )
    return result == 1


def _dialog_sort_single(filename: str, suggestion: str, tags: str, themes: list) -> str:
    """单个素材确认对话框"""
    choices = " | ".join(themes[:8])
    msg = f"文件：{filename}\n建议归入：{suggestion}\n标签：{tags}\n\n主题列表：{choices}"

    result = rumps.alert(
        title="整理建议",
        message=msg,
        ok="确认归入",
        cancel="跳过",
        other="指定主题",
    )

    if result == 1:
        return "confirm"
    elif result == -1:
        # "指定主题" - 让用户选择
        items_str = '", "'.join(themes)
        script = (
            'try\n'
            f'  set choices to {{"{items_str}"}}\n'
            '  set c to choose from list choices '
            'with prompt "选择主题：" '
            'OK button name "确定" '
            'Cancel button name "取消"\n'
            '  if c is false then return "cancel"\n'
            '  return item 1 of c\n'
            'on error\n'
            '  return "cancel"\n'
            'end try'
        )
        try:
            r = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=15,
            )
            chosen = r.stdout.strip()
            if chosen and chosen != "cancel":
                return chosen
        except Exception:
            pass
        return "skip"
    else:
        return "skip"


def _create_theme(name):
    """创建新主题"""
    t = load_themes()
    t.setdefault("themes", {})[name] = {
        "created_at": __import__("datetime").datetime.now().isoformat()[:10],
        "default_tags": [],
        "eagle_folder": name,
    }
    save_themes(t)
    _notify(f'主题「{name}」已创建')


def _delete_theme(name):
    """删除主题"""
    data = load_themes()
    data["themes"].pop(name, None)
    save_themes(data)
    if get_current_theme() == name:
        set_current_theme(None)
    _notify(f'主题「{name}」已删除')
