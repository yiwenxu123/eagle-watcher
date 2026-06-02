"""
Downloads 文件夹监控

监听 Downloads 目录的新增文件 -> 决策引擎 -> 自动入库 Eagle。
使用分层可回退文件监控（FSEvents → inode 轮询）。
"""

import logging
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from collections import deque
from typing import Optional

from eagle_watcher.config import load_config, ensure_data_dir
from eagle_watcher.services.state_manager import get_state_manager
from eagle_watcher.services.file_watcher import create_watcher
from eagle_watcher.services.history import append as history_append
from eagle_watcher.eagle_api import EagleAPI, create_eagle_api
from eagle_watcher.analyzer import decide
from eagle_watcher.ai_tagger import analyze_image
from eagle_watcher.notifier import notify

_LOG = logging.getLogger("watcher")
_processing_files: set[str] = set()
_retry_queue: deque[tuple[str, int]] = deque(maxlen=100)  # (file_path, attempt)
_MAX_RETRIES = 3
_MAX_AI_FILE_SIZE = 20 * 1024 * 1024  # 20MB - Qwen-VL API limit
_AI_ALLOWED_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"}  # 支持的文件格式
_retry_lock = threading.Lock()

# AI 标签中的通用英文词，不适合作为知识库关键词（避免污染）
_GENERIC_AI_TAGS = frozenset({
    "portrait", "landscape", "screenshot", "photo", "image", "picture",
    "photograph", "snapshot", "selfie",
    "food", "drink", "people", "person", "animal", "pet", "nature",
    "city", "urban", "street", "building", "house",
    "art", "design", "illustration", "drawing", "painting", "graphic",
    "background", "texture", "pattern", "wallpaper",
    "indoor", "outdoor", "day", "night", "sunset", "sunrise",
    "colorful", "black", "white", "dark", "light", "bright",
    "abstract", "modern", "vintage", "retro", "minimal",
    "front", "back", "side", "top", "bottom", "view", "closeup",
    "beautiful", "pretty", "nice", "cute", "cool", "amazing",
    "render", "mockup", "concept",
})

# ── 文件类型过滤预设 ──
FILE_FILTER_PRESETS: dict[str, dict] = {
    "all":     {"label": "全部类型",       "extensions": []},
    "image":   {"label": "图片",           "extensions": [".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp",
                                                          ".svg", ".ico", ".tiff", ".tif", ".heic", ".heif", ".raw"]},
    "document":{"label": "文档",           "extensions": [".md", ".txt", ".pdf", ".doc", ".docx", ".xls", ".xlsx",
                                                          ".ppt", ".pptx", ".csv", ".json", ".yaml", ".yml",
                                                          ".xml", ".html", ".htm"]},
    "video":   {"label": "视频",           "extensions": [".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv",
                                                          ".webm", ".m4v", ".mpg", ".mpeg", ".3gp"]},
}


def get_filter_presets() -> dict:
    """返回过滤预设信息（前端使用）"""
    return {k: {"label": v["label"]} for k, v in FILE_FILTER_PRESETS.items()}


def resolve_filter_extensions(file_filter: str | None) -> set[str]:
    """将过滤预设名解析为扩展名集合。None 或 'all' 返回空集（不过滤）。"""
    if not file_filter or file_filter == "all":
        return set()
    preset = FILE_FILTER_PRESETS.get(file_filter)
    if preset is None:
        return set()
    return set(preset["extensions"])


def _load_import_filters(cfg: Optional[dict] = None) -> tuple[set[str], set[str]]:
    """加载 import_filters 配置，返回 (allow_extensions, skip_extensions)。
    现有 config.yaml 可能不含 import_filters 键，用内建默认值兜底。
    """
    _DEFAULT_SKIP = {".tmp", ".part", ".download", ".crdownload"}
    if cfg is None:
        from eagle_watcher.config import load_config
        cfg = load_config()
    filters = cfg.get("import_filters", {})
    allowed = {e.lower() for e in filters.get("extensions", []) if e}
    skipped = {e.lower() for e in filters.get("skip_extensions", []) if e}
    if not skipped:
        skipped = _DEFAULT_SKIP
    return allowed, skipped


