"""
HTTP Server — 统一服务器，支持远程 Agent API + HUD 面板

远程 Agent（Hermes / OpenClaw）通过 SSH 隧道调用端口 9800：
  ssh -L 9800:localhost:9800 mac

HUD 面板前端通过端口 9801 访问。

架构：
  BaseHandler      — 共享基类（JSON/HTML 响应、CORS、EagleAPI 创建）
  RemoteHandler    — 远程 Agent API（GET /ping, GET /status, POST /import）
  PanelHandler     — HUD 面板 API（前端页面 + JSON API 路由）

用法：
  python server.py          → 启动远程 Agent API（端口 9800）
  python -c "from eagle_watcher.server import start_panel_server; start_panel_server()"
                            → 启动 HUD 面板 API（端口 9801）
"""

import json
import logging
import os
import secrets
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, parse_qs

from eagle_watcher.pyui.panel import set_pinned as panel_set_pinned
from eagle_watcher.ai_tagger import _get_api_key, _get_model, clear_cache as ai_clear_cache, get_cache_size as ai_cache_size
from eagle_watcher.config import (
    load_config, ensure_data_dir,
    get_categories, get_category_names, get_category_info,
    get_projects, get_project_info, get_current_project, set_current_project,
    create_project, delete_project, create_category, delete_category,
)
from eagle_watcher.eagle_api import EagleAPI, create_eagle_api
from eagle_watcher.services.state_manager import get_state_manager
from eagle_watcher.knowledge import match_by_filename, record_match
from eagle_watcher.analyzer import decide
from eagle_watcher.watcher import scan_directory, get_scan_progress

_LOG = logging.getLogger("server")

HOST = "127.0.0.1"
REMOTE_PORT = 9800
PANEL_PORT = 9801

_HTML_PATH = Path(__file__).parent / "pyui" / "panel.html"

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


# 幂等性缓存：有序字典 + 惰性过期，防止无限增长
_idempotency_cache: dict[str, float] = {}
_idempotency_lock = threading.Lock()
_IDEMPOTENCY_TTL = 30  # seconds
_IDEMPOTENCY_MAX_KEYS = 200  # 防止内存泄漏的硬上限

# 面板服务器 session token：启动时生成，注入前端页面，验证 API 请求
_panel_session_token: str = ""
_panel_token_initialized = False


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
    global _panel_session_token, _panel_token_initialized
    if not _panel_token_initialized:
        _panel_session_token = secrets.token_urlsafe(32)
        _panel_token_initialized = True
    return _panel_session_token


# ══════════════════════════════════════════════════════════════
# BaseHandler — 共享基类
# ══════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════
# RemoteHandler — 远程 Agent API（端口 9800）
# ══════════════════════════════════════════════════════════════

class RemoteHandler(BaseHandler):
    """远程 Agent API：GET /ping, GET /status, POST /import"""

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

        if path == "/ping":
            cfg = load_config()
            eagle = create_eagle_api(cfg)
            online = eagle.ping()
            self._send_json(200, {"status": "ok", "eagle_online": online})

        elif path == "/status":
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


# ══════════════════════════════════════════════════════════════
# PanelHandler — HUD 面板 API（端口 9801）
# ══════════════════════════════════════════════════════════════

