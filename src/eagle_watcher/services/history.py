"""操作历史日志：JSONL 格式追加写入"""

import json
import logging
from datetime import datetime
from pathlib import Path

_LOG = logging.getLogger("history")

DATA_DIR = Path.home() / ".eagle-watcher"
HISTORY_PATH = DATA_DIR / "history.jsonl"

MAX_ENTRIES = 2000  # 超过此数量时截断旧条目


def append(entry: dict):
    """追加一条操作记录"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    entry.setdefault("time", datetime.now().isoformat())
    try:
        with open(HISTORY_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        _LOG.warning("写入历史记录失败: %s", e)
    # 每 100 次调用自动清理过旧记录
    append.call_count = getattr(append, "call_count", 0) + 1
    if append.call_count % 100 == 0:
        try:
            cleanup()
        except Exception:
            pass


def recent(limit: int = 50) -> list[dict]:
    """读取最近 N 条操作记录"""
    if not HISTORY_PATH.exists():
        return []
    try:
        lines = HISTORY_PATH.read_text(encoding="utf-8").strip().split("\n")
        result = []
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                result.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if len(result) >= limit:
                break
        return result
    except OSError as e:
        _LOG.warning("读取历史记录失败: %s", e)
        return []


def cleanup(max_entries: int = MAX_ENTRIES):
    """截断过旧的历史记录"""
    if not HISTORY_PATH.exists():
        return
    try:
        lines = HISTORY_PATH.read_text(encoding="utf-8").strip().split("\n")
        if len(lines) <= max_entries:
            return
        keep = lines[-max_entries:]
        HISTORY_PATH.write_text("\n".join(keep) + "\n", encoding="utf-8")
        _LOG.info("历史记录截断: %d → %d 条", len(lines), len(keep))
    except OSError as e:
        _LOG.warning("截断历史记录失败: %s", e)


def clear():
    """清空所有操作历史"""
    try:
        if HISTORY_PATH.exists():
            HISTORY_PATH.unlink()
            _LOG.info("历史记录已清空")
    except OSError as e:
        _LOG.warning("清空历史记录失败: %s", e)
