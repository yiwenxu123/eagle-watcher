"""线程安全的运行时状态管理：JSON 文件持久化 + 写穿透"""

import json
import logging
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

_LOG = logging.getLogger("state")

from eagle_watcher._constants import MAX_PROCESSED_FILES, TRIM_KEEP_COUNT

# Keep local fallback for import resilience
_MAX_PROCESSED_FILES = 1000
_TRIM_KEEP_COUNT = 500

DATA_DIR = Path.home() / ".eagle-watcher"
STATE_PATH = DATA_DIR / "state.json"


class StateManager:

    def __init__(self):
        self._lock = threading.Lock()
        self._state: dict = self._load()

    def _load(self) -> dict:
        try:
            if STATE_PATH.exists():
                with open(STATE_PATH) as f:
                    return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            _LOG.warning("state.json 读取失败，使用默认值: %s", e)
        return self._default()

    def _save(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=str(DATA_DIR), suffix=".json.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._state, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, str(STATE_PATH))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    @staticmethod
    def _default() -> dict:
        return {
            "current_project": None,
            "current_theme": None,  # legacy field for backward compat
            "set_at": None,
            "inbox_notified_today": False,
            "last_processed": None,
            "watcher_running": False,
            "eagle_online": False,
            "temp_watch_dirs": [],
        }

    # ── 当前项目（取代旧 current_theme）──

    def get_current_project(self) -> Optional[str]:
        with self._lock:
            return self._state.get("current_project") or self._state.get("current_theme")

    def set_current_project(self, name: Optional[str]):
        with self._lock:
            state_copy = dict(self._state)
            state_copy["current_project"] = name
            state_copy["current_theme"] = name  # keep legacy field in sync
            state_copy["set_at"] = datetime.now().isoformat()
            self._state = state_copy
            self._save()

    # ── 向后兼容：旧 get/set_current_theme 别名 ──

    def get_current_theme(self) -> Optional[str]:
        return self.get_current_project()

    def set_current_theme(self, theme: Optional[str]):
        self.set_current_project(theme)

    # ── 每日通知 ──

    def get_inbox_notified_today(self) -> bool:
        with self._lock:
            return self._state.get("inbox_notified_today", False)

    def set_inbox_notified_today(self, value: bool):
        with self._lock:
            state_copy = dict(self._state)
            state_copy["inbox_notified_today"] = value
            self._state = state_copy
            self._save()

    def check_and_set_inbox_notified(self) -> bool:
        """
        原子化检查并设置 inbox_notified_today.
        返回 True 表示这是首次通知（调用方应发送通知），
        返回 False 表示今天已经通知过。
        """
        with self._lock:
            state_copy = dict(self._state)
            if state_copy.get("inbox_notified_today", False):
                return False
            state_copy["inbox_notified_today"] = True
            self._state = state_copy
            self._save()
            return True

    def reset_daily_flags(self):
        with self._lock:
            if not self._state.get("inbox_notified_today"):
                return
            state_copy = dict(self._state)
            state_copy["inbox_notified_today"] = False
            self._state = state_copy
            self._save()
            _LOG.info("每日标志已重置")

    # ── 状态查询 ──

    def get_all_state(self) -> dict:
        with self._lock:
            return dict(self._state)

    def set_state_from_server(self, project: Optional[str]):
        with self._lock:
            state_copy = dict(self._state)
            state_copy["current_project"] = project
            state_copy["current_theme"] = project
            self._state = state_copy
            self._save()

    # ── 已处理文件 ──

    def get_processed_files(self) -> set[str]:
        with self._lock:
            return set(self._state.get("processed_files", []))

    def is_file_processed(self, file_path: str) -> bool:
        """只读检查文件是否已处理，不修改状态。"""
        with self._lock:
            processed = set(self._state.get("processed_files", []))
            try:
                st = Path(file_path).stat()
                key = f"{st.st_ino}:{st.st_size}"
            except OSError:
                return False
            return key in processed

    def set_processed_files(self, files: set[str]):
        with self._lock:
            state_copy = dict(self._state)
            state_copy["processed_files"] = list(files)
            self._state = state_copy
            self._save()

    def mark_file_processed(self, file_path: str) -> bool:
        """原子化检查并标记文件为已处理。
        返回 True 表示该文件首次被标记（即需要处理），
        返回 False 表示该文件已被标记过（跳过处理）。
        """
        with self._lock:
            processed = set(self._state.get("processed_files", []))
            try:
                st = Path(file_path).stat()
                key = f"{st.st_ino}:{st.st_size}"
            except OSError:
                return True  # 无法读取文件信息，返回 True 让调用方重试
            if key in processed:
                return False  # 已处理过
            processed.add(key)
            if len(processed) > MAX_PROCESSED_FILES:
                processed = set(sorted(processed)[-TRIM_KEEP_COUNT:])
            state_copy = dict(self._state)
            state_copy["processed_files"] = list(processed)
            self._state = state_copy
            self._save()
            return True

    # ── 运行时状态 ──

    def get_last_processed(self) -> Optional[dict]:
        with self._lock:
            result = self._state.get("last_processed")
            return dict(result) if result else None

    def set_last_processed(self, info: dict):
        with self._lock:
            state_copy = dict(self._state)
            state_copy["last_processed"] = info
            self._state = state_copy
            self._save()

    def get_watcher_running(self) -> bool:
        with self._lock:
            return self._state.get("watcher_running", False)

    def set_watcher_running(self, running: bool):
        with self._lock:
            state_copy = dict(self._state)
            state_copy["watcher_running"] = running
            self._state = state_copy
            self._save()

    def get_eagle_online(self) -> bool:
        with self._lock:
            return self._state.get("eagle_online", False)

    def set_eagle_online(self, online: bool):
        with self._lock:
            state_copy = dict(self._state)
            state_copy["eagle_online"] = online
            self._state = state_copy
            self._save()


    # ── 临时监控目录 ──

    def get_temp_watch_dirs(self) -> list[str]:
        with self._lock:
            return list(self._state.get("temp_watch_dirs", []))

    def set_temp_watch_dirs(self, dirs: list[str]):
        with self._lock:
            state_copy = dict(self._state)
            state_copy["temp_watch_dirs"] = list(dirs)
            self._state = state_copy
            self._save()

    def add_temp_watch_dir(self, path: str) -> bool:
        """添加临时监控目录，已存在则跳过。返回 True 表示新增。"""
        with self._lock:
            dirs = list(self._state.get("temp_watch_dirs", []))
            if path in dirs:
                return False
            dirs.append(path)
            state_copy = dict(self._state)
            state_copy["temp_watch_dirs"] = dirs
            self._state = state_copy
            self._save()
            return True

    def remove_temp_watch_dir(self, path: str) -> bool:
        """移除临时监控目录。返回 True 表示确实删除了。"""
        with self._lock:
            dirs = list(self._state.get("temp_watch_dirs", []))
            if path not in dirs:
                return False
            dirs.remove(path)
            state_copy = dict(self._state)
            state_copy["temp_watch_dirs"] = dirs
            self._state = state_copy
            self._save()
            return True


_instance: Optional[StateManager] = None
_instance_lock = threading.Lock()


def get_state_manager() -> StateManager:
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = StateManager()
    return _instance