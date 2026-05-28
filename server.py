"""
HTTP Server — 远程 Agent 调用入口

Hermes（腾讯云）或 OpenClaw（Windows）通过此服务远程导入素材到 Eagle。

调用方式：
  POST http://mac:9800/import
  Content-Type: application/json
  {
    "file_url": "https://example.com/image.jpg",
    "project": "武安侯",
    "tags": ["白起", "战国"],
    "folder": "人物"
  }

远程 Agent 通过 SSH 隧道连接：
  ssh -L 9800:localhost:9800 mac
"""

import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from config import load_config, ensure_data_dir, set_current_theme
from eagle_api import EagleAPI

HOST = "127.0.0.1"
PORT = 9800


class ImportHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        # 保持静默，不输出请求日志到 stderr
        pass

    def _send_json(self, status_code: int, body: dict):
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(body, ensure_ascii=False).encode("utf-8"))

    def _get_eagle(self) -> Optional[EagleAPI]:
        cfg = load_config()
        eagle = EagleAPI(
            base_url=cfg["eagle"]["host"],
            token=cfg["eagle"]["token"],
        )
        if not eagle.ping():
            self._send_json(503, {"status": "error", "message": "Eagle 未运行"})
            return None
        return eagle

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/ping":
            cfg = load_config()
            eagle = EagleAPI(
                base_url=cfg["eagle"]["host"],
                token=cfg["eagle"]["token"],
            )
            online = eagle.ping()
            self._send_json(200, {"status": "ok", "eagle_online": online})

        elif path == "/status":
            cfg = load_config()
            eagle = EagleAPI(
                base_url=cfg["eagle"]["host"],
                token=cfg["eagle"]["token"],
            )
            from config import get_current_theme, get_theme_names
            info = {
                "theme": get_current_theme(),
                "themes": get_theme_names(),
                "eagle_online": eagle.ping(),
            }
            if eagle.ping():
                try:
                    folders = eagle.list_folders()
                    info["folders"] = [f["name"] for f in folders]
                except Exception:
                    info["folders"] = []
            self._send_json(200, {"status": "ok", "data": info})

        else:
            self._send_json(404, {"status": "error", "message": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path != "/import":
            self._send_json(404, {"status": "error", "message": "not found"})
            return

        content_len = int(self.headers.get("Content-Length", 0))
        if content_len == 0:
            self._send_json(400, {"status": "error", "message": "empty body"})
            return

        try:
            body = json.loads(self.rfile.read(content_len))
        except json.JSONDecodeError:
            self._send_json(400, {"status": "error", "message": "invalid JSON"})
            return

        file_url = body.get("file_url", "")
        file_path = body.get("file_path", "")
        project = body.get("project")
        tags = body.get("tags", [])
        folder = body.get("folder")

        if not file_url and not file_path:
            self._send_json(400, {"status": "error", "message": "file_url or file_path is required"})
            return

        eagle = self._get_eagle()
        if not eagle:
            return

        # 如果指定了 project，作为提示设置当前主题（不改变已有主题）
        if project:
            from config import get_current_theme
            current = get_current_theme()
            if not current:
                set_current_theme(project)

        target_folder = folder or project
        folder_id = None
        if target_folder:
            folder_id = eagle.get_or_create_folder(target_folder)

        try:
            if file_path:
                if not Path(file_path).exists():
                    self._send_json(400, {"status": "error", "message": f"file not found: {file_path}"})
                    return
                result = eagle.add_from_path(
                    file_path,
                    tags=tags,
                    folder_id=folder_id,
                )
            else:
                result = eagle.add_from_url(
                    file_url,
                    tags=tags,
                    folder_id=folder_id,
                )
        except Exception as e:
            self._send_json(500, {"status": "error", "message": str(e)})
            return

        if result.get("status") == "success":
            self._send_json(200, {
                "status": "success",
                "message": f"已入库：{target_folder or '未分类'}",
                "data": {"tags": tags, "folder": target_folder},
            })
        else:
            self._send_json(500, {
                "status": "error",
                "message": str(result),
            })


def start_server(host: str = HOST, port: int = PORT):
    server = HTTPServer((host, port), ImportHandler)
    print(f"🌐  HTTP Server 已启动：http://{host}:{port}")
    print(f"    POST /import  — 远程 Agent 导入素材")
    print(f"    GET  /ping    — 健康检查")
    print(f"    GET  /status  — 状态查询")
    print(f"    按 Ctrl+C 停止\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 HTTP Server 已停止")
        server.server_close()


if __name__ == "__main__":
    ensure_data_dir()
    start_server()
