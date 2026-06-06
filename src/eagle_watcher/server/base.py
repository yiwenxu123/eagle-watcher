"""BaseHandler — 共享基类（JSON/HTML 响应、CORS、EagleAPI 创建）"""

import json
import logging
from http.server import BaseHTTPRequestHandler
from typing import Optional

from eagle_watcher.config import load_config
from eagle_watcher.eagle_api import EagleAPI, create_eagle_api
from eagle_watcher.server._common import _ensure_panel_token

_LOG = logging.getLogger("server")


class BaseHandler(BaseHTTPRequestHandler):
    """共享基类：JSON/HTML 响应、CORS、EagleAPI 创建"""

    def log_message(self, format, *args):
        # 过滤路径中的 token 参数，防止日志泄露
        safe_path = self.path.split("?")[0] if "token=" in self.path else self.path
        _LOG.info("HTTP %s %s - %s", self.command, safe_path, args[0] if args else '')

    def _send(self, code: int, body, content_type="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        if isinstance(body, (dict, list)):
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            data = body.encode("utf-8")
        else:
            data = body
        self.wfile.write(data)

    def _send_json(self, code: int, data: dict):
        self._send(code, data, "application/json")

    def _send_html(self, code: int, html: str):
        # 禁止缓存，确保 WKWebView 始终拿到最新面板
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "null")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        token = _ensure_panel_token()
        injected = html.replace(
            "</head>",
            f'<meta name="session-token" content="{token}">\n</head>'
        )
        data = injected.encode("utf-8") if isinstance(injected, str) else injected
        self.wfile.write(data)

    def _eagle(self) -> Optional[EagleAPI]:
        """创建 EagleAPI 连接，离线时发送 503 并返回 None。"""
        cfg = load_config()
        api = create_eagle_api(cfg)
        if not api.ping():
            self._send_json(503, {"error": "Eagle 未运行"})
            return None
        return api

    def _eagle_safe(self) -> Optional[EagleAPI]:
        """创建 EagleAPI 连接，离线时返回 None（不发送错误响应）。"""
        cfg = load_config()
        api = create_eagle_api(cfg)
        return api if api.ping() else None

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
