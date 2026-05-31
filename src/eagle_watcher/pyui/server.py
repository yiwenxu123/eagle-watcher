"""HTTP Server — 提供 HUD 前端 + JSON API"""
import json
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from eagle_watcher.config import (
    load_config, ensure_data_dir,
    get_categories, get_category_names, get_category_info,
    get_projects, get_project_names, get_project_info,
    get_current_project, set_current_project,
    create_project, delete_project,
    create_category, delete_category,
    save_categories, save_projects,
)
from eagle_watcher.eagle_api import EagleAPI, create_eagle_api
from eagle_watcher.services.state_manager import get_state_manager
from eagle_watcher.knowledge import match_by_filename

_LOG = logging.getLogger("server")

# 置顶状态（panel.py 通过 import 读取）
_pinned = False


def is_pinned() -> bool:
    return _pinned


def set_pinned(v: bool) -> None:
    global _pinned
    _pinned = v
    _LOG.info("Pin: %s", "enabled" if v else "disabled")

HOST = "127.0.0.1"
PORT = 9800

_HTML_PATH = Path(__file__).parent / "panel.html"


class Handler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        _LOG.info("HTTP %s %s - %s", self.command, self.path, args[0] if args else '')

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

    def _send_html(self, code: int, html: str):
        # 禁止缓存，确保 WKWebView 始终拿到最新面板
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        data = html.encode("utf-8") if isinstance(html, str) else html
        self.wfile.write(data)

    def _send_json(self, code: int, data: dict):
        self._send(code, data, "application/json")

    def _eagle(self) -> Optional[EagleAPI]:
        cfg = load_config()
        api = create_eagle_api(cfg)
        if not api.ping():
            self._send_json(503, {"error": "Eagle 未运行"})
            return None
        return api

    def _eagle_safe(self) -> Optional[EagleAPI]:
        """不报错的 _eagle 版本，Eagle 离线时返回 None"""
        cfg = load_config()
        api = create_eagle_api(cfg)
        return api if api.ping() else None

    # ────────── 前端页面 ──────────

    def _handle_panel(self):
        html = _HTML_PATH.read_text(encoding="utf-8")
        self._send_html(200, html)

    # ────────── API: 状态 ──────────

    def _handle_api_status(self):
        sm = get_state_manager()
        api = create_eagle_api(load_config())
        online = api.ping()

        today_count, inbox_count = 0, 0
        local_cats = get_categories()
        projects = get_projects()

        if online:
            try:
                items = api.list_items()
                from datetime import datetime
                today = datetime.now().strftime("%Y-%m-%d")
                for item in items:
                    btime = item.get("btime", 0) / 1000
                    if btime > 0 and datetime.fromtimestamp(btime).strftime("%Y-%m-%d") == today:
                        today_count += 1
                    if "待分类" in item.get("tags", []):
                        inbox_count += 1

                # 从 Eagle 真实文件夹构建分类，覆盖本地数据
                eagle_folders = api.list_folders()
                merged = {}
                for f in eagle_folders:
                    name = f["name"]
                    merged[name] = {
                        "eagle_folder": name,
                        "folder_id": f["id"],
                        "from_eagle": True,
                    }
                    # 保留本地元数据（项目关联等）
                    if name in local_cats:
                        local_cats.pop(name)
                # 补充本地有但 Eagle 没有的分类（标记未关联）
                for name, info in local_cats.items():
                    merged[name] = info
                    merged[name]["from_eagle"] = False
                categories = merged
            except Exception as e:
                _LOG.warning("Eagle API 读取失败: %s", e)
                categories = local_cats
        else:
            categories = local_cats

        self._send_json(200, {
            "current_project": get_current_project(),
            "categories": categories,
            "projects": projects,
            "eagle_online": online,
            "today_count": today_count,
            "inbox_count": inbox_count,
            "last_processed": sm.get_last_processed(),
        })

    # ────────── API: 项目操作 ──────────

    def _handle_set_project(self, body: dict):
        name = body.get("project")
        set_current_project(name)
        self._send_json(200, {"ok": True, "current_project": name})

    def _handle_create_project(self, body: dict):
        name = body.get("name", "").strip()
        category = body.get("category", "").strip()
        tags = body.get("tags", [])
        if not name:
            self._send_json(400, {"error": "name is required"})
            return
        if not category:
            cats = get_category_names()
            if cats:
                category = cats[0]
            else:
                self._send_json(400, {"error": "no categories exist"})
                return
        create_project(name, category, tags)

        # 写入知识库，自动文件名匹配生效
        try:
            from eagle_watcher.knowledge import record_match
            record_match(name, name, category, tags)
            _LOG.info("知识库已学习: %s → %s", name, category)
        except Exception as e:
            _LOG.warning("知识库写入失败: %s", e)

        self._send_json(200, {"ok": True, "name": name, "knowledge_learned": True})

    def _handle_delete_project(self, body: dict):
        name = body.get("name", "").strip()
        if not name:
            self._send_json(400, {"error": "name is required"})
            return
        delete_project(name)
        self._send_json(200, {"ok": True})

    # ────────── API: 分类操作 ──────────

    def _handle_create_category(self, body: dict):
        name = body.get("name", "").strip()
        if not name:
            self._send_json(400, {"error": "name is required"})
            return

        folder_id = None
        api = self._eagle_safe()
        if api:
            folder_id = api.get_or_create_folder(name)

        create_category(name, folder_id=folder_id)
        self._send_json(200, {
            "ok": True,
            "name": name,
            "eagle_folder_created": api is not None,
        })

    def _handle_delete_category(self, body: dict):
        name = body.get("name", "").strip()
        if not name:
            self._send_json(400, {"error": "name is required"})
            return

        # 删除 Eagle 文件夹
        cat_info = get_category_info(name)
        if cat_info:
            folder_id = cat_info.get("folder_id")
            if folder_id:
                try:
                    api = self._eagle_safe()
                    if api:
                        api.delete_folder(folder_id)
                except Exception as e:
                    _LOG.warning("删除 Eagle 文件夹失败 %s: %s", folder_id, e)

        # 删除本地关联项目和分类
        projects = get_projects()
        for pname, info in list(projects.items()):
            if info.get("category") == name:
                delete_project(pname)
        delete_category(name)
        self._send_json(200, {"ok": True})

    # ────────── API: 通用箱整理 ──────────

    def _handle_api_inbox(self):
        api = self._eagle()
        if not api:
            return
        items = api.list_items(tags="待分类")
        result = []
        for item in items:
            name = f"{item.get('name', '')}.{item.get('ext', '')}"
            match = match_by_filename(name)
            result.append({
                "id": item["id"],
                "name": name,
                "thumbnail": item.get("thumbnail", ""),
                "tags": item.get("tags", []),  # 现有标签，用于 sort_confirm 合并
                "suggested_theme": match["theme"] if match else None,
                "suggested_tags": match["tags"] if match else [],
                "confidence": match.get("confidence", 0) if match else 0,
            })
        self._send_json(200, {"items": result, "total": len(result)})

    def _handle_sort_confirm(self, body: dict):
        item_id = body.get("id", "").strip()
        incoming_tags = body.get("tags", [])
        existing_tags = body.get("existing_tags", [])
        if not item_id:
            self._send_json(400, {"error": "id is required"})
            return
        api = self._eagle()
        if not api:
            return
        # 如果提供了分类文件夹，确保存在
        folder_id = None
        folder = body.get("folder")
        if folder:
            folder_id = api.get_or_create_folder(folder)
        # 合并标签：保留非「待分类」的现有标签 + 去重追加新标签
        new_tags = [t for t in existing_tags if t != "待分类"]
        for t in incoming_tags:
            if t not in new_tags:
                new_tags.append(t)
        result = api.update_item(item_id, tags=new_tags)
        if result.get("status") == "success":
            self._send_json(200, {"ok": True})
        else:
            self._send_json(500, {"error": str(result)})

    # ────────── API: 置顶 ──────────

    def _handle_set_pinned(self, body: dict):
        set_pinned(body.get("pinned", False))
        self._send_json(200, {"ok": True, "pinned": is_pinned()})

    # ────────── API: 系统动作 ──────────

    def _handle_api_action(self, body: dict):
        import subprocess
        action = body.get("action", "")
        if action == "open-eagle":
            subprocess.Popen(["open", "-a", "Eagle"])
            self._send_json(200, {"ok": True})
        else:
            self._send_json(400, {"error": f"unknown action: {action}"})

    # ────────── 路由 ──────────

    def _route(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if self.command == "GET":
            if path == "/ping":
                api = create_eagle_api(load_config())
                self._send_json(200, {"status": "ok", "eagle_online": api.ping()})
            elif path == "/status":
                self._handle_api_status()
            elif path in ("/", "/panel"):
                self._handle_panel()
            elif path == "/api/status":
                self._handle_api_status()
            elif path == "/api/inbox":
                self._handle_api_inbox()
            else:
                self._send_json(404, {"error": "not found"})

        elif self.command == "POST":
            content_len = int(self.headers.get("Content-Length", 0))
            body = {}
            if content_len > 0:
                raw = self.rfile.read(content_len)
                try:
                    body = json.loads(raw)
                except json.JSONDecodeError:
                    self._send_json(400, {"error": "invalid JSON"})
                    return

            if path == "/import":
                self._handle_import(body)
            elif path == "/api/current-project":
                self._handle_set_project(body)
            elif path == "/api/projects/create":
                self._handle_create_project(body)
            elif path == "/api/projects/delete":
                self._handle_delete_project(body)
            elif path == "/api/categories/create":
                self._handle_create_category(body)
            elif path == "/api/categories/delete":
                self._handle_delete_category(body)
            elif path == "/api/sort/confirm":
                self._handle_sort_confirm(body)
            elif path == "/api/action":
                self._handle_api_action(body)
            elif path == "/api/set-pinned":
                self._handle_set_pinned(body)
            else:
                self._send_json(404, {"error": "not found"})

        else:
            self._send_json(405, {"error": "method not allowed"})

    def do_GET(self):
        self._route()

    def do_POST(self):
        self._route()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # ────────── POST /import（复现原有逻辑）──────────

    def _handle_import(self, body: dict):
        file_url = body.get("file_url", "")
        file_path = body.get("file_path", "")
        project = body.get("project")
        tags = body.get("tags", [])
        folder = body.get("folder")

        if not file_url and not file_path:
            self._send_json(400, {"error": "file_url or file_path required"})
            return

        api = self._eagle()
        if not api:
            return

        target_folder = folder or project
        folder_id = None
        if target_folder:
            folder_id = api.get_or_create_folder(target_folder)

        try:
            if file_path:
                if not Path(file_path).exists():
                    self._send_json(400, {"error": f"file not found: {file_path}"})
                    return
                result = api.add_from_path(file_path, tags=tags, folder_id=folder_id)
            else:
                result = api.add_from_url(file_url, tags=tags, folder_id=folder_id)
        except Exception as e:
            self._send_json(500, {"error": str(e)})
            return

        if result.get("status") == "success":
            self._send_json(200, {"status": "success", "data": {"tags": tags, "folder": target_folder}})
        else:
            self._send_json(500, {"error": str(result)})


def start_server(host: str = HOST, port: int = PORT):
    server = HTTPServer((host, port), Handler)
    _LOG.info("HTTP 服务器已启动: http://%s:%s", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
