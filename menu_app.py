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
from services.sort_service import SortService

_LOG = logging.getLogger("menu")


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
        self.sort_btn = rumps.MenuItem("整理待分类", callback=self._on_sort)
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

    def _rebuild_menu(self, _=None):
        self._load_theme_items()
        self.menu.clear()
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

    def _refresh_theme(self):
        cur = get_current_theme()
        self.theme_title.title = f"📁 当前主题：{cur or '无（自动匹配）'}"
        self._rebuild_menu()

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
            from datetime import datetime
            today_date = datetime.now().strftime("%Y-%m-%d")
            today_count = 0
            inbox_count = 0

            all_items = []
            try:
                all_items = self.eagle.list_items()
            except Exception:
                pass

            for item in all_items:
                btime = item.get("btime", 0) / 1000
                if btime > 0:
                    if datetime.fromtimestamp(btime).strftime("%Y-%m-%d") == today_date:
                        today_count += 1
                if "待分类" in item.get("tags", []):
                    inbox_count += 1

            _LOG.info(f"status: today={today_count} inbox={inbox_count}")
            self.today_item.title = f"📥 今日入库：{today_count}"
            self.inbox_item.title = f"📦 待分类：{inbox_count} 个"
        except Exception as e:
            _LOG.warning(f"status error: {e}")
            self.today_item.title = "📥 今日入库：--"
            self.inbox_item.title = "📦 待分类：--"

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

    # —— 整理待分类：子菜单浏览 ——

    _sort_items: list = []
    _sort_index: int = 0
    _sort_confirmed: int = 0
    _sort_skipped: int = 0
    _sort_service = None

    def _on_sort(self, _):
        _LOG.info("sort: start")
        svc = SortService(self.eagle)

        def fetch():
            try:
                items = svc.get_inbox_items()
                if not items:
                    rumps.alert(title="整理待分类", message="没有待分类素材 🎉")
                    return
                self._sort_items = [(svc.analyze(it), it) for it in items]
                self._sort_index = 0
                self._sort_confirmed = 0
                self._sort_skipped = 0
                self._sort_service = svc
                self._show_sort_panel()
            except Exception as e:
                _LOG.warning("sort fetch error: %s", e)
                rumps.alert(title="整理失败", message=str(e))

        threading.Thread(target=fetch, daemon=True).start()

    def _show_sort_panel(self):
        self.menu.clear()
        items = []
        total = len(self._sort_items)

        if self._sort_index >= total:
            items = [
                rumps.MenuItem("✅ 整理完成"),
                None,
                rumps.MenuItem(f"确认 {self._sort_confirmed}  |  跳过 {self._sort_skipped}"),
                None,
                rumps.MenuItem("返回主菜单", callback=self._rebuild_menu),
            ]
            self.menu = items
            self._refresh_status()
            return

        a, raw = self._sort_items[self._sort_index]
        filename = Path(a["filename"]).name
        suggested = a["suggested_theme"]
        is_matched = suggested != "（未匹配）"

        file_icon = "🖼" if "待分类" in raw.get("tags", []) else "📄"
        items.append(rumps.MenuItem(f"{file_icon} {filename}"))

        if is_matched:
            conf_pct = int(a.get("confidence", 0) * 100)
            items.append(rumps.MenuItem(f"💡 建议 → {suggested} ({conf_pct}%)"))
            items.append(None)
            confirm_item = rumps.MenuItem(f"✅ 确认归入「{suggested}」")
            confirm_item.set_callback(lambda _: self._do_sort_confirm(a, raw))
            items.append(confirm_item)
        else:
            items.append(rumps.MenuItem("💡 未匹配到已有主题"))
            items.append(None)

        choose_item = rumps.MenuItem("🔄 选择其他主题")
        choose_item.set_callback(lambda _: self._do_sort_choose(a, raw))
        items.append(choose_item)

        skip_item = rumps.MenuItem("⏭️ 跳过")
        skip_item.set_callback(lambda _: self._do_sort_skip())
        items.append(skip_item)

        items.append(None)
        if is_matched:
            all_item = rumps.MenuItem(f"⚡ 确认全部 (剩余{total - self._sort_index}项)")
            all_item.set_callback(lambda _: self._do_sort_all())
            items.append(all_item)

        items.append(rumps.MenuItem(f"📊 {self._sort_index + 1}/{total}"))

        self.menu = items

    def _do_sort_confirm(self, a, raw):
        if self._sort_service.confirm(
            raw, a["suggested_theme"], a["suggested_tags"], a["filename"],
        ):
            self._sort_confirmed += 1
        else:
            self._sort_skipped += 1
        self._sort_index += 1
        self._show_sort_panel()

    def _do_sort_choose(self, a, raw):
        themes = get_theme_names()
        default = a["suggested_theme"] if a["suggested_theme"] != "（未匹配）" else ""
        win = rumps.Window(
            message=f"可用主题：{'、'.join(themes)}\n\n输入主题名：",
            title="选择主题",
            default_text=default,
            ok="确定",
            cancel="取消",
            dimensions=(320, 24),
        )
        resp = win.run()
        if resp.clicked == 1 and resp.text.strip():
            chosen = resp.text.strip()
            tags = a["suggested_tags"] if chosen == a["suggested_theme"] else [chosen]
            if self._sort_service.confirm(raw, chosen, tags, a["filename"]):
                self._sort_confirmed += 1
            else:
                self._sort_skipped += 1
        else:
            self._sort_skipped += 1
        self._sort_index += 1
        self._show_sort_panel()

    def _do_sort_skip(self):
        self._sort_skipped += 1
        self._sort_index += 1
        self._show_sort_panel()

    def _do_sort_all(self):
        remaining = self._sort_items[self._sort_index:]
        for a, raw in remaining:
            if a["suggested_theme"] != "（未匹配）":
                if self._sort_service.confirm(
                    raw, a["suggested_theme"], a["suggested_tags"], a["filename"],
                ):
                    self._sort_confirmed += 1
                else:
                    self._sort_skipped += 1
            else:
                self._sort_skipped += 1
        self._sort_index = len(self._sort_items)
        self._show_sort_panel()

    # —— 管理主题：子菜单 CRUD ——

    def _on_manage(self, _):
        self._show_manage_panel()

    def _show_manage_panel(self):
        self.menu.clear()
        themes = get_theme_names()
        items = []

        if not themes:
            items.append(rumps.MenuItem("📭 暂无主题"))
            items.append(None)
            new_item = rumps.MenuItem("➕ 创建主题")
            new_item.set_callback(lambda _: self._do_create_theme())
            items.append(new_item)
            items.append(None)
            items.append(rumps.MenuItem("← 返回", callback=self._rebuild_menu))
            self.menu = items
            return

        for name in themes:
            info = load_themes().get("themes", {}).get(name, {})
            tags = info.get("default_tags", [])
            folder = info.get("eagle_folder", name)
            tag_str = "、".join(tags) if tags else "无"
            theme_menu = rumps.MenuItem(f"🎨 {name}")
            sub = [
                rumps.MenuItem(f"🏷️ 标签: {tag_str}"),
                rumps.MenuItem(f"📁 文件夹: {folder}"),
                None,
            ]
            edit_tag = rumps.MenuItem("✏️ 编辑标签")
            edit_tag.set_callback(lambda _, n=name: self._do_edit_theme_tags(n))
            sub.append(edit_tag)
            edit_folder = rumps.MenuItem("📁 修改文件夹")
            edit_folder.set_callback(lambda _, n=name: self._do_edit_theme_folder(n))
            sub.append(edit_folder)
            sub.append(None)
            del_item = rumps.MenuItem("🗑️ 删除")
            del_item.set_callback(lambda _, n=name: self._do_delete_theme(n))
            sub.append(del_item)
            theme_menu.add(sub)
            items.append(theme_menu)

        items.append(None)
        new_item = rumps.MenuItem("➕ 创建主题")
        new_item.set_callback(lambda _: self._do_create_theme())
        items.append(new_item)
        items.append(None)
        items.append(rumps.MenuItem("← 返回", callback=self._rebuild_menu))

        self.menu = items

    def _do_create_theme(self):
        name = _input_text("创建主题", "输入主题名称：")
        if not name:
            self._show_manage_panel()
            return
        tags_str = _input_text("标签", "默认标签（逗号分隔，可选）：")
        tags = [t.strip() for t in tags_str.split(",")] if tags_str else []
        _create_theme(name, tags)
        _notify(f'主题「{name}」已创建')
        self._show_manage_panel()

    def _do_edit_theme_tags(self, name):
        info = load_themes().get("themes", {}).get(name, {})
        current = ", ".join(info.get("default_tags", []))
        new_val = _input_text(f"编辑标签 - {name}", "标签（逗号分隔）：", default_text=current)
        if new_val is not None:
            themes = load_themes()
            themes["themes"][name]["default_tags"] = [
                t.strip() for t in new_val.split(",") if t.strip()
            ]
            save_themes(themes)
            _notify(f'主题「{name}」标签已更新')
        self._show_manage_panel()

    def _do_edit_theme_folder(self, name):
        info = load_themes().get("themes", {}).get(name, {})
        current = info.get("eagle_folder", name)
        new_val = _input_text(f"修改文件夹 - {name}", "Eagle 文件夹名：", default_text=current)
        if new_val:
            themes = load_themes()
            themes["themes"][name]["eagle_folder"] = new_val
            save_themes(themes)
            _notify(f'主题「{name}」文件夹已更新')
        self._show_manage_panel()

    def _do_delete_theme(self, name):
        result = rumps.alert(
            title="删除主题",
            message=f"确定删除主题「{name}」？\n此操作不可撤销。",
            ok="删除", cancel="取消",
        )
        if result == 1:
            _delete_theme(name)
            _notify(f'主题「{name}」已删除')
        self._show_manage_panel()

    def _refresh_menu(self):
        """刷新菜单（在主线程调用）"""
        self._load_theme_items()
        self._build_menu()
        self._mark_theme()


# —— 原生对话框辅助函数 ——

def _notify(msg):
    rumps.notification(title="素材管家", subtitle="", message=msg)


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


def _create_theme(name, tags=None):
    t = load_themes()
    t.setdefault("themes", {})[name] = {
        "created_at": __import__("datetime").datetime.now().isoformat()[:10],
        "default_tags": tags or [],
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