def _should_process_file(file_path: str, cfg: Optional[dict] = None,
                          file_filter: Optional[str] = None) -> bool:
    """检查文件扩展名是否允许处理。返回 True 表示应该处理。
    
    file_filter: 预设名（'image'/'document'/'video'）或 None（使用 config 过滤）
    """
    ext = Path(file_path).suffix.lower()
    # 始终检查跳过扩展名（临时文件等），无论是否用了 filter 预设
    allowed, skipped = _load_import_filters(cfg)
    if ext in skipped:
        return False
    # 再应用 filter 预设（优先级高于 config 的 allow list）
    if file_filter:
        allowed_exts = resolve_filter_extensions(file_filter)
        if not allowed_exts:
            return True  # 'all' → 不过滤
        return ext in allowed_exts
    # 未传 filter 时使用 config 配置的允许列表
    if allowed and ext not in allowed:
        return False
    return True


def count_files_by_type(directory: str, recursive: bool = True) -> dict:
    """统计目录下各类型文件数量，供扫描前确认使用"""
    path = Path(directory)
    if not path.is_dir():
        return {"total": 0, "extensions": {}}
    if recursive:
        files = [p for p in path.rglob("*") if p.is_file()]
    else:
        files = [p for p in path.iterdir() if p.is_file()]
    exts: dict[str, int] = {}
    for f in files:
        ext = f.suffix.lower() or "(无扩展名)"
        exts[ext] = exts.get(ext, 0) + 1
    return {"total": len(files), "extensions": exts}

# ── 目录扫描（批量处理已有文件）──
_scan_progress: dict = {}
_scan_lock = threading.Lock()


def get_scan_progress() -> dict:
    """获取当前扫描进度"""
    with _scan_lock:
        return dict(_scan_progress) if _scan_progress else {"status": "idle"}


def scan_directory(eagle: EagleAPI, directory: str, recursive: bool = True,
                   file_filter: Optional[str] = None):
    """在后台线程中扫描目录并批量处理所有已有文件"""
    global _scan_progress
    with _scan_lock:
        if _scan_progress.get("status") == "scanning":
            _LOG.warning("已有扫描进行中，忽略: %s", directory)
            return
        _scan_progress = {
            "status": "scanning",
            "directory": directory,
            "total": 0,
            "processed": 0,
            "skipped": 0,
            "failed": 0,
            "current_file": "",
            "error": None,  # 清除前次失败的错误消息
        }

    def _worker():
        sm = get_state_manager()
        path = Path(directory)
        if not path.is_dir():
            with _scan_lock:
                _scan_progress["status"] = "failed"
                _scan_progress["error"] = "目录不存在"
            return

        if recursive:
            all_files = [p for p in path.rglob("*") if p.is_file()]
        else:
            all_files = [p for p in path.iterdir() if p.is_file()]

        # 文件类型过滤
        cfg = load_config()
        filtered = [f for f in all_files if _should_process_file(str(f), cfg, file_filter)]
        skipped_files_count = len(all_files) - len(filtered)
        files = filtered

        actual_total = len(all_files)
        with _scan_lock:
            _scan_progress["total"] = actual_total
            _scan_progress["skipped_ext"] = skipped_files_count

        _LOG.info("开始扫描目录: %s (%d 个文件, 跳过 %d 个因类型不匹配)",
                  directory, actual_total, skipped_files_count)
        print(f"\n📂 开始扫描目录：{directory}（共 {actual_total} 个文件，"
              f"跳过 {skipped_files_count} 个因类型不匹配）")

        processed = 0
        skipped = 0
        failed = 0

        for file_path in files:
            fp = str(file_path)

            with _scan_lock:
                _scan_progress["current_file"] = file_path.name

            # 跳过已处理的
            if sm.is_file_processed(fp):
                skipped += 1
                continue

            try:
                _process_file(eagle, fp)
                processed += 1
            except Exception as e:
                _LOG.error("扫描处理失败: %s — %s", fp, e)
                print(f"  ❌ {file_path.name}: {e}")
                failed += 1

            with _scan_lock:
                _scan_progress["processed"] = processed
                _scan_progress["skipped"] = skipped
                _scan_progress["failed"] = failed

        with _scan_lock:
            _scan_progress["status"] = "completed"
            _scan_progress["current_file"] = ""

        _LOG.info("扫描完成: 目录=%s, 总计=%d, 已处理=%d, 跳过=%d, 失败=%d",
                  directory, actual_total, processed, skipped, failed)
        print(f"\n✅ 扫描完成：{directory}")
        print(f"   总计 {actual_total} 个文件 → 处理 {processed} 个，跳过 {skipped} 个，失败 {failed} 个")

    thread = threading.Thread(target=_worker, daemon=True, name="dir-scan")
    thread.start()


