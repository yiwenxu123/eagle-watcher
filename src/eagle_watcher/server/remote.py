"""RemoteHandler — 远程 Agent API（端口 9800）"""

import json
import logging
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from eagle_watcher.config import load_config
from eagle_watcher.eagle_api import EagleAPI, create_eagle_api
from eagle_watcher.analyzer import decide
from eagle_watcher.server._common import _invalidate_status_cache
from eagle_watcher.server.base import BaseHandler

_LOG = logging.getLogger("server")


class RemoteHandler(BaseHandler):
    """远程 Agent API：GET /ping, GET /status, POST /import

    认证方式（可选）：
      在 config.yaml 中设置 server.api_key，请求时通过 X-API-Key header 传入。
      未配置 api_key 时向后兼容，允许所有请求。
      /ping 端点始终开放（无需认证）。
    """

    def _check_api_key(self) -> bool:
        """验证 X-API-Key header 与配置中的 server.api_key 匹配。
        如果未配置 api_key，则允许所有请求（向后兼容）。
        """
        cfg = load_config()
        expected = cfg.get("server", {}).get("api_key", "")
        if not expected:
            return True  # 未配置 api_key：向后兼容，允许所有请求
        actual = self.headers.get("X-API-Key", "")
        if actual == expected:
            return True
        self._send_json(401, {"status": "error", "message": "invalid or missing API key"})
        return False

    def _get_eagle(self) -> Optional[EagleAPI]:
        """创建 EagleAPI（使用 status/message 错误格式，保持向后兼容）。"""
        cfg = load_config()
        eagle = create_eagle_api(cfg)
        if not eagle.ping():
            self._send_json(503, {"status": "error", "message": "Eagle 未运行"})
            return None
        return eagle

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        # /ping 不验证 API Key（健康检查端点）
        if path == "/ping":
            cfg = load_config()
            eagle = create_eagle_api(cfg)
            online = eagle.ping()
            self._send_json(200, {"status": "ok", "eagle_online": online})
            return

        # 其他 GET 端点需要 API Key 认证
        if not self._check_api_key():
            return

        if path == "/status":
            cfg = load_config()
            eagle = create_eagle_api(cfg)
            from eagle_watcher.config import get_current_project, get_project_names
            info = {
                "project": get_current_project(),
                "projects": get_project_names(),
                "eagle_online": eagle.ping(),
            }
            if eagle.ping():
                try:
                    folders = eagle.list_folders()
                    info["folders"] = [f["name"] for f in folders]
                except Exception as e:
                    _LOG.warning("Failed to fetch folder list: %s", e)
                    info["folders"] = []
            self._send_json(200, {"status": "ok", "data": info})

        else:
            self._send_json(404, {"status": "error", "message": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if not self._check_api_key():
            return

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

        # 当未指定 project 时，通过 decide() 决策引擎自动判断
        if not project:
            filename = Path(file_path).name if file_path else Path(file_url).name
            decision = decide(filename)
            if decision.get("theme"):
                project = decision["theme"]
                tags = list(dict.fromkeys(decision.get("tags", []) + tags))
                folder = folder or decision.get("folder", project)

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
            _LOG.exception("导入失败")
            self._send_json(500, {"status": "error", "message": str(e)})
            return

        if result.get("status") == "success":
            _invalidate_status_cache()
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
