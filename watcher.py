"""
Downloads 文件夹监控

监听 Downloads 目录的新增文件 -> 决策引擎 -> 自动入库 Eagle 或进通用箱。
优先使用 watchdog 事件驱动，不可用时降级为轮询方式。
"""

import logging
import os
import time
from typing import Optional
from pathlib import Path

from config import load_config, ensure_data_dir
from services.state_manager import get_state_manager
from eagle_api import EagleAPI
from analyzer import decide
from ai_tagger import analyze_image
from notifier import notify

# 尝试导入 watchdog
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileCreatedEvent
    HAS_WATCHDOG = True
except ImportError:
    HAS_WATCHDOG = False
    _LOG = logging.getLogger("watcher")
    _LOG.warning("watchdog 未安装，将使用轮询方式监控。安装命令：pip install watchdog")


def _wait_for_file(file_path: str, timeout: int = 30) -> bool:
    """等待文件写入完成，避免处理正在写入的文件"""
    start = time.time()
    last_size = -1

    while time.time() - start < timeout:
        try:
            current_size = os.path.getsize(file_path)
            if current_size == last_size and current_size > 0:
                return True  # 文件大小稳定，写入完成
            last_size = current_size
        except OSError:
            pass
        time.sleep(0.5)

    return False  # 超时


# watchdog 事件处理器
if HAS_WATCHDOG:
    class DownloadHandler(FileSystemEventHandler):
        """监控 Downloads 文件夹的事件处理器"""

        def __init__(self, eagle: EagleAPI):
            super().__init__()
            self.eagle = eagle
            self._processing = set()

        def on_created(self, event):
            if event.is_directory:
                return

            file_path = event.src_path
            filename = Path(file_path).name

            if filename.startswith("."):
                return
            if filename.endswith((".crdownload", ".tmp", ".part", ".download")):
                return

            if file_path in self._processing:
                return
            self._processing.add(file_path)

            try:
                if not _wait_for_file(file_path):
                    _LOG.warning(f"文件写入超时，跳过：{file_path}")
                    return

                _LOG.info(f"watchdog 检测到新文件：{filename}")
                print(f"\n📥 检测到新文件：{filename}")
                _process_file(self.eagle, file_path)
            except Exception as e:
                _LOG.error(f"处理文件失败：{file_path} — {e}")
                print(f"  ⚠️  处理失败：{filename} — {e}")
            finally:
                self._processing.discard(file_path)

_LOG = logging.getLogger("watcher")


def _get_downloaded_files(downloads_dir: str, known: set) -> list[str]:
    new_files = []
    try:
        for entry in os.scandir(downloads_dir):
            if entry.is_file():
                fpath = entry.path
                if fpath not in known:
                    name = entry.name
                    if name.startswith("."):
                        continue
                    if name.endswith((".crdownload", ".tmp", ".part", ".download")):
                        continue
                    new_files.append(fpath)
    except PermissionError:
        pass
    return new_files


def _check_result(result: dict, filename: str, theme: str, tags: list[str]):
    if result.get("status") == "success":
        theme_label = theme or "通用箱"
        tag_str = ", ".join(tags)
        print(f"  ✅ {filename} → {theme_label} ｜ {tag_str}")
        sm = get_state_manager()
        if not theme and not sm.get_inbox_notified_today():
            notify("素材管家", f"📦 通用箱有新素材：{filename}")
            sm.set_inbox_notified_today(True)
    else:
        print(f"  ❌ 入库失败：{filename} — {result}")


def _process_file(eagle: EagleAPI, file_path: str):
    filename = Path(file_path).name
    decision = decide(filename)

    folder_id = None
    if decision["folder"]:
        folder_id = eagle.get_or_create_folder(decision["folder"])

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
                tags=ai_tags,
                folder_id=folder_id,
            )
            _check_result(result, filename, decision.get("theme", ""), ai_tags)
        else:
            print(f"  ⚠️ AI 分析失败，暂存到通用箱")
            result = eagle.add_from_path(
                file_path,
                tags=["待AI识别"],
                folder_id=folder_id or eagle.get_or_create_folder("_通用箱"),
            )
            _check_result(result, filename, decision.get("theme", ""), ["待AI识别"])
        return

    tags = decision["tags"]
    result = eagle.add_from_path(
        file_path,
        name=Path(file_path).stem,
        tags=tags,
        folder_id=folder_id,
    )
    _check_result(result, filename, decision.get("theme", ""), tags)


def run_once(eagle: EagleAPI, known_files: set) -> set:
    cfg = load_config()
    downloads_dir = cfg["paths"]["downloads"]

    new_files = _get_downloaded_files(downloads_dir, known_files)
    if not new_files:
        return known_files

    _LOG.info(f"发现 {len(new_files)} 个新文件")
    print(f"\n📥 发现 {len(new_files)} 个新文件")

    for fpath in new_files:
        known_files.add(fpath)
        try:
            if not _wait_for_file(fpath):
                _LOG.warning(f"文件写入超时，跳过：{fpath}")
                print(f"  ⚠️  文件写入超时，跳过：{Path(fpath).name}")
                continue
            _process_file(eagle, fpath)
        except Exception as e:
            print(f"  ⚠️  处理失败：{fpath} — {e}")

    return known_files


def run_watcher_with_watchdog(eagle: EagleAPI):
    cfg = load_config()
    downloads_dir = cfg["paths"]["downloads"]

    if not os.path.isdir(downloads_dir):
        _LOG.error(f"下载目录不存在：{downloads_dir}")
        return

    handler = DownloadHandler(eagle)
    observer = Observer()
    observer.schedule(handler, downloads_dir, recursive=False)
    observer.start()
    _LOG.info(f"watchdog 监控已启动：{downloads_dir}")
    print(f"👀 watchdog 监控已启动：{downloads_dir}")

    try:
        while observer.is_alive():
            observer.join(timeout=1)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()
        _LOG.info("watchdog 监控已停止")


def run_watcher(eagle: Optional[EagleAPI] = None):
    ensure_data_dir()
    cfg = load_config()

    if eagle is None:
        eagle = EagleAPI(
            base_url=cfg["eagle"]["host"],
            token=cfg["eagle"]["token"],
        )

    if HAS_WATCHDOG:
        _LOG.info("使用 watchdog 事件驱动监控")
        run_watcher_with_watchdog(eagle)
        return

    _LOG.info("使用轮询方式监控")
    interval = cfg["paths"].get("watch_interval", 2.0)

    downloads_dir = cfg["paths"]["downloads"]
    known_files = set()
    if os.path.isdir(downloads_dir):
        for entry in os.scandir(downloads_dir):
            if entry.is_file() and not entry.name.startswith("."):
                known_files.add(entry.path)

    while True:
        try:
            known_files = run_once(eagle, known_files)
        except Exception:
            pass
        time.sleep(interval)


def main():
    ensure_data_dir()
    cfg = load_config()

    eagle = EagleAPI(
        base_url=cfg["eagle"]["host"],
        token=cfg["eagle"]["token"],
    )

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