def _is_processed(file_path: str) -> bool:
    """只读检查文件是否已处理，不标记。"""
    state = get_state_manager()
    return state.is_file_processed(file_path)


def _trash_file(file_path: str) -> bool:
    """将文件移入 macOS 废纸篓（使用 NSWorkspace.recycleURLs）"""
    try:
        from AppKit import NSWorkspace, NSURL
        url = NSURL.fileURLWithPath_(file_path)
        ws = NSWorkspace.sharedWorkspace()
        result = ws.recycleURLs_completionHandler_([url], None)
        return bool(result)
    except Exception as e:
        _LOG.error("移入废纸篓失败 %s: %s", file_path, e)
        return False


def _check_result(result: dict, filename: str, theme: str, tags: list[str],
                   file_path: str = ""):
    sm = get_state_manager()
    if result.get("status") == "success":
        theme_label = theme or "通用箱"
        tag_str = ", ".join(tags)
        print(f"  ✅ {filename} → {theme_label} ｜ {tag_str}", flush=True)

        # 导入成功后持久化标记已处理
        if file_path:
            sm.mark_file_processed(file_path)

        sm.set_last_processed({
            "filename": filename,
            "theme": theme_label,
            "time": datetime.now().isoformat(),
            "status": "success",
        })

        # 根据配置策略处理原始文件
        cfg = load_config()
        delete_policy = cfg.get("delete_after_import", "trash")
        if delete_policy != "keep" and file_path and os.path.exists(file_path):
            if _trash_file(file_path):
                _LOG.info("已移入废纸篓: %s", filename)
            else:
                _LOG.error("移入废纸篓失败，保留原始文件: %s", filename)

        if theme:
            if cfg.get("notifications", {}).get("import_success", False):
                notify("素材管家", f"✅ {filename} → {theme_label}")
        else:
            if sm.check_and_set_inbox_notified():
                notify("素材管家", f"📦 通用箱有新素材：{filename}")

        history_append({"action": "import", "status": "success",
                        "filename": filename, "theme": theme_label, "tags": tags})
    else:
        print(f"  ❌ 入库失败：{filename} — {result}", flush=True)
        sm.set_last_processed({
            "filename": filename,
            "time": datetime.now().isoformat(),
            "status": "failed",
            "error": str(result),
        })
        history_append({"action": "import", "status": "failed",
                        "filename": filename, "error": str(result)})