class PanelHandler(BaseHandler):
    """HUD 面板：前端页面 + JSON API"""

    # ────────── CORS 覆写（面板仅允许 WKWebView）──────────

    def _send(self, code: int, body, content_type="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "null")
        self.end_headers()
        if isinstance(body, (dict, list)):
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            data = body.encode("utf-8")
        else:
            data = body
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "null")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Session-Token")
        self.end_headers()

    # ────────── 前端页面 ──────────

    def _handle_panel(self):
        html = _HTML_PATH.read_text(encoding="utf-8")
        self._send_html(200, html)

    # ────────── API: 状态 ──────────

    @staticmethod
    def _check_eagle_online(api: EagleAPI, now: float) -> bool:
        global _eagle_offline_since
        if _eagle_offline_since and now - _eagle_offline_since < _EAGLE_OFFLINE_RETRY_INTERVAL:
            return False
        online = api.ping()
        _eagle_offline_since = 0 if online else now
        return online

    @staticmethod
    def _fetch_categories_and_counts(api: EagleAPI) -> tuple[dict, int, int]:
        """从 Eagle API 并发拉取状态数据，返回 (categories, today_count, inbox_count)"""
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        local_cats = get_categories()

        try:
            pool = _get_status_pool()
            fut_recent = pool.submit(api.list_items, order_by="-btime", limit=100)
            fut_inbox = pool.submit(api.list_items, tags="待分类")
            fut_folders = pool.submit(api.list_folders)

            recent_items = fut_recent.result()
            inbox_items = fut_inbox.result()
            eagle_folders = fut_folders.result()

            today_count = sum(
                1 for item in recent_items
                if item.get("btime", 0) > 0
                and datetime.fromtimestamp(item["btime"] / 1000).strftime("%Y-%m-%d") == today
            )

            merged = {}
            for f in eagle_folders:
                name = f["name"]
                merged[name] = {"eagle_folder": name, "folder_id": f["id"], "from_eagle": True}
                local_cats.pop(name, None)
            for name, info in local_cats.items():
                merged[name] = info
                merged[name]["from_eagle"] = False

            return merged, today_count, len(inbox_items)
        except Exception as e:
            _LOG.warning("Eagle API 读取失败: %s", e)
            return local_cats, 0, 0

    @staticmethod
    def _check_downloads_permission() -> tuple[bool, str]:
        try:
            downloads = load_config().get("paths", {}).get("downloads", "")
            if not downloads:
                return False, ""
            test_file = os.path.join(downloads, ".eagle-watcher-perm-test")
            try:
                with open(test_file, "w") as f:
                    f.write("test")
            except PermissionError:
                return True, downloads
            else:
                try:
                    os.remove(test_file)
                except OSError:
                    pass
                return False, ""
        except (OSError, AttributeError, TypeError):
            return False, ""

    def _handle_api_status(self):
        global _status_cache
        now = time.time()
        with _status_cache_lock:
            if _status_cache["data"] and now - _status_cache["ts"] < _STATUS_CACHE_TTL:
                self._send_json(200, _status_cache["data"])
                return

        sm = get_state_manager()
        api = create_eagle_api(load_config())
        online = self._check_eagle_online(api, now)

        local_cats = get_categories()
        projects = get_projects()
        categories, today_count, inbox_count = (
            self._fetch_categories_and_counts(api) if online else (local_cats, 0, 0)
        )
        permission_denied, permission_path = self._check_downloads_permission()

        result = {
            "current_project": get_current_project(),
            "categories": categories,
            "projects": projects,
            "eagle_online": online,
            "permission_denied": permission_denied,
            "permission_path": permission_path,
            "today_count": today_count,
            "inbox_count": inbox_count,
            "last_processed": sm.get_last_processed(),
            "ai_configured": bool(_get_api_key()),
            "ai_model": _get_model() if _get_api_key() else None,
            "watch_dirs": _get_watch_dirs_from_config(),
        }
        with _status_cache_lock:
            _status_cache["data"] = result
            _status_cache["ts"] = now
        self._send_json(200, result)

    # ────────── API: 项目操作 ──────────

    def _handle_set_project(self, body: dict):
        name = body.get("project")
        set_current_project(name)
        _invalidate_status_cache()
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
            record_match(name, name, category, tags)
            _LOG.info("知识库已学习: %s → %s", name, category)
        except Exception as e:
            _LOG.warning("知识库写入失败: %s", e)

        _invalidate_status_cache()
        self._send_json(200, {"ok": True, "name": name, "knowledge_learned": True})

    def _handle_delete_project(self, body: dict):
        name = body.get("name", "").strip()
        if not name:
            self._send_json(400, {"error": "name is required"})
            return
        delete_project(name)
        _invalidate_status_cache()
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
        _invalidate_status_cache()
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

        # 级联删除需要显式确认 — 在确认前不应用幂等性缓存
        if not body.get("confirm"):
            projects = get_projects()
            affected = [pn for pn, pi in projects.items() if pi.get("category") == name]
            if self._check_idempotency(body, f"del_cat_preconfirm:{name}"):
                pass  # 幂等确认检查：已答复过
            self._send_json(400, {
                "error": "cascade_confirm_required",
                "affected_projects": affected,
                "message": f"删除「{name}」将同时删除 {len(affected)} 个关联主题",
            })
            return

        # 收集删除前的所有数据（用于潜在的回滚）
        cat_info = get_category_info(name)
        folder_id = cat_info.get("folder_id") if cat_info else None
        projects_to_delete = [
            pn for pn, pi in get_projects().items() if pi.get("category") == name
        ]

        failures = []

        # Step 1: 删除 Eagle 文件夹（失败不阻断，继续删本地）
        if folder_id:
            try:
                api = self._eagle_safe()
                if api:
                    api.delete_folder(folder_id)
            except Exception as e:
                _LOG.warning("删除 Eagle 文件夹失败 %s: %s", folder_id, e)
                failures.append(f"Eagle 文件夹: {e}")

        # Step 2: 删除本地关联项目
        for pname in projects_to_delete:
            try:
                delete_project(pname)
            except Exception as e:
                _LOG.warning("删除项目失败 %s: %s", pname, e)
                failures.append(f"主题 {pname}: {e}")

        # Step 3: 删除本地分类本身
        try:
            delete_category(name)
        except Exception as e:
            _LOG.warning("删除分类失败 %s: %s", name, e)
            failures.append(f"分类 {name}: {e}")

        if failures:
            _invalidate_status_cache()  # 部分删除也可能改变了 Eagle 状态
            self._send_json(500, {
                "ok": False,
                "error": f"部分删除失败，可能遗留了 {len(failures)} 个条目",
                "failures": failures,
                "partial": True,
            })
        else:
            # 全量删除成功后才缓存幂等性 key，避免部分失败后重试被误判为已处理
            self._check_idempotency(body, f"del_cat:{name}")
            _invalidate_status_cache()
            self._send_json(200, {"ok": True})
    # ────────── API: 通用箱整理 ──────────

    def _handle_api_inbox(self):
        api = self._eagle()
        if not api:
            return
        # 支持分页参数
        qs = parse_qs(urlparse(self.path).query)
        limit = int(qs.get("limit", [50])[0])
        offset = int(qs.get("offset", [0])[0])

        items = api.list_items(tags="待分类", limit=limit, offset=offset)

        result = []
        for item in items:
            name = f"{item.get('name', '')}.{item.get('ext', '')}"
            match = match_by_filename(name)
            result.append({
                "id": item["id"],
                "name": name,
                "thumbnail": item.get("thumbnail", ""),
                "tags": item.get("tags", []),
                "suggested_theme": match["theme"] if match else None,
                "suggested_tags": match["tags"] if match else [],
                "confidence": match.get("confidence", 0) if match else 0,
            })
        # total 为 offset + len(items)，避免前端计算负数
        self._send_json(200, {
            "items": result,
            "total": offset + len(items),
            "offset": offset,
            "limit": limit,
            "has_more": len(items) >= limit,
        })

    def _handle_sort_confirm(self, body: dict):
        item_id = body.get("id", "").strip()
        incoming_tags = body.get("tags", [])
        replace_tags = body.get("replace_tags", False)
        if not item_id:
            self._send_json(400, {"error": "id is required"})
            return

        if self._check_idempotency(body, f"sort_cfm:{item_id}"):
            self._send_json(200, {"ok": True, "cached": True})
            return
        api = self._eagle()
        if not api:
            return
        # 从 Eagle 重新读取当前标签（而非信任前端传来的 existing_tags）
        item = api.get_item(item_id)
        existing_tags = item.get("tags", []) if item else []
        # folder 是用户选择的主题/项目名，用于在 Eagle 中查找或创建对应文件夹（方便手动整理）
        # 注意：Eagle API 不支持 item/move 端点，素材不会被自动移动到该文件夹
        # 主题/项目信息通过标签关联，而非文件系统目录层级
        # P1-6: 优先使用主题关联的分类文件夹，而非主题名创建新文件夹
        folder_id = None
        folder = body.get("folder")
        if folder:
            project_info = get_project_info(folder)
            if project_info:
                eagle_folder = project_info.get("eagle_folder") or folder
                folder_id = api.get_or_create_folder(eagle_folder)
            else:
                folder_id = api.get_or_create_folder(folder)
        if replace_tags:
            # 替换模式：完全使用传入的标签
            new_tags = [t for t in incoming_tags if t != "待分类"]
        else:
            # 合并模式：保留非「待分类」的现有标签 + 去重追加新标签
            new_tags = [t for t in existing_tags if t != "待分类"]
            for t in incoming_tags:
                if t not in new_tags:
                    new_tags.append(t)
        result = api.update_item(item_id, tags=new_tags)
        if result.get("status") == "success":
            # 写入知识库
            filename = body.get("name", "")
            if filename and folder:
                try:
                    record_match(filename, Path(filename).stem, folder, incoming_tags)
                    _LOG.info("知识库已学习: %s → %s", filename, folder)
                except Exception as e:
                    _LOG.warning("知识库写入失败: %s", e)
            _invalidate_status_cache()
            self._send_json(200, {
                "ok": True,
                "note": "标签已更新。Eagle 暂不支持自动移动素材到不同文件夹，你可以在 Eagle 中手动整理。"
            })
        else:
            self._send_json(500, {"error": str(result)})

    def _handle_sort_skip(self, body: dict):
        item_id = body.get("id", "").strip()
        if not item_id:
            self._send_json(400, {"error": "id is required"})
            return

        if self._check_idempotency(body, f"sort_skip:{item_id}"):
            self._send_json(200, {"ok": True, "cached": True})
            return
        api = self._eagle()
        if not api:
            return
        # 从 Eagle 重新读取当前标签
        item = api.get_item(item_id)
        existing_tags = item.get("tags", []) if item else []
        # 移除"待分类"标签，添加"已跳过"标签
        new_tags = [t for t in existing_tags if t != "待分类"]
        if "已跳过" not in new_tags:
            new_tags.append("已跳过")
        result = api.update_item(item_id, tags=new_tags)
        if result.get("status") == "success":
            _invalidate_status_cache()
            self._send_json(200, {"ok": True})
        else:
            self._send_json(500, {"error": str(result)})

    # ────────── API: 置顶 ──────────

    def _handle_set_pinned(self, body: dict):
        pinned = body.get("pinned", False)
        try:
            panel_set_pinned(pinned)
            self._send_json(200, {"ok": True, "pinned": pinned})
        except Exception as e:
            _LOG.exception("置顶操作异常: %s", e)
            self._send_json(500, {"error": str(e)})

    # ────────── API: 系统动作 ──────────

    def _handle_api_action(self, body: dict):
        import subprocess
        action = body.get("action", "")
        if action == "open-eagle":
            subprocess.Popen(["open", "-a", "Eagle"])
            self._send_json(200, {"ok": True})
        elif action == "open-privacy-settings":
            subprocess.Popen(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"])
            self._send_json(200, {"ok": True})
        else:
            self._send_json(400, {"error": f"unknown action: {action}"})

    # ────────── API: 临时监控目录管理 ──────────

    def _handle_watch_dirs_add(self, body: dict):
        path = body.get("path", "").strip()
        if not path:
            self._send_json(400, {"error": "path is required"})
            return
        expanded = os.path.expanduser(path)
        if not Path(expanded).is_dir():
            self._send_json(400, {"error": f"目录不存在: {expanded}", "path": expanded})
            return
        sm = get_state_manager()
        added = sm.add_temp_watch_dir(expanded)
        _invalidate_status_cache()
        self._send_json(200, {"ok": True, "path": expanded, "added": added})

    def _handle_watch_dirs_remove(self, body: dict):
        path = body.get("path", "").strip()
        if not path:
            self._send_json(400, {"error": "path is required"})
            return
        expanded = os.path.expanduser(path)
        sm = get_state_manager()
        removed = sm.remove_temp_watch_dir(expanded)
        _invalidate_status_cache()
        self._send_json(200, {"ok": True, "path": expanded, "removed": removed})

    def _handle_trigger_picker(self):
        from eagle_watcher.pyui.panel import trigger_folder_picker
        trigger_folder_picker()
        self._send_json(200, {"ok": True})

    def _handle_picker_result(self):
        from eagle_watcher.pyui.panel import get_picker_result
        status, path = get_picker_result()
        if status == "done" and path:
            sm = get_state_manager()
            sm.add_temp_watch_dir(path)
            _invalidate_status_cache()
            self._send_json(200, {"status": "done", "path": path})
        elif status == "cancelled":
            self._send_json(200, {"status": "cancelled"})
        else:
            self._send_json(200, {"status": "pending"})

    def _handle_watch_dir_scan(self, body: dict):
        path = body.get("path", "").strip()
        if not path:
            self._send_json(400, {"error": "path is required"})
            return
        expanded = os.path.expanduser(path)
        if not Path(expanded).is_dir():
            self._send_json(400, {"error": f"目录不存在: {expanded}"})
            return
        sm = get_state_manager()
        if expanded not in sm.get_temp_watch_dirs():
            self._send_json(400, {"error": "只支持扫描临时监控目录"})
            return
        progress = get_scan_progress()
        if progress.get("status") == "scanning":
            self._send_json(409, {"error": "已有扫描进行中", "progress": progress})
            return
        api = self._eagle()
        if not api:
            return
        file_filter = body.get("filter") or None
        scan_directory(api, expanded, recursive=True, file_filter=file_filter)
        self._send_json(200, {"ok": True, "path": expanded, "filter": file_filter or "default", "message": "扫描已启动"})

    # ────────── API: AI 缓存管理 ──────────

    def _handle_ai_cache_clear(self):
        size_before = ai_cache_size()
        ai_clear_cache()
        self._send_json(200, {
            "ok": True,
            "cleared_bytes": size_before,
        })

    # ────────── API: POST /import（复现原有逻辑）──────────

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

        # 当未指定 project 时，通过 decide() 决策引擎自动判断
        if not project:
            filename = Path(file_path).name if file_path else Path(file_url).name
            decision = decide(filename)
            if decision.get("theme"):
                project = decision["theme"]
                # 决策引擎返回的标签作为默认标签，合并用户传入的标签
                tags = list(dict.fromkeys(decision.get("tags", []) + tags))
                folder = folder or decision.get("folder", project)

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
            _LOG.exception("导入失败")
            self._send_json(500, {"error": str(e)})
            return

        if result.get("status") == "success":
            # 学习知识：如果有来自决策引擎的项目匹配，记录到知识库
            if project and file_path:
                try:
                    from eagle_watcher.knowledge import record_match
                    filename = Path(file_path).stem if file_path else Path(file_url).stem
                    record_match(str(filename), filename, project, tags)
                except Exception:
                    pass
            _invalidate_status_cache()
            self._send_json(200, {"status": "success", "data": {"tags": tags, "folder": target_folder}})
        else:
            self._send_json(500, {"error": str(result)})

# ────────── 路由 ──────────

    @staticmethod
    def _check_idempotency(body: dict, name: str) -> bool:
        """检查 idempotency_key，已处理返回 True，否则记录并返回 False。"""
        global _idempotency_cache
        idemp_key = body.get("idempotency_key") or f"{name}:{time.time():.0f}"
        now = time.time()
        with _idempotency_lock:
            if idemp_key in _idempotency_cache:
                return True
            # 在插入新 key 前检查上限，淘汰最旧条目
            if len(_idempotency_cache) >= _IDEMPOTENCY_MAX_KEYS:
                cutoff = now - _IDEMPOTENCY_TTL
                _idempotency_cache = {k: ts for k, ts in _idempotency_cache.items() if ts > cutoff}
            _idempotency_cache[idemp_key] = now
        return False

    def _route(self):
        try:
            self._do_route()
        except Exception as e:
            _LOG.error("路由异常: %s", e, exc_info=True)
            self._send_json(500, {"error": "internal error", "detail": str(e)})

    def _do_route(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        # POST 请求验证 session token（面板内部安全）
        if self.command == "POST":
            token = self.headers.get("X-Session-Token", "")
            expected = _ensure_panel_token()
            if token != expected:
                self._send_json(403, {"error": "invalid session token"})
                return

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
            elif path == "/api/history":
                from eagle_watcher.services.history import recent
                self._send_json(200, {"items": recent(50)})
            elif path == "/api/watch-dirs":
                self._send_json(200, {"dirs": _get_watch_dirs_from_config()})
            elif path == "/api/watch-dirs/scan-status":
                self._send_json(200, get_scan_progress())
            elif path == "/api/watch-dirs/picker-result":
                self._handle_picker_result()
            elif path == "/api/watch-dirs/scan-preview":
                import urllib.parse
                params = urllib.parse.parse_qs(parsed.query)
                scan_path = params.get("path", [None])[0]
                if not scan_path:
                    self._send_json(400, {"error": "path is required"})
                    return
                from eagle_watcher.watcher import count_files_by_type
                stats = count_files_by_type(scan_path)
                self._send_json(200, stats)
            elif path == "/api/watch-dirs/filter-presets":
                from eagle_watcher.watcher import get_filter_presets
                self._send_json(200, {"presets": get_filter_presets()})
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
            elif path == "/api/sort/skip":
                self._handle_sort_skip(body)
            elif path == "/api/ai/cache/clear":
                self._handle_ai_cache_clear()
            elif path == "/api/watch-dirs/add":
                self._handle_watch_dirs_add(body)
            elif path == "/api/watch-dirs/remove":
                self._handle_watch_dirs_remove(body)
            elif path == "/api/watch-dirs/scan":
                self._handle_watch_dir_scan(body)
            elif path == "/api/watch-dirs/trigger-picker":
                self._handle_trigger_picker()
            elif path == "/api/set-pinned":
                self._handle_set_pinned(body)
            elif path == "/api/history/clear":
                from eagle_watcher.services.history import clear as clear_history
                clear_history()
                self._send_json(200, {"ok": True})
            else:
                self._send_json(404, {"error": "not found"})

        else:
            self._send_json(405, {"error": "method not allowed"})

    def do_GET(self):
        self._route()

    def do_POST(self):
        self._route()


# ══════════════════════════════════════════════════════════════
# 启动入口
# ══════════════════════════════════════════════════════════════

def start_remote_server(host: str = HOST, port: int = REMOTE_PORT):
    """启动远程 Agent API 服务器（端口 9800）。"""
    try:
        from eagle_watcher._logging import setup_logging
        setup_logging("eagle-server")
    except Exception:
        pass

    HTTPServer.allow_reuse_address = True
    server = HTTPServer((host, port), RemoteHandler)
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


def start_panel_server(host: str = HOST, port: int = PANEL_PORT):
    """启动 HUD 面板 API 服务器（端口 9801）。"""
    HTTPServer.allow_reuse_address = True
    server = HTTPServer((host, port), PanelHandler)
    _LOG.info("HTTP 服务器已启动: http://%s:%s", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


# 向后兼容别名：eagle-watcher.pyproject.toml 指向此入口
# menu_app.py 从 eagle_watcher.pyui.server 导入 start_server
start_server = start_remote_server

if __name__ == "__main__":
    ensure_data_dir()
    start_server()
