"""操作历史日志：JSONL 格式追加写入，支持高效倒序读取"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path

_LOG = logging.getLogger("history")

DATA_DIR = Path.home() / ".eagle-watcher"
HISTORY_PATH = DATA_DIR / "history.jsonl"

MAX_ENTRIES = 2000  # 超过此数量时截断旧条目
MAX_READ_SIZE = 100 * 1024  # 最多读取 100KB（约 500 条记录）


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


def _read_last_n_lines(file_path: Path, n: int) -> list[str]:
    """高效读取文件最后 N 行（倒序读取）

    使用 seek 从文件末尾开始读取，避免读取整个文件
    """
    if not file_path.exists():
        return []

    try:
        file_size = file_path.stat().st_size
        if file_size == 0:
            return []

        # 限制读取大小
        read_size = min(file_size, MAX_READ_SIZE)

        with open(file_path, "rb") as f:
            # 移动到文件末尾前 read_size 字节
            f.seek(max(0, file_size - read_size))
            # 读取指定大小的内容
            content = f.read(read_size).decode("utf-8", errors="ignore")

        # 分割成行，过滤空行
        lines = [line.strip() for line in content.split("\n") if line.strip()]

        # 返回最后 n 行（倒序）
        return lines[-n:] if len(lines) > n else lines

    except OSError as e:
        _LOG.warning("读取历史记录失败: %s", e)
        return []


def recent(limit: int = 50) -> list[dict]:
    """读取最近 N 条操作记录（优化版本）"""
    if not HISTORY_PATH.exists():
        return []

    try:
        # 使用优化的读取方法
        lines = _read_last_n_lines(HISTORY_PATH, limit)

        # 解析 JSON（从后往前，因为 lines 已经是倒序）
        result = []
        for line in reversed(lines):
            try:
                result.append(json.loads(line))
            except json.JSONDecodeError:
                continue

        return result

    except Exception as e:
        _LOG.warning("读取历史记录失败: %s", e)
        return []


def cleanup(max_entries: int = MAX_ENTRIES):
    """截断过旧的历史记录"""
    if not HISTORY_PATH.exists():
        return
    try:
        # 使用优化的读取方法获取最后 max_entries 条
        lines = _read_last_n_lines(HISTORY_PATH, max_entries)

        # 检查是否需要截断
        total_lines = 0
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            total_lines = sum(1 for _ in f)

        if total_lines <= max_entries:
            return

        # 写入保留的记录
        HISTORY_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        _LOG.info("历史记录截断: %d → %d 条", total_lines, len(lines))
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