def _process_file(eagle: EagleAPI, file_path: str):
    filename = Path(file_path).name

    # Simple conflict check: skip if same filename+size exists in recent 50 items
    file_size = os.path.getsize(file_path)
    file_name = Path(file_path).name
    try:
        recent = eagle.list_items(order_by="-btime", limit=50)
        for item in recent:
            item_name = item.get("name", "")
            item_ext = item.get("ext", "")
            # Eagle 返回的 ext 无点号前缀，如 "jpg" → 需要拼接比较
            item_full_name = f"{item_name}.{item_ext}" if item_ext else item_name
            if item_full_name == file_name:
                item_size = item.get("size", 0)
                if item_size and abs(item_size - file_size) < 1024:
                    _LOG.info("跳过已存在的文件: %s (同名+同大小)", file_name)
                    return
    except Exception:
        _LOG.debug("文件去重检查失败（非阻塞）: %s", file_name)

    decision = decide(filename)

    folder_id = None
    if decision.get("folder"):
        folder_id = eagle.get_or_create_folder(decision["folder"])

    tags = list(decision.get("tags", []))

    if decision["action"] == "ai_analyze":
        ai_result = None
        # P2-12: Pre-checks before calling Qwen-VL API
        ext = Path(file_path).suffix.lower()
        if ext not in _AI_ALLOWED_EXTS:
            print(f"  ⏭️ 不支持的文件格式 ({ext})，跳过AI分析: {filename}")
            decision["action"] = "inbox"
        elif file_size == 0:
            print(f"  ⏭️ 空文件，跳过AI分析: {filename}")
            decision["action"] = "inbox"
        elif file_size > _MAX_AI_FILE_SIZE:
            print(f"  ⏭️ 文件过大 ({file_size/1024/1024:.1f}MB)，跳过AI分析: {filename}")
            decision["action"] = "inbox"
        else:
            print(f"  🤖 文件名模糊，Qwen-VL 分析中：{filename}")
            ai_result = analyze_image(file_path)
        if ai_result:
            ai_tags = ai_result["tags"]
            suggested_name = ai_result["name"]
            print(f"  🏷  AI 识别结果：{' ｜ '.join(ai_tags)}")
            result = eagle.add_from_path(
                file_path,
                name=suggested_name,
                tags=tags + ai_tags,
                folder_id=folder_id,
            )
            _check_result(result, filename, decision.get("theme", ""), ai_tags, file_path)
            if result.get("status") != "success":
                raise Exception(f"Eagle import failed: {result}")
            # AI 分析成功：学习 AI 标签到当前项目的知识映射
            current_project = get_state_manager().get_current_project()
            if current_project and ai_tags:
                try:
                    from eagle_watcher.knowledge import record_match
                    # 取第一个非通用英文词的 AI 标签作为关键词，避免 "portrait→主题" 等污染
                    keyword = next((t for t in ai_tags if t.lower() not in _GENERIC_AI_TAGS), None)
                    if keyword:
                        record_match(filename, keyword, current_project, ai_tags)
                        _LOG.info("知识库已学习: %s → %s (来自 AI)", keyword, current_project)
                    else:
                        _LOG.debug("AI 标签均为通用词，跳过知识库学习: %s", ai_tags)
                except Exception as e:
                    _LOG.warning("AI 知识库学习失败: %s", e)
        else:
            print(f"  ⏭️ AI 分析跳过或失败，暂存到通用箱")
            result = eagle.add_from_path(
                file_path,
                tags=tags or ["待分类"],
                folder_id=folder_id,
            )
            _check_result(result, filename, "", tags or ["待分类"], file_path)
            if result.get("status") != "success":
                raise Exception(f"Eagle import failed: {result}")
        return

    if not tags:
        tags = ["待分类"]

    result = eagle.add_from_path(
        file_path,
        name=Path(file_path).stem,
        tags=tags,
        folder_id=folder_id,
    )
    _check_result(result, filename, decision.get("theme", ""), tags, file_path)
    if result.get("status") != "success":
        raise Exception(f"Eagle import failed: {result}")


def _on_file_detected(eagle: EagleAPI, file_path: str, attempt: int = 0):
    global _processing_files
    if file_path in _processing_files:
        return
    if attempt == 0 and _is_processed(file_path):
        return
    if not _should_process_file(file_path):
        _LOG.debug("跳过不符合筛选条件的文件: %s", file_path)
        return
    _processing_files.add(file_path)

    filename = Path(file_path).name
    if attempt > 0:
        _LOG.info("重试处理文件：%s（第 %d 次）", filename, attempt)
        print(f"\n🔄 重试处理：{filename}（第 {attempt} 次）")
    else:
        _LOG.info("检测到新文件：%s", filename)
        print(f"\n📥 检测到新文件：{filename}")
    try:
        _process_file(eagle, file_path)
    except Exception as e:
        _LOG.error("处理文件失败：%s — %s", file_path, e)
        if attempt < _MAX_RETRIES:
            with _retry_lock:
                _retry_queue.append((file_path, attempt + 1))
            _LOG.warning("加入重试队列: %s (第 %d 次)", filename, attempt + 1)
            print(f"  ⚠️  处理失败，将重试：{filename}")
        else:
            print(f"  ❌ 处理最终失败：{filename} — {e}")
    finally:
        _processing_files.discard(file_path)


def _resolve_watch_dirs(cfg: dict, extra_dirs: Optional[list[str]] = None,
                        temp_dirs: Optional[list[str]] = None) -> list[str]:
    """解析需要监控的目录列表

    合并 downloads + extra_watch_dirs (from config) + temp_dirs (from caller/state)
    + extra_dirs (from caller)，去重并过滤出不存在的目录。
    """
    dirs: list[str] = []
    downloads = cfg.get("paths", {}).get("downloads", "")
    if downloads:
        expanded = os.path.expanduser(downloads)
        if Path(expanded).is_dir():
            dirs.append(expanded)
        else:
            _LOG.warning("下载目录不存在，跳过: %s", downloads)

    configured_extra = cfg.get("paths", {}).get("extra_watch_dirs", [])
    if isinstance(configured_extra, list):
        for d in configured_extra:
            expanded = os.path.expanduser(d)
            if expanded in dirs:
                continue
            if Path(expanded).is_dir():
                dirs.append(expanded)
            else:
                _LOG.warning("额外监控目录不存在，跳过: %s", d)

    if temp_dirs:
        for d in temp_dirs:
            expanded = os.path.expanduser(d)
            if expanded in dirs:
                continue
            if Path(expanded).is_dir():
                dirs.append(expanded)
            else:
                _LOG.warning("临时监控目录不存在，跳过: %s", d)

    if extra_dirs:
        for d in extra_dirs:
            expanded = os.path.expanduser(d)
            if expanded in dirs:
                continue
            if Path(expanded).is_dir():
                dirs.append(expanded)
            else:
                _LOG.warning("指定目录不存在，跳过: %s", d)

    return dirs


