import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

_LOG = logging.getLogger("state")

DATA_DIR = Path.home() / ".eagle-watcher"
STATE_PATH = DATA_DIR / "state.json"


class StateManager:

    def __init__(self):
        self._lock = threading.RLock()
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
        with open(STATE_PATH, "w") as f:
            json.dump(self._state, f, ensure_ascii=False, indent=2)

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
        }

    # ── 当前项目（取代旧 current_theme）──

    def get_current_project(self) -> Optional[str]:
        with self._lock:
            return self._state.get("current_project") or self._state.get("current_theme")

    def set_current_project(self, name: Optional[str]):
        with self._lock:
            self._state["current_project"] = name
            self._state["current_theme"] = name  # keep legacy field in sync
            self._state["set_at"] = datetime.now().isoformat()
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
            self._state["inbox_notified_today"] = value
            self._save()

    def reset_daily_flags(self):
        with self._lock:
            if self._state.get("inbox_notified_today"):
                self._state["inbox_notified_today"] = False
                self._save()
                _LOG.info("每日标志已重置")

    # ── 状态查询 ──

    def get_all_state(self) -> dict:
        with self._lock:
            return dict(self._state)

    def set_state_from_server(self, project: Optional[str]):
        with self._lock:
            self._state["current_project"] = project
            self._state["current_theme"] = project

    # ── 已处理文件 ──

    def get_processed_files(self) -> set[str]:
        with self._lock:
            return set(self._state.get("processed_files", []))

    def set_processed_files(self, files: set[str]):
        with self._lock:
            self._state["processed_files"] = list(files)
            self._save()

    def mark_file_processed(self, file_path: str) -> bool:
        """原子化检查并标记文件为已处理。

        返回 True 表示该文件首次被标记（即需要处理），
        返回 False 表示该文件已被标记过（跳过处理）。
        此方法在锁内完成 check-and-set，避免 TOCTOU 竞态。
        """
        from pathlib import Path
        with self._lock:
            processed = set(self._state.get("processed_files", []))
            try:
                st = Path(file_path).stat()
                key = f"{st.st_ino}:{st.st_size}"
            except OSError:
                return False  # 无法读取文件信息，保守返回"已处理"
            if key in processed:
                return False  # 已处理过
            processed.add(key)
            if len(processed) > 1000:
                processed = set(list(processed)[-500:])
            self._state["processed_files"] = list(processed)
            self._save()
            return True

    # ── 运行时状态 ──

    def get_last_processed(self) -> Optional[dict]:
        with self._lock:
            result = self._state.get("last_processed")
            return dict(result) if result else None

    def set_last_processed(self, info: dict):
        with self._lock:
            self._state["last_processed"] = info
            self._save()

    def get_watcher_running(self) -> bool:
        with self._lock:
            return self._state.get("watcher_running", False)

    def set_watcher_running(self, running: bool):
        with self._lock:
            self._state["watcher_running"] = running
            self._save()

    def get_eagle_online(self) -> bool:
        with self._lock:
            return self._state.get("eagle_online", False)

    def set_eagle_online(self, online: bool):
        with self._lock:
            self._state["eagle_online"] = online
            self._save()


_instance: Optional[StateManager] = None
_instance_lock = threading.Lock()


def get_state_manager() -> StateManager:
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = StateManager()
    return _instance