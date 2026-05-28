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
            "current_theme": None,
            "set_at": None,
            "inbox_notified_today": False,
        }

    def get_current_theme(self) -> Optional[str]:
        with self._lock:
            return self._state.get("current_theme")

    def set_current_theme(self, theme: Optional[str]):
        with self._lock:
            self._state["current_theme"] = theme
            self._state["set_at"] = datetime.now().isoformat()
            self._save()

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

    def get_all_state(self) -> dict:
        with self._lock:
            return dict(self._state)

    def set_state_from_server(self, theme: Optional[str]):
        with self._lock:
            self._state["current_theme"] = theme


_instance: Optional[StateManager] = None
_instance_lock = threading.Lock()


def get_state_manager() -> StateManager:
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = StateManager()
    return _instance