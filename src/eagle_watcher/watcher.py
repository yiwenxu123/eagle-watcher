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
            item_full_name = f"{item.get('name', '')}.{item.get('ext', '')}"
            if item_full_name == file_name:
                item_size = item.get("size", 0)
                if item_size and abs(item_size - file_size) < 1024:
                    _LOG.info("跳过已存在的文件: %s (同名+同大小)", file_name)
                    return
    except Exception:
        pass  # dedup failure is non-blocking

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
        return  # 已处理过，不加入 _processing_files 避免内存泄漏
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


def run_watcher(eagle: Optional[EagleAPI] = None):
    get_state_manager().set_watcher_running(True)
    ensure_data_dir()
    cfg = load_config()

    if eagle is None:
        eagle = create_eagle_api(cfg)

    downloads_dir = cfg["paths"]["downloads"]
    if not Path(downloads_dir).is_dir():
        _LOG.error("下载目录不存在: %s", downloads_dir)
        print(f"❌ 下载目录不存在: {downloads_dir}")
        return

    def callback(fp: str):
        _on_file_detected(eagle, fp)

    poll_interval = cfg.get("paths", {}).get("watch_interval", 1.0)
    watcher = create_watcher(downloads_dir, callback, poll_interval=poll_interval)
    watcher.start()

    print(f"👀 监控已启动：{downloads_dir}")

    _last_cleanup_time = time.monotonic()

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

            # 每分钟清理一次 _processing_files 中已不存在的文件
            now = time.monotonic()
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
        watcher.stop()


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