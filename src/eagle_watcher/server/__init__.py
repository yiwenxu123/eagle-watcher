"""
HTTP Server 包 — 统一服务器，支持远程 Agent API + HUD 面板

模块结构：
  _common.py   — 常量、工具函数、缓存状态
  base.py      — BaseHandler 基类
  remote.py    — RemoteHandler（远程 Agent API）
  panel.py     — PanelHandler（HUD 面板 API）
"""

from eagle_watcher.server._common import (
    HOST, REMOTE_PORT, PANEL_PORT, _HTML_PATH,
    _status_cache, _status_cache_lock, _STATUS_CACHE_TTL,
    _eagle_offline_since, _EAGLE_OFFLINE_RETRY_INTERVAL,
    _idempotency_cache, _idempotency_lock, _IDEMPOTENCY_TTL, _IDEMPOTENCY_MAX_KEYS,
    _panel_session_token, _panel_token_initialized, _panel_token_created_at, _PANEL_TOKEN_TTL,
    _get_status_pool, _invalidate_status_cache,
    _get_watch_dirs_from_config, _ensure_panel_token,
    _reset_eagle_offline,
)
from eagle_watcher.server.base import BaseHandler
from eagle_watcher.server.remote import RemoteHandler
from eagle_watcher.server.panel import PanelHandler

__all__ = [
    "HOST", "REMOTE_PORT", "PANEL_PORT",
    "BaseHandler", "RemoteHandler", "PanelHandler",
    "start_remote_server", "start_panel_server", "start_server",
]


def start_remote_server(host: str = HOST, port: int = REMOTE_PORT):
    """启动远程 Agent API 服务器（端口 9800）。"""
    from http.server import HTTPServer
    try:
        from eagle_watcher._logging import setup_logging
        setup_logging("eagle-server")
    except Exception:
        pass

    HTTPServer.allow_reuse_address = True
    server = HTTPServer((host, port), RemoteHandler)
    print(f"🌐  HTTP Server 已启动：http://{host}:{port}")
    print(f"    POST /import  — 远程 Agent 导入素材")
    print(f"    GET  /ping    — 健康检查（无需认证）")
    print(f"    GET  /status  — 状态查询")
    # 检查 API Key 是否已配置
    from eagle_watcher.config import load_config
    _cfg = load_config()
    _server_key = _cfg.get("server", {}).get("api_key", "")
    if _server_key:
        print(f"    🔑 API Key 认证已启用")
    else:
        print(f"    ⚠️  API Key 未配置（请求无需认证）")
    print(f"    按 Ctrl+C 停止\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 HTTP Server 已停止")
        server.server_close()


def start_panel_server(host: str = HOST, port: int = PANEL_PORT):
    """启动 HUD 面板 API 服务器（端口 9801）。"""
    import logging
    from http.server import HTTPServer
    _LOG = logging.getLogger("server")
    HTTPServer.allow_reuse_address = True
    server = HTTPServer((host, port), PanelHandler)
    _LOG.info("HTTP 服务器已启动: http://%s:%s", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


# 向后兼容别名
start_server = start_remote_server

if __name__ == "__main__":
    from eagle_watcher.config import ensure_data_dir
    ensure_data_dir()
    start_server()
