"""Thin wrapper — re-exports PanelHandler and start_panel_server from eagle_watcher.server"""
from eagle_watcher.server import (
    BaseHandler,
    PanelHandler as Handler,
    RemoteHandler,
    _status_cache,
    _STATUS_CACHE_TTL,
    _HTML_PATH,
    start_panel_server as start_server,
    start_remote_server,
)

__all__ = [
    "Handler",
    "BaseHandler",
    "RemoteHandler",
    "start_server",
    "start_remote_server",
    "_status_cache",
    "_STATUS_CACHE_TTL",
    "_HTML_PATH",
]