def _reconcile_watchers(watchers: dict, configured: set[str], callback, poll_interval: float,
                        temp_dirs: Optional[list[str]] = None):
    """从 state 同步临时监控目录，启动新 watcher、停止已移除的。"""
    active_temp: set[str] = set()
    if temp_dirs:
        for d in temp_dirs:
            expanded = os.path.expanduser(d)
            if Path(expanded).is_dir():
                active_temp.add(expanded)

    expected = configured | active_temp
    current = set(watchers.keys())

    for d in current - expected:
        watchers[d].stop()
        del watchers[d]
        _LOG.info("已停止监控目录: %s", d)

    for d in expected - current:
        w = create_watcher(d, callback, poll_interval=poll_interval)
        w.start()
        watchers[d] = w
        print(f"👀 新监控已启动：{d}")


def run_watcher(eagle: Optional[EagleAPI] = None,
                extra_dirs: Optional[list[str]] = None):
    get_state_manager().set_watcher_running(True)
    ensure_data_dir()
    cfg = load_config()

    if eagle is None:
        eagle = create_eagle_api(cfg)

    sm = get_state_manager()
    watch_dirs = _resolve_watch_dirs(cfg, extra_dirs, temp_dirs=sm.get_temp_watch_dirs())
    if not watch_dirs:
        _LOG.error("没有有效的监控目录")
        print("❌ 没有有效的监控目录")
        return

    poll_interval = cfg.get("paths", {}).get("watch_interval", 2.0)

    def callback(fp: str):
        _on_file_detected(eagle, fp)

    # 启动初始目录（config + cli 传参），后续临时目录通过 reconcile 同步
    configured_watch_dirs: set[str] = set()
    watchers = {}
    for d in watch_dirs:
        w = create_watcher(d, callback, poll_interval=poll_interval)
        w.start()
        watchers[d] = w
        configured_watch_dirs.add(d)
        print(f"👀 监控已启动：{d}")

    _last_cleanup_time = time.monotonic()
    _last_reconcile_time = time.monotonic()

    try:
        while True:
            time.sleep(5)
            # 处理重试队列
            with _retry_lock:
                pending = list(_retry_queue)
                _retry_queue.clear()
            for fp, attempt in pending:
                if os.path.exists(fp):
                    _on_file_detected(eagle, fp, attempt=attempt)
                else:
                    _LOG.warning("重试文件已不存在: %s", fp)

            now = time.monotonic()

            # 每 30 秒同步临时监控目录（面板添加/移除的目录）
            if now - _last_reconcile_time >= 30:
                _last_reconcile_time = now
                temp_dirs = get_state_manager().get_temp_watch_dirs()
                _reconcile_watchers(watchers, configured_watch_dirs, callback,
                                    poll_interval, temp_dirs=temp_dirs)

            # 每分钟清理一次 _processing_files 中已不存在的文件
            if now - _last_cleanup_time >= 60:
                _last_cleanup_time = now
                stale = [fp for fp in list(_processing_files) if not os.path.exists(fp)]
                for fp in stale:
                    _processing_files.discard(fp)
                if stale:
                    _LOG.info("清理了 %d 个不存在的文件记录", len(stale))
    except KeyboardInterrupt:
        print("\n👋 停止监控")
    finally:
        for w in watchers.values():
            w.stop()


def main():
    ensure_data_dir()
    cfg = load_config()

    eagle = create_eagle_api(cfg)

    if not eagle.ping():
        print("❌ Eagle 未运行，请先打开 Eagle")
        return

    print("👀 监控中，按 Ctrl+C 停止\n")
    try:
        run_watcher(eagle)
    except KeyboardInterrupt:
        print("\n👋 停止监控")


if __name__ == "__main__":
    main()