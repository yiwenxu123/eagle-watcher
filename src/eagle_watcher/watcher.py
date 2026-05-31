"""
Downloads 文件夹监控

监听 Downloads 目录的新增文件 -> 决策引擎 -> 自动入库 Eagle。
使用分层可回退文件监控（FSEvents → inode 轮询）。
"""

import logging
import time
from pathlib import Path
from typing import Optional

from eagle_watcher.config import load_config, ensure_data_dir
from eagle_watcher.services.state_manager import get_state_manager
from eagle_watcher.services.file_watcher import create_watcher
from eagle_watcher.eagle_api import EagleAPI, create_eagle_api
from eagle_watcher.analyzer import decide
from eagle_watcher.ai_tagger import analyze_image
from eagle_watcher.notifier import notify

_LOG = logging.getLogger("watcher")
_processing_files: set[str] = set()


def _is_processed(file_path: str) -> bool:
    """检查文件是否已处理（原子操作，线程安全）"""
    state = get_state_manager()
    already_marked = not state.mark_file_processed(file_path)
    if already_marked:
        _LOG.info("跳过已处理的文件: %s", Path(file_path).name)
    return already_marked


def _check_result(result: dict, filename: str, theme: str, tags: list[str]):
    if result.get("status") == "success":
        theme_label = theme or "通用箱"
        tag_str = ", ".join(tags)
        print(f"  ✅ {filename} → {theme_label} ｜ {tag_str}")
        if theme:
            # Direct import with theme - notify if enabled
            cfg = load_config()
            if cfg.get("notifications", {}).get("import_success", False):
                notify("素材管家", f"✅ {filename} → {theme_label}")
        else:
            # Inbox item - notify once per day
            sm = get_state_manager()
            if not sm.get_inbox_notified_today():
                notify("素材管家", f"📦 通用箱有新素材：{filename}")
                sm.set_inbox_notified_today(True)
    else:
        print(f"  ❌ 入库失败：{filename} — {result}")


def _process_file(eagle: EagleAPI, file_path: str):
    filename = Path(file_path).name
    decision = decide(filename)

    folder_id = None
    if decision.get("folder"):
        folder_id = eagle.get_or_create_folder(decision["folder"])

    tags = list(decision.get("tags", []))

    if decision["action"] == "ai_analyze":
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
            _check_result(result, filename, decision.get("theme", ""), ai_tags)
        else:
            print(f"  ⚠️ AI 分析失败，暂存到通用箱")
            result = eagle.add_from_path(
                file_path,
                tags=tags or ["待分类"],
                folder_id=folder_id,
            )
            _check_result(result, filename, "", tags or ["待分类"])
        return

    if not tags:
        tags = ["待分类"]

    result = eagle.add_from_path(
        file_path,
        name=Path(file_path).stem,
        tags=tags,
        folder_id=folder_id,
    )
    _check_result(result, filename, decision.get("theme", ""), tags)


def _on_file_detected(eagle: EagleAPI, file_path: str):
    global _processing_files
    if file_path in _processing_files:
        return
    if _is_processed(file_path):
        _processing_files.add(file_path)
        return
    _processing_files.add(file_path)

    filename = Path(file_path).name
    _LOG.info("检测到新文件：%s", filename)
    print(f"\n📥 检测到新文件：{filename}")
    try:
        _process_file(eagle, file_path)
    except Exception as e:
        _LOG.error("处理文件失败：%s — %s", file_path, e)
        print(f"  ⚠️  处理失败：{filename} — {e}")
    finally:
        _processing_files.discard(file_path)


def run_watcher(eagle: Optional[EagleAPI] = None):
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

    watcher = create_watcher(downloads_dir, callback)
    watcher.start()

    print(f"👀 监控已启动：{downloads_dir}")

    try:
        while True:
            time.sleep(1)
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