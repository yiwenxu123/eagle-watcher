"""常量、工具函数、缓存状态"""

import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Optional

from eagle_watcher.config import load_config
from eagle_watcher.services.state_manager import get_state_manager

_LOG = logging.getLogger("server")

HOST = "127.0.0.1"
REMOTE_PORT = 9800
PANEL_PORT = 9801

_HTML_PATH = Path(__file__).parent.parent / "pyui" / "panel.html"

# 状态缓存：避免每 5 秒重复查询 Eagle 全量数据
_status_cache: dict = {"data": None, "ts": 0}
_status_cache_lock = threading.Lock()
_STATUS_CACHE_TTL = 10  # 秒

# 可复用的线程池（避免每次 /api/status 创建新池）
_status_pool = None
_status_pool_lock = threading.Lock()

# Eagle 离线状态缓存：离线时降频 ping，避免每 5 秒连一次
_eagle_offline_since: float = 0  # 上次 ping 失败的时间戳
_EAGLE_OFFLINE_RETRY_INTERVAL = 30  # 离线后每 30 秒再试一次

# 幂等性缓存：有序字典 + 惰性过期，防止无限增长
_idempotency_cache: dict[str, float] = {}
_idempotency_lock = threading.Lock()
_IDEMPOTENCY_TTL = 30  # seconds
_IDEMPOTENCY_MAX_KEYS = 200  # 防止内存泄漏的硬上限

# 面板服务器 session token：启动时生成，注入前端页面，验证 API 请求
_panel_session_token: str = ""
_panel_token_initialized = False
_panel_token_created_at: float = 0  # Token 创建时间戳
_PANEL_TOKEN_TTL = 24 * 60 * 60  # Token 有效期（24 小时）


def _get_status_pool():
    global _status_pool
    if _status_pool is None:
        with _status_pool_lock:
            if _status_pool is None:
                from concurrent.futures import ThreadPoolExecutor
                _status_pool = ThreadPoolExecutor(max_workers=3)
    return _status_pool


def _invalidate_status_cache():
    """清除状态缓存，强制下次 /api/status 重新计算"""
    global _status_cache, _eagle_offline_since
    with _status_cache_lock:
        _status_cache["data"] = None
        _eagle_offline_since = 0  # 同时重置离线状态，允许下次请求重试


def _reset_eagle_offline():
    """重置 Eagle 离线状态缓存（用于测试 fixture）。"""
    global _eagle_offline_since
    _eagle_offline_since = 0


def _get_watch_dirs_from_config() -> list[dict]:
    """从配置 + state 读取所有监控目录信息（config 目录 + 临时目录）"""
    cfg = load_config()
    dirs = []
    downloads = cfg.get("paths", {}).get("downloads", "")
    if downloads:
        expanded = os.path.expanduser(downloads)
        dirs.append({
            "path": expanded,
            "exists": Path(expanded).is_dir(),
            "type": "downloads",
        })
    extra = cfg.get("paths", {}).get("extra_watch_dirs", [])
    if isinstance(extra, list):
        for d in extra:
            expanded = os.path.expanduser(d)
            dirs.append({
                "path": expanded,
                "exists": Path(expanded).is_dir(),
                "type": "extra",
            })
    sm = get_state_manager()
    for d in sm.get_temp_watch_dirs():
        expanded = os.path.expanduser(d)
        dirs.append({
            "path": expanded,
            "exists": Path(expanded).is_dir(),
            "type": "temp",
        })
    return dirs


def _ensure_panel_token():
    """确保 Token 有效，过期则自动生成新的"""
    global _panel_session_token, _panel_token_initialized, _panel_token_created_at

    current_time = time.time()

    # 检查是否需要生成新 Token
    if (not _panel_token_initialized or
            current_time - _panel_token_created_at > _PANEL_TOKEN_TTL):
        _panel_session_token = secrets.token_urlsafe(32)
        _panel_token_initialized = True
        _panel_token_created_at = current_time
        _LOG.info("面板 Token 已更新")

    return _panel_session_token
