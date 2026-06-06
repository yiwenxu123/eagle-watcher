"""Tests for pyui/server.py — HTTP handler routing + API logic"""

import io
import json
import secrets
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import eagle_watcher.watcher as _watcher_mod

from eagle_watcher.server import (
    PanelHandler as Handler,
    _ensure_panel_token,
    _status_cache,
    _STATUS_CACHE_TTL,
)


# ── helpers ──


def _parse_response(wfile_bytes: bytes):
    """Parse HTTP response bytes → (status_code, headers_dict, body_str)."""
    text = wfile_bytes.decode("utf-8")
    parts = text.split("\r\n\r\n", 1)
    header_lines = parts[0].split("\r\n")
    status_code = int(header_lines[0].split(" ")[1])
    headers = {}
    for line in header_lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip()] = v.strip()
    body = parts[1] if len(parts) > 1 else ""
    return status_code, headers, body


# ── fixtures ──


@pytest.fixture
def handler(mock_data_dir):
    """Create a bare Handler instance with minimal HTTP request attributes."""
    h = Handler.__new__(Handler)
    h.command = "GET"
    h.path = "/"
    h.request_version = "HTTP/1.0"
    h.protocol_version = "HTTP/1.0"
    h.headers = {}
    h.rfile = io.BytesIO(b"")
    h.wfile = io.BytesIO()
    h.client_address = ("127.0.0.1", 0)
    h.close_connection = True
    h._headers_buffer = []
    h.requestline = "GET / HTTP/1.0"
    h.raw_requestline = b"GET / HTTP/1.0"
    return h


@pytest.fixture
def eagle_online(handler, monkeypatch, mock_eagle_api):
    """Patch create_eagle_api so handle methods get a mock EagleAPI with ping()==True."""
    import eagle_watcher.server.panel as _panel_mod
    _panel_mod._eagle_offline_since = 0
    monkeypatch.setattr(
        "eagle_watcher.server.base.create_eagle_api",
        lambda cfg: mock_eagle_api,
    )
    monkeypatch.setattr(
        "eagle_watcher.server.panel.create_eagle_api",
        lambda cfg: mock_eagle_api,
    )
    return handler


@pytest.fixture
def eagle_offline(handler, monkeypatch):
    """Patch create_eagle_api so handle methods get a mock with ping()==False."""
    offline_api = MagicMock()
    offline_api.ping.return_value = False
    monkeypatch.setattr(
        "eagle_watcher.server.base.create_eagle_api",
        lambda cfg: offline_api,
    )
    monkeypatch.setattr(
        "eagle_watcher.server.panel.create_eagle_api",
        lambda cfg: offline_api,
    )
    return handler


@pytest.fixture
def with_panel(handler, monkeypatch, mock_data_dir):
    """Create a panel.html file so _handle_panel can read it."""
    html_path = mock_data_dir / "panel.html"
    html_path.write_text("<html>test panel</html>")
    monkeypatch.setattr("eagle_watcher.server.panel._HTML_PATH", html_path)
    return handler


# ── helper methods ──


class PyUIHelpers:
    """Mixin-style helpers injected into the test class."""

    @staticmethod
    def do_get(handler, path):
        handler.command = "GET"
        handler.path = path
        handler.wfile = io.BytesIO()
        handler._headers_buffer = []
        handler.headers = {}
        handler.rfile = io.BytesIO(b"")
        handler.do_GET()
        return _parse_response(handler.wfile.getvalue())

    @staticmethod
    def do_post(handler, path, body_dict=None):
        handler.command = "POST"
        handler.path = path
        handler.wfile = io.BytesIO()
        handler._headers_buffer = []
        token = _ensure_panel_token()
        body_bytes = json.dumps(body_dict or {}).encode("utf-8")
        handler.headers = {
            "Content-Length": str(len(body_bytes)),
            "X-Session-Token": token,
        }
        handler.rfile = io.BytesIO(body_bytes)
        handler.do_POST()
        return _parse_response(handler.wfile.getvalue())


# ── reset cache between tests ──


@pytest.fixture(autouse=True)
def reset_cache():
    _status_cache["data"] = None
    _status_cache["ts"] = 0
    with _watcher_mod._scan_lock:
        _watcher_mod._scan_progress.clear()
    yield


# ══════════════════════════════════════════
# Tests
# ══════════════════════════════════════════


class TestGETRoutes(PyUIHelpers):
    """GET route handling"""

    def test_get_ping(self, eagle_online):
        """GET /ping returns 200 with eagle status"""
        code, headers, body = self.do_get(eagle_online, "/ping")
        assert code == 200
        data = json.loads(body)
        assert data["status"] == "ok"
        assert data["eagle_online"] is True

    def test_get_status(self, eagle_online):
        """GET /status returns 200 with full status payload"""
        code, headers, body = self.do_get(eagle_online, "/status")
        assert code == 200
        data = json.loads(body)
        assert "current_project" in data
        assert "categories" in data
        assert isinstance(data["categories"], dict)
        assert "projects" in data
        assert isinstance(data["projects"], dict)
        assert data["eagle_online"] is True

    def test_get_root(self, with_panel):
        """GET / returns 200 with panel HTML"""
        code, headers, body = self.do_get(with_panel, "/")
        assert code == 200
        assert headers.get("Content-Type", "").startswith("text/html")
        assert "test panel" in body

    def test_get_panel(self, with_panel):
        """GET /panel returns 200 with panel HTML"""
        code, headers, body = self.do_get(with_panel, "/panel")
        assert code == 200
        assert "test panel" in body

    def test_get_api_status(self, eagle_online):
        """GET /api/status returns 200 with status payload"""
        code, headers, body = self.do_get(eagle_online, "/api/status")
        assert code == 200
        data = json.loads(body)
        assert data["eagle_online"] is True
        assert "categories" in data
        assert "projects" in data

    def test_get_api_inbox(self, eagle_online):
        """GET /api/inbox returns 200 with inbox items"""
        code, headers, body = self.do_get(eagle_online, "/api/inbox")
        assert code == 200
        data = json.loads(body)
        assert "items" in data
        assert isinstance(data["items"], list)
        assert "total" in data
        assert "has_more" in data

    def test_get_api_history(self, eagle_online):
        """GET /api/history returns 200 with history items"""
        code, headers, body = self.do_get(eagle_online, "/api/history")
        assert code == 200
        data = json.loads(body)
        assert "items" in data
        assert isinstance(data["items"], list)

    def test_get_unknown_route_returns_404(self, eagle_online):
        """GET /nonexistent returns 404"""
        code, headers, body = self.do_get(eagle_online, "/nonexistent")
        assert code == 404
        data = json.loads(body)
        assert "not found" in data["error"].lower()


class TestPOSTRoutes(PyUIHelpers):
    """POST route handling"""

    # ── /import ──

    def test_post_import_with_url(self, eagle_online):
        """POST /import with file_url returns 200"""
        code, headers, body = self.do_post(eagle_online, "/import", {
            "file_url": "https://example.com/img.jpg",
            "project": "test",
            "tags": ["tag1"],
        })
        assert code == 200
        data = json.loads(body)
        assert data["status"] == "success"

    def test_post_import_missing_params(self, eagle_online):
        """POST /import with empty body returns 400"""
        code, headers, body = self.do_post(eagle_online, "/import", {})
        assert code == 400
        data = json.loads(body)
        assert "required" in data["error"]

    # ── /api/current-project ──

    def test_post_set_project(self, eagle_online):
        """POST /api/current-project sets current project"""
        code, headers, body = self.do_post(eagle_online, "/api/current-project", {
            "project": "武安侯",
        })
        assert code == 200
        data = json.loads(body)
        assert data["ok"] is True
        assert data["current_project"] == "武安侯"

    # ── /api/projects/create ──

    def test_post_create_project(self, eagle_online):
        """POST /api/projects/create creates a new project"""
        code, headers, body = self.do_post(eagle_online, "/api/projects/create", {
            "name": "新项目",
            "category": "设计",
            "tags": ["测试"],
        })
        assert code == 200
        data = json.loads(body)
        assert data["ok"] is True
        assert data["knowledge_learned"] is True

    def test_post_create_project_no_name(self, eagle_online):
        """POST /api/projects/create without name returns 400"""
        code, headers, body = self.do_post(eagle_online, "/api/projects/create", {})
        assert code == 400
        data = json.loads(body)
        assert "name" in data.get("error", "").lower()

    # ── /api/projects/delete ──

    def test_post_delete_project(self, eagle_online):
        """POST /api/projects/delete removes a project"""
        code, headers, body = self.do_post(eagle_online, "/api/projects/delete", {
            "name": "武安侯",
        })
        assert code == 200
        data = json.loads(body)
        assert data["ok"] is True

    def test_post_delete_project_no_name(self, eagle_online):
        """POST /api/projects/delete without name returns 400"""
        code, headers, body = self.do_post(eagle_online, "/api/projects/delete", {})
        assert code == 400
        data = json.loads(body)
        assert "name" in data.get("error", "").lower()

    # ── /api/categories/create ──

    def test_post_create_category(self, eagle_online):
        """POST /api/categories/create creates a new category"""
        code, headers, body = self.do_post(eagle_online, "/api/categories/create", {
            "name": "新分类",
        })
        assert code == 200
        data = json.loads(body)
        assert data["ok"] is True
        assert "eagle_folder_created" in data

    def test_post_create_category_no_name(self, eagle_online):
        """POST /api/categories/create without name returns 400"""
        code, headers, body = self.do_post(eagle_online, "/api/categories/create", {})
        assert code == 400
        data = json.loads(body)
        assert "name" in data.get("error", "").lower()

    # ── /api/categories/delete ──

    def test_post_delete_category_without_confirm(self, eagle_online):
        """POST /api/categories/delete without confirm returns 400 with cascade info"""
        code, headers, body = self.do_post(eagle_online, "/api/categories/delete", {
            "name": "历史",
        })
        assert code == 400
        data = json.loads(body)
        assert "cascade_confirm_required" in data.get("error", "")
        assert "affected_projects" in data
        assert len(data["affected_projects"]) > 0

    def test_post_delete_category_with_confirm(self, eagle_online):
        """POST /api/categories/delete with confirm=True succeeds"""
        code, headers, body = self.do_post(eagle_online, "/api/categories/delete", {
            "name": "设计",
            "confirm": True,
        })
        assert code == 200
        data = json.loads(body)
        assert data["ok"] is True

    # ── /api/sort/confirm ──

    def test_post_sort_confirm_replace_tags(self, eagle_online, mock_eagle_api):
        """POST /api/sort/confirm with replace_tags=True fully replaces tags"""
        mock_eagle_api.get_item.return_value = {"tags": ["待分类", "旧标签"]}
        code, headers, body = self.do_post(eagle_online, "/api/sort/confirm", {
            "id": "item123",
            "tags": ["新标签"],
            "replace_tags": True,
            "folder": "设计",
        })
        assert code == 200
        data = json.loads(body)
        assert data["ok"] is True
        # Verify replace mode: 待分类 removed, only new tags kept
        mock_eagle_api.update_item.assert_called_once()
        _call_args, call_kwargs = mock_eagle_api.update_item.call_args
        assert call_kwargs["tags"] == ["新标签"]

    def test_post_sort_confirm_merge_tags(self, eagle_online, mock_eagle_api):
        """POST /api/sort/confirm with replace_tags=False merges old + new tags"""
        mock_eagle_api.get_item.return_value = {"tags": ["待分类", "旧标签"]}
        code, headers, body = self.do_post(eagle_online, "/api/sort/confirm", {
            "id": "item456",
            "idempotency_key": "sort_cfm:item456",
            "tags": ["新标签"],
            "replace_tags": False,
            "folder": "设计",
        })
        assert code == 200
        data = json.loads(body)
        assert data["ok"] is True
        # Verify merge mode: old tags (minus 待分类) + new tags
        mock_eagle_api.update_item.assert_called_once()
        _call_args, call_kwargs = mock_eagle_api.update_item.call_args
        assert "旧标签" in call_kwargs["tags"]
        assert "新标签" in call_kwargs["tags"]
        assert "待分类" not in call_kwargs["tags"]

    def test_post_sort_confirm_no_id(self, eagle_online):
        """POST /api/sort/confirm without id returns 400"""
        code, headers, body = self.do_post(eagle_online, "/api/sort/confirm", {})
        assert code == 400

    # ── /api/sort/skip ──

    def test_post_sort_skip(self, eagle_online, mock_eagle_api):
        """POST /api/sort/skip removes 待分类 and adds 已跳过 tag"""
        mock_eagle_api.get_item.return_value = {"tags": ["待分类"]}
        code, headers, body = self.do_post(eagle_online, "/api/sort/skip", {
            "id": "item123",
        })
        assert code == 200
        data = json.loads(body)
        assert data["ok"] is True
        # Verify: 待分类 removed, 已跳过 added
        mock_eagle_api.update_item.assert_called_once()
        _call_args, call_kwargs = mock_eagle_api.update_item.call_args
        assert "待分类" not in call_kwargs["tags"]
        assert "已跳过" in call_kwargs["tags"]

    def test_post_sort_skip_no_id(self, eagle_online):
        """POST /api/sort/skip without id returns 400"""
        code, headers, body = self.do_post(eagle_online, "/api/sort/skip", {})
        assert code == 400

    # ── /api/ai/cache/clear ──

    def test_post_ai_cache_clear(self, eagle_online):
        """POST /api/ai/cache/clear clears AI cache"""
        code, headers, body = self.do_post(eagle_online, "/api/ai/cache/clear", {})
        assert code == 200
        data = json.loads(body)
        assert data["ok"] is True
        assert "cleared_bytes" in data

    # ── /api/set-pinned ──

    def test_post_set_pinned_true(self, eagle_online):
        """POST /api/set-pinned with pinned=True"""
        code, headers, body = self.do_post(eagle_online, "/api/set-pinned", {
            "pinned": True,
        })
        assert code == 200
        data = json.loads(body)
        assert data["ok"] is True
        assert data["pinned"] is True

    def test_post_set_pinned_false(self, eagle_online):
        """POST /api/set-pinned with pinned=False"""
        code, headers, body = self.do_post(eagle_online, "/api/set-pinned", {
            "pinned": False,
        })
        assert code == 200
        data = json.loads(body)
        assert data["ok"] is True
        assert data["pinned"] is False

    # ── /api/action ──

    def test_post_action_open_eagle(self, eagle_online):
        """POST /api/action with action=open-eagle"""
        code, headers, body = self.do_post(eagle_online, "/api/action", {
            "action": "open-eagle",
        })
        assert code == 200
        data = json.loads(body)
        assert data["ok"] is True

    def test_post_action_unknown(self, eagle_online):
        """POST /api/action with unknown action returns 400"""
        code, headers, body = self.do_post(eagle_online, "/api/action", {
            "action": "invalid-action",
        })
        assert code == 400

    # ── watch-dirs add/remove ──

    def test_post_watch_dirs_add_valid(self, handler, mock_data_dir):
        """POST /api/watch-dirs/add 添加有效目录"""
        code, headers, body = self.do_post(handler, "/api/watch-dirs/add",
                                           {"path": str(mock_data_dir)})
        assert code == 200
        data = json.loads(body)
        assert data["ok"] is True

    def test_post_watch_dirs_add_missing_path(self, handler):
        """POST /api/watch-dirs/add 缺少 path 参数返回 400"""
        code, headers, body = self.do_post(handler, "/api/watch-dirs/add", {})
        assert code == 400
        data = json.loads(body)
        assert "path is required" in data["error"]

    def test_post_watch_dirs_add_nonexistent(self, handler):
        """POST /api/watch-dirs/add 不存在的目录返回 400"""
        code, headers, body = self.do_post(handler, "/api/watch-dirs/add",
                                           {"path": "/tmp/nonexistent-xyz-12345"})
        assert code == 400
        data = json.loads(body)
        assert "目录不存在" in data["error"]

    def test_post_watch_dirs_remove_valid(self, handler, mock_data_dir):
        """POST /api/watch-dirs/remove 移除已添加的目录"""
        # 先添加
        self.do_post(handler, "/api/watch-dirs/add", {"path": str(mock_data_dir)})
        # 再移除
        code, headers, body = self.do_post(handler, "/api/watch-dirs/remove",
                                           {"path": str(mock_data_dir)})
        assert code == 200
        data = json.loads(body)
        assert data["removed"] is True

    def test_post_watch_dirs_remove_not_found(self, handler):
        """POST /api/watch-dirs/remove 移除不存在的目录返回 removed=False"""
        code, headers, body = self.do_post(handler, "/api/watch-dirs/remove",
                                           {"path": "/tmp/never-added"})
        assert code == 200
        data = json.loads(body)
        assert data["removed"] is False

    # ── /api/watch-dirs/scan ──

    def test_get_scan_status_idle(self, eagle_online):
        """GET /api/watch-dirs/scan-status 初始返回 idle"""
        code, headers, body = self.do_get(eagle_online, "/api/watch-dirs/scan-status")
        assert code == 200
        data = json.loads(body)
        assert data["status"] == "idle"

    def test_post_scan_missing_path(self, handler):
        """POST /api/watch-dirs/scan 缺少 path 返回 400"""
        code, headers, body = self.do_post(handler, "/api/watch-dirs/scan", {})
        assert code == 400
        data = json.loads(body)
        assert "path is required" in data["error"]

    def test_post_scan_nonexistent_dir(self, handler):
        """POST /api/watch-dirs/scan 不存在的目录返回 400"""
        code, headers, body = self.do_post(handler, "/api/watch-dirs/scan",
                                           {"path": "/tmp/nonexistent-xyz-scan"})
        assert code == 400
        data = json.loads(body)
        assert "目录不存在" in data["error"]

    def test_post_scan_not_temp_dir(self, handler, mock_data_dir):
        """POST /api/watch-dirs/scan 非临时目录返回 400"""
        code, headers, body = self.do_post(handler, "/api/watch-dirs/scan",
                                           {"path": str(mock_data_dir)})
        assert code == 400
        data = json.loads(body)
        assert "只支持扫描临时监控目录" in data["error"]

    def test_post_scan_valid(self, handler, mock_data_dir, monkeypatch, mock_eagle_api):
        """POST /api/watch-dirs/scan 有效临时目录返回 200 并启动扫描"""
        self.do_post(handler, "/api/watch-dirs/add", {"path": str(mock_data_dir)})
        monkeypatch.setattr("eagle_watcher.server.base.create_eagle_api", lambda cfg: mock_eagle_api)
        code, headers, body = self.do_post(handler, "/api/watch-dirs/scan",
                                           {"path": str(mock_data_dir)})
        assert code == 200
        data = json.loads(body)
        assert data["ok"] is True
        assert "扫描已启动" in data["message"]

    # ── unknown POST route ──

    def test_post_unknown_route_returns_404(self, eagle_online):
        """POST /api/nonexistent returns 404"""
        code, headers, body = self.do_post(eagle_online, "/api/nonexistent", {})
        assert code == 404
        data = json.loads(body)
        assert "not found" in data["error"].lower()


class TestErrorAndEdgeCases(PyUIHelpers):
    """Error handling and edge cases"""

    def test_invalid_json_body(self, eagle_online):
        """POST with malformed JSON body returns 400"""
        handler = eagle_online
        handler.command = "POST"
        handler.path = "/import"
        handler.wfile = io.BytesIO()
        handler._headers_buffer = []
        token = _ensure_panel_token()
        handler.headers = {"Content-Length": "4", "X-Session-Token": token}
        handler.rfile = io.BytesIO(b"not{")
        handler.do_POST()

        code, headers, body = _parse_response(handler.wfile.getvalue())
        assert code == 400
        data = json.loads(body)
        assert "invalid json" in data.get("error", "").lower()

    def test_eagle_offline_returns_503(self, eagle_offline):
        """Routes requiring Eagle return 503 when offline"""
        code, headers, body = self.do_get(eagle_offline, "/api/inbox")
        assert code == 503
        data = json.loads(body)
        assert "未运行" in data.get("error", "")

    def test_method_not_allowed(self, eagle_online):
        """PUT /ping returns 405"""
        handler = eagle_online
        handler.command = "PUT"
        handler.path = "/ping"
        handler.wfile = io.BytesIO()
        handler._headers_buffer = []
        handler.headers = {}
        handler.rfile = io.BytesIO(b"")
        handler._route()

        code, headers, body = _parse_response(handler.wfile.getvalue())
        assert code == 405
        data = json.loads(body)
        assert "method not allowed" in data.get("error", "").lower()

    def test_api_status_eagle_offline(self, eagle_offline):
        """_handle_api_status returns 200 with eagle_online=False"""
        code, headers, body = self.do_get(eagle_offline, "/api/status")
        assert code == 200
        data = json.loads(body)
        assert data["eagle_online"] is False
        assert "categories" in data
        assert "projects" in data

    def test_cors_on_all_responses(self, eagle_online):
        """All responses carry Access-Control-Allow-Origin: null (panel server)"""
        paths = ["/ping", "/api/status", "/api/inbox", "/api/history"]
        for path in paths:
            _, headers, _ = self.do_get(eagle_online, path)
            assert headers.get("Access-Control-Allow-Origin") == "null", f"Missing CORS on {path}"

    def test_options_request(self, eagle_online):
        """OPTIONS request returns CORS preflight headers (null origin for panel)"""
        handler = eagle_online
        handler.command = "OPTIONS"
        handler.path = "/"
        handler.wfile = io.BytesIO()
        handler._headers_buffer = []
        handler.headers = {}
        handler.rfile = io.BytesIO(b"")
        handler.do_OPTIONS()

        code, headers, body = _parse_response(handler.wfile.getvalue())
        assert code == 200
        assert headers.get("Access-Control-Allow-Origin") == "null"
        assert headers.get("Access-Control-Allow-Methods") is not None
        assert "GET" in headers["Access-Control-Allow-Methods"]
        assert "POST" in headers["Access-Control-Allow-Methods"]
        assert headers.get("Access-Control-Allow-Headers") is not None


class TestStatusCache(PyUIHelpers):
    """Status cache TTL behavior"""

    def test_cache_is_populated_after_first_call(self, eagle_online):
        """After calling /api/status, _status_cache should have data"""
        self.do_get(eagle_online, "/api/status")
        assert _status_cache["data"] is not None
        assert _status_cache["ts"] > 0

    def test_cache_ttl_respected(self, eagle_online, monkeypatch, mock_eagle_api):
        """Cache serves stale data within TTL, re-fetches after expiry"""
        fake_time = [1000.0]
        monkeypatch.setattr("eagle_watcher.server.panel.time.time", lambda: fake_time[0])

        # First call populates cache
        self.do_get(eagle_online, "/api/status")
        assert _status_cache["ts"] == 1000.0
        ping_calls_before = mock_eagle_api.ping.call_count

        # Advance time just under TTL — cache should serve without calling ping again
        fake_time[0] += _STATUS_CACHE_TTL - 1
        self.do_get(eagle_online, "/api/status")
        # ping() should NOT have been called again
        assert mock_eagle_api.ping.call_count == ping_calls_before

        # Advance time past TTL — should re-fetch
        fake_time[0] += 2
        self.do_get(eagle_online, "/api/status")
        assert mock_eagle_api.ping.call_count > ping_calls_before

    def test_cache_invalidated_on_status_change(self, eagle_online, monkeypatch, mock_eagle_api):
        """Cache timestamp resets on fresh fetch after TTL expiry"""
        fake_time = [1000.0]
        monkeypatch.setattr("eagle_watcher.server.panel.time.time", lambda: fake_time[0])

        # First call
        self.do_get(eagle_online, "/api/status")
        first_ts = _status_cache["ts"]
        assert first_ts == 1000.0

        # Advance past TTL and call again
        fake_time[0] += _STATUS_CACHE_TTL + 1
        self.do_get(eagle_online, "/api/status")
        assert _status_cache["ts"] > first_ts

    def test_get_and_get_api_status_share_cache(self, eagle_online, monkeypatch, mock_eagle_api):
        """GET /status and GET /api/status share the same _status_cache"""
        fake_time = [1000.0]
        monkeypatch.setattr("eagle_watcher.server.panel.time.time", lambda: fake_time[0])

        # Call /status to populate cache
        self.do_get(eagle_online, "/status")
        assert _status_cache["ts"] == 1000.0

        ping_count = mock_eagle_api.ping.call_count

        # Call /api/status within TTL — should use the same cache
        self.do_get(eagle_online, "/api/status")
        assert mock_eagle_api.ping.call_count == ping_count


class TestInboxPagination(PyUIHelpers):
    """Inbox listing with pagination support"""

    def test_inbox_default_pagination(self, eagle_online, mock_eagle_api):
        """Default inbox limit is 50 with offset 0"""
        # Eagle API 服务端分页，只返回 limit 条
        mock_eagle_api.list_items.return_value = [
            {"id": f"item{i}", "name": f"img{i}", "ext": "png",
             "thumbnail": "", "tags": ["待分类"], "btime": 0}
            for i in range(50)
        ]
        code, headers, body = self.do_get(eagle_online, "/api/inbox?limit=50&offset=0")
        assert code == 200
        data = json.loads(body)
        assert len(data["items"]) == 50
        assert data["total"] == 50  # 分页后 total = 当前页大小
        assert data["offset"] == 0
        assert data["limit"] == 50
        assert data["has_more"] is True  # 50 >= 50 → 可能有更多
        mock_eagle_api.list_items.assert_called_with(
            tags="待分类", limit=50, offset=0
        )

    def test_inbox_pagination_middle(self, eagle_online, mock_eagle_api):
        """Inbox with limit=10 offset=20"""
        mock_eagle_api.list_items.return_value = [
            {"id": f"item{i}", "name": f"img{i}", "ext": "png",
             "thumbnail": "", "tags": ["待分类"], "btime": 0}
            for i in range(10)
        ]
        code, headers, body = self.do_get(eagle_online, "/api/inbox?limit=10&offset=20")
        assert code == 200
        data = json.loads(body)
        assert len(data["items"]) == 10
        assert data["offset"] == 20
        assert data["has_more"] is True
        mock_eagle_api.list_items.assert_called_with(
            tags="待分类", limit=10, offset=20
        )

    def test_inbox_pagination_last_page(self, eagle_online, mock_eagle_api):
        """Inbox last page has has_more=False"""
        # 最后一页返回不足 limit 条 → has_more = False
        mock_eagle_api.list_items.return_value = [
            {"id": f"item{i}", "name": f"img{i}", "ext": "png",
             "thumbnail": "", "tags": ["待分类"], "btime": 0}
            for i in range(5)
        ]
        code, headers, body = self.do_get(eagle_online, "/api/inbox?limit=10&offset=20")
        assert code == 200
        data = json.loads(body)
        assert len(data["items"]) == 5
        assert data["has_more"] is False
        mock_eagle_api.list_items.assert_called_with(
            tags="待分类", limit=10, offset=20
        )


# ── /api/watch-dirs ─────────────────────────────────────────────────


class TestWatchDirsEndpoint:

    def test_watch_dirs_returns_list(self, mock_data_dir, handler):
        """GET /api/watch-dirs 返回目录列表"""
        handler.path = "/api/watch-dirs"
        handler.command = "GET"
        handler.do_GET()
        code, headers, body = _parse_response(handler.wfile.getvalue())
        assert code == 200
        data = json.loads(body)
        assert "dirs" in data
        # 至少应包含 downloads 目录（默认配置在 mock_data_dir 环境中）
        assert len(data["dirs"]) >= 1

    def test_watch_dirs_has_type_field(self, mock_data_dir, handler):
        """每个目录包含 type 字段（downloads/extra）"""
        handler.path = "/api/watch-dirs"
        handler.command = "GET"
        handler.do_GET()
        code, headers, body = _parse_response(handler.wfile.getvalue())
        data = json.loads(body)
        for d in data["dirs"]:
            assert "type" in d
            assert "path" in d
            assert "exists" in d


# ── _get_watch_dirs_from_config ─────────────────────────────────────


class TestGetWatchDirsFromConfig:

    def test_returns_downloads(self, mock_data_dir):
        """默认配置至少返回 downloads 目录"""
        from eagle_watcher.server import _get_watch_dirs_from_config
        dirs = _get_watch_dirs_from_config()
        assert len(dirs) >= 1
        assert dirs[0]["type"] == "downloads"

    def test_includes_extra_dirs(self, mock_data_dir):
        """extra_watch_dirs 配置的目录也返回"""
        import yaml
        from eagle_watcher.config import CONFIG_PATH
        # 写入含 extra_watch_dirs 的配置
        cfg = {
            "eagle": {"host": "http://localhost:41595", "token": "x"},
            "paths": {
                "downloads": str(mock_data_dir),
                "extra_watch_dirs": ["/tmp"],
                "watch_interval": 2.0,
            },
        }
        CONFIG_PATH.write_text(yaml.dump(cfg))
        # 重新读取
        from eagle_watcher.server import _get_watch_dirs_from_config
        dirs = _get_watch_dirs_from_config()
        types = [d["type"] for d in dirs]
        assert "downloads" in types
        assert "extra" in types


# ── /api/knowledge ─────────────────────────────────────────────────


class TestKnowledgeAPI(PyUIHelpers):
    """Knowledge base CRUD via HTTP API"""

    def _seed(self):
        from eagle_watcher.knowledge import record_match, _save
        _save({"keywords_mapping": {}, "sources": {}})
        record_match("白起.jpg", "白起", "武安侯", ["战国"])
        record_match("兵马俑.jpg", "兵马俑", "秦始皇", ["秦朝"])

    def test_get_knowledge_list(self, handler, mock_data_dir):
        """GET /api/knowledge returns keyword list with stats"""
        self._seed()
        code, headers, body = self.do_get(handler, "/api/knowledge")
        assert code == 200
        data = json.loads(body)
        assert data["total"] == 2
        assert len(data["items"]) == 2
        assert "stats" in data
        assert data["stats"]["total_keywords"] == 2

    def test_get_knowledge_search(self, handler, mock_data_dir):
        """GET /api/knowledge?search=白起 filters by keyword"""
        self._seed()
        code, _, body = self.do_get(handler, "/api/knowledge?search=白起")
        assert code == 200
        data = json.loads(body)
        assert data["total"] == 1
        assert data["items"][0]["keyword"] == "白起"

    def test_get_knowledge_theme_filter(self, handler, mock_data_dir):
        """GET /api/knowledge?theme=秦始皇 filters by theme"""
        self._seed()
        code, _, body = self.do_get(handler, "/api/knowledge?theme=秦始皇")
        assert code == 200
        data = json.loads(body)
        assert data["total"] == 1
        assert data["items"][0]["theme"] == "秦始皇"

    def test_get_knowledge_pagination(self, handler, mock_data_dir):
        """GET /api/knowledge with page/per_page params"""
        self._seed()
        code, _, body = self.do_get(handler, "/api/knowledge?page=1&per_page=1")
        assert code == 200
        data = json.loads(body)
        assert len(data["items"]) == 1
        assert data["total"] == 2

    def test_post_knowledge_update(self, handler, mock_data_dir):
        """POST /api/knowledge/update modifies keyword theme and tags"""
        self._seed()
        code, _, body = self.do_post(handler, "/api/knowledge/update", {
            "keyword": "白起",
            "theme": "新主题",
            "tags": ["新标签"],
        })
        assert code == 200
        data = json.loads(body)
        assert data["ok"] is True

    def test_post_knowledge_update_missing_keyword(self, handler, mock_data_dir):
        """POST /api/knowledge/update without keyword returns 400"""
        code, _, body = self.do_post(handler, "/api/knowledge/update", {})
        assert code == 400

    def test_post_knowledge_update_nonexistent(self, handler, mock_data_dir):
        """POST /api/knowledge/update for non-existent keyword returns 404"""
        self._seed()
        code, _, body = self.do_post(handler, "/api/knowledge/update", {
            "keyword": "不存在",
            "theme": "T",
        })
        assert code == 404

    def test_post_knowledge_delete(self, handler, mock_data_dir):
        """POST /api/knowledge/delete removes keyword"""
        self._seed()
        code, _, body = self.do_post(handler, "/api/knowledge/delete", {
            "keyword": "白起",
        })
        assert code == 200
        data = json.loads(body)
        assert data["ok"] is True

    def test_post_knowledge_delete_missing_keyword(self, handler, mock_data_dir):
        """POST /api/knowledge/delete without keyword returns 400"""
        code, _, body = self.do_post(handler, "/api/knowledge/delete", {})
        assert code == 400

    def test_post_knowledge_delete_nonexistent(self, handler, mock_data_dir):
        """POST /api/knowledge/delete for non-existent keyword returns 404"""
        self._seed()
        code, _, body = self.do_post(handler, "/api/knowledge/delete", {
            "keyword": "不存在",
        })
        assert code == 404


# ── /api/config/token ──────────────────────────────────────────────


class TestConfigTokenAPI(PyUIHelpers):
    """Token save and Eagle connectivity check"""

    def test_post_config_token_saves(self, eagle_online, mock_data_dir):
        """POST /api/config/token saves token and returns eagle_online status"""
        code, _, body = self.do_post(eagle_online, "/api/config/token", {
            "token": "test-token-123",
        })
        assert code == 200
        data = json.loads(body)
        assert data["ok"] is True
        assert "eagle_online" in data

    def test_post_config_token_empty(self, eagle_online):
        """POST /api/config/token with empty token returns 400"""
        code, _, body = self.do_post(eagle_online, "/api/config/token", {
            "token": "",
        })
        assert code == 400

    def test_post_config_token_missing(self, eagle_online):
        """POST /api/config/token without token field returns 400"""
        code, _, body = self.do_post(eagle_online, "/api/config/token", {})
        assert code == 400


# ── /api/export ─────────────────────────────────────────────────────


class TestExportAPI(PyUIHelpers):
    """Export workspace API endpoints"""

    def _setup_export_dir(self, tmp_path):
        export_dir = tmp_path / "export"
        export_dir.mkdir(exist_ok=True)
        return str(export_dir)

    def test_get_export_status(self, handler, mock_data_dir):
        """GET /api/export/status returns export config and stats"""
        code, _, body = self.do_get(handler, "/api/export/status")
        assert code == 200
        data = json.loads(body)
        assert "enabled" in data
        assert "dir" in data
        assert "file_count" in data

    def test_post_export_config_save(self, handler, mock_data_dir, tmp_path):
        """POST /api/export/config saves export settings"""
        export_dir = self._setup_export_dir(tmp_path)
        code, _, body = self.do_post(handler, "/api/export/config", {
            "enabled": True,
            "dir": export_dir,
            "auto": True,
            "structure": "theme",
        })
        assert code == 200
        data = json.loads(body)
        assert data["ok"] is True

    def test_post_export_config_updates_status(self, handler, mock_data_dir, tmp_path):
        """Saving config then checking status reflects the change"""
        export_dir = self._setup_export_dir(tmp_path)
        self.do_post(handler, "/api/export/config", {
            "enabled": True, "dir": export_dir,
        })
        code, _, body = self.do_get(handler, "/api/export/status")
        data = json.loads(body)
        assert data["enabled"] is True
        assert data["dir"] == export_dir

    def test_post_export_item(self, handler, mock_data_dir, tmp_path):
        """POST /api/export/item exports a single file"""
        export_dir = self._setup_export_dir(tmp_path)
        self.do_post(handler, "/api/export/config", {
            "enabled": True, "dir": export_dir,
        })
        # 创建源文件
        src = tmp_path / "source.jpg"
        src.write_bytes(b"test content")
        code, _, body = self.do_post(handler, "/api/export/item", {
            "file_path": str(src),
            "theme": "武安侯",
            "filename": "source.jpg",
        })
        assert code == 200
        data = json.loads(body)
        assert data["ok"] is True
        assert "exported_to" in data

    def test_post_export_item_missing_fields(self, handler, mock_data_dir):
        """POST /api/export/item without required fields returns 400"""
        code, _, body = self.do_post(handler, "/api/export/item", {})
        assert code == 400

    def test_post_export_item_nonexistent_file(self, handler, mock_data_dir, tmp_path):
        """POST /api/export/item with non-existent file returns 400"""
        export_dir = self._setup_export_dir(tmp_path)
        self.do_post(handler, "/api/export/config", {
            "enabled": True, "dir": export_dir,
        })
        code, _, body = self.do_post(handler, "/api/export/item", {
            "file_path": "/tmp/nonexistent-xyz.jpg",
            "theme": "T",
            "filename": "x.jpg",
        })
        assert code == 400

    def test_post_export_clear_requires_confirm(self, handler, mock_data_dir, tmp_path):
        """POST /api/export/clear without confirm returns 400"""
        export_dir = self._setup_export_dir(tmp_path)
        (Path(export_dir) / "test.txt").write_text("data")
        self.do_post(handler, "/api/export/config", {
            "enabled": True, "dir": export_dir,
        })
        code, _, body = self.do_post(handler, "/api/export/clear", {})
        assert code == 400
        data = json.loads(body)
        assert data["error"] == "confirm_required"
        assert data["file_count"] == 1

    def test_post_export_clear_with_confirm(self, handler, mock_data_dir, tmp_path):
        """POST /api/export/clear with confirm=true clears the export directory"""
        export_dir = self._setup_export_dir(tmp_path)
        (Path(export_dir) / "test.txt").write_text("data")
        self.do_post(handler, "/api/export/config", {
            "enabled": True, "dir": export_dir,
        })
        code, _, body = self.do_post(handler, "/api/export/clear", {"confirm": True})
        assert code == 200
        data = json.loads(body)
        assert data["ok"] is True
        assert data["cleared"] == 1

    def test_post_export_config_with_themes(self, handler, mock_data_dir, tmp_path):
        """POST /api/export/config saves themes filter list"""
        export_dir = self._setup_export_dir(tmp_path)
        code, _, body = self.do_post(handler, "/api/export/config", {
            "enabled": True,
            "dir": export_dir,
            "themes": ["武安侯", "秦始皇"],
        })
        assert code == 200
        # 验证 status 返回中包含 themes_filter
        _, _, status_body = self.do_get(handler, "/api/export/status")
        status = json.loads(status_body)
        assert status["themes_filter"] == ["武安侯", "秦始皇"]

    def test_post_export_theme_missing_param(self, handler, mock_data_dir):
        """POST /api/export/theme without theme returns 400"""
        code, _, body = self.do_post(handler, "/api/export/theme", {})
        assert code == 400

    @patch("eagle_watcher.server.base.create_eagle_api")
    @patch("eagle_watcher.server.panel.export_by_theme")
    def test_post_export_theme_success(self, mock_export, mock_api_factory, handler, mock_data_dir, tmp_path):
        """POST /api/export/theme with valid theme returns result"""
        export_dir = tmp_path / "export"
        export_dir.mkdir(exist_ok=True)
        self.do_post(handler, "/api/export/config", {
            "enabled": True, "dir": str(export_dir),
        })
        mock_api = MagicMock()
        mock_api.ping.return_value = True
        mock_api_factory.return_value = mock_api
        mock_export.return_value = {"exported": 5, "skipped": 2, "error": None}
        code, _, body = self.do_post(handler, "/api/export/theme", {"theme": "武安侯"})
        assert code == 200
        data = json.loads(body)
        assert data["exported"] == 5
        assert data["skipped"] == 2

    def test_post_export_config_rejects_system_dir(self, handler, mock_data_dir):
        """POST /api/export/config rejects system-critical directories"""
        code, _, body = self.do_post(handler, "/api/export/config", {
            "enabled": True, "dir": "/System/Library",
        })
        assert code == 400
        data = json.loads(body)
        assert "系统目录" in data["error"]


# ══════════════════════════════════════════════════════════════
# RemoteHandler Tests
# ══════════════════════════════════════════════════════════════


class TestRemoteHandler:
    """Tests for RemoteHandler (port 9800) — API key auth + endpoints"""

    @pytest.fixture
    def remote_handler(self, mock_data_dir):
        """Create a RemoteHandler instance with minimal HTTP request attributes."""
        from eagle_watcher.server import RemoteHandler
        h = RemoteHandler.__new__(RemoteHandler)
        h.command = "GET"
        h.path = "/ping"
        h.request_version = "HTTP/1.0"
        h.protocol_version = "HTTP/1.0"
        h.headers = {}
        h.rfile = io.BytesIO(b"")
        h.wfile = io.BytesIO()
        h.client_address = ("127.0.0.1", 0)
        h.close_connection = True
        h._headers_buffer = []
        h.requestline = "GET /ping HTTP/1.0"
        h.raw_requestline = b"GET /ping HTTP/1.0"
        return h

    def _do_get(self, handler, path, headers=None):
        handler.command = "GET"
        handler.path = path
        handler.wfile = io.BytesIO()
        handler._headers_buffer = []
        handler.headers = headers or {}
        handler.rfile = io.BytesIO(b"")
        handler.do_GET()
        return _parse_response(handler.wfile.getvalue())

    def _do_post(self, handler, path, body_dict=None, headers=None):
        handler.command = "POST"
        handler.path = path
        handler.wfile = io.BytesIO()
        handler._headers_buffer = []
        body_bytes = json.dumps(body_dict or {}).encode("utf-8")
        h = {"Content-Length": str(len(body_bytes))}
        if headers:
            h.update(headers)
        handler.headers = h
        handler.rfile = io.BytesIO(body_bytes)
        handler.do_POST()
        return _parse_response(handler.wfile.getvalue())

    def test_ping_no_auth_required(self, remote_handler, monkeypatch, mock_eagle_api):
        """/ping 不需要 API Key"""
        monkeypatch.setattr("eagle_watcher.server.remote.create_eagle_api", lambda cfg: mock_eagle_api)
        code, _, body = self._do_get(remote_handler, "/ping")
        assert code == 200
        data = json.loads(body)
        assert data["status"] == "ok"

    def test_check_api_key_no_configured_key(self, remote_handler, mock_data_dir):
        """未配置 api_key 时允许所有请求"""
        handler = remote_handler
        handler.headers = {}
        assert handler._check_api_key() is True

    def test_check_api_key_valid(self, remote_handler, mock_data_dir, monkeypatch):
        """配置了 api_key 且请求携带正确 key → 允许"""
        import yaml
        from eagle_watcher.config import CONFIG_PATH
        cfg = {
            "eagle": {"host": "http://localhost:41595", "token": ""},
            "paths": {"downloads": str(mock_data_dir), "watch_interval": 2.0},
            "server": {"api_key": "secret-key-123"},
        }
        CONFIG_PATH.write_text(yaml.dump(cfg))
        handler = remote_handler
        handler.headers = {"X-API-Key": "secret-key-123"}
        assert handler._check_api_key() is True

    def test_check_api_key_invalid(self, remote_handler, mock_data_dir, monkeypatch):
        """配置了 api_key 但请求携带错误 key → 拒绝"""
        import yaml
        from eagle_watcher.config import CONFIG_PATH
        cfg = {
            "eagle": {"host": "http://localhost:41595", "token": ""},
            "paths": {"downloads": str(mock_data_dir), "watch_interval": 2.0},
            "server": {"api_key": "secret-key-123"},
        }
        CONFIG_PATH.write_text(yaml.dump(cfg))
        handler = remote_handler
        handler.headers = {"X-API-Key": "wrong-key"}
        result = handler._check_api_key()
        assert result is False
        code, _, body = _parse_response(handler.wfile.getvalue())
        assert code == 401

    def test_check_api_key_missing_header(self, remote_handler, mock_data_dir, monkeypatch):
        """配置了 api_key 但请求未携带 key → 拒绝"""
        import yaml
        from eagle_watcher.config import CONFIG_PATH
        cfg = {
            "eagle": {"host": "http://localhost:41595", "token": ""},
            "paths": {"downloads": str(mock_data_dir), "watch_interval": 2.0},
            "server": {"api_key": "secret-key-123"},
        }
        CONFIG_PATH.write_text(yaml.dump(cfg))
        handler = remote_handler
        handler.headers = {}
        result = handler._check_api_key()
        assert result is False

    def test_get_status_with_auth(self, remote_handler, monkeypatch, mock_eagle_api):
        """/status 需要认证（未配置 key 时允许）"""
        monkeypatch.setattr("eagle_watcher.server.remote.create_eagle_api", lambda cfg: mock_eagle_api)
        code, _, body = self._do_get(remote_handler, "/status")
        assert code == 200
        data = json.loads(body)
        assert data["status"] == "ok"
        assert "data" in data

    def test_get_status_eagle_offline(self, remote_handler, monkeypatch):
        """/status Eagle 离线时 eagle_online=False"""
        offline_api = MagicMock()
        offline_api.ping.return_value = False
        monkeypatch.setattr("eagle_watcher.server.remote.create_eagle_api", lambda cfg: offline_api)
        code, _, body = self._do_get(remote_handler, "/status")
        assert code == 200
        data = json.loads(body)
        assert data["data"]["eagle_online"] is False

    def test_get_unknown_route_404(self, remote_handler, monkeypatch, mock_eagle_api):
        """未知 GET 路由返回 404"""
        monkeypatch.setattr("eagle_watcher.server.remote.create_eagle_api", lambda cfg: mock_eagle_api)
        code, _, body = self._do_get(remote_handler, "/unknown")
        assert code == 404

    def test_post_import_no_auth(self, remote_handler, mock_data_dir, monkeypatch, mock_eagle_api):
        """/import 不带认证时被拒绝（配置了 key 时）"""
        import yaml
        from eagle_watcher.config import CONFIG_PATH
        cfg = {
            "eagle": {"host": "http://localhost:41595", "token": ""},
            "paths": {"downloads": str(mock_data_dir), "watch_interval": 2.0},
            "server": {"api_key": "secret"},
        }
        CONFIG_PATH.write_text(yaml.dump(cfg))
        monkeypatch.setattr("eagle_watcher.server.remote.create_eagle_api", lambda cfg: mock_eagle_api)
        code, _, body = self._do_post(remote_handler, "/import", {"file_url": "http://example.com/img.jpg"})
        assert code == 401

    def test_post_import_empty_body(self, remote_handler, monkeypatch, mock_eagle_api):
        """/import 空 body 返回 400"""
        monkeypatch.setattr("eagle_watcher.server.remote.create_eagle_api", lambda cfg: mock_eagle_api)
        handler = remote_handler
        handler.command = "POST"
        handler.path = "/import"
        handler.wfile = io.BytesIO()
        handler._headers_buffer = []
        handler.headers = {"Content-Length": "0"}
        handler.rfile = io.BytesIO(b"")
        handler.do_POST()
        code, _, body = _parse_response(handler.wfile.getvalue())
        assert code == 400
        data = json.loads(body)
        assert "empty body" in data["message"]

    def test_post_import_invalid_json(self, remote_handler, monkeypatch, mock_eagle_api):
        """/import 无效 JSON 返回 400"""
        monkeypatch.setattr("eagle_watcher.server.remote.create_eagle_api", lambda cfg: mock_eagle_api)
        handler = remote_handler
        handler.command = "POST"
        handler.path = "/import"
        handler.wfile = io.BytesIO()
        handler._headers_buffer = []
        body = b"not json"
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile = io.BytesIO(body)
        handler.do_POST()
        code, _, body = _parse_response(handler.wfile.getvalue())
        assert code == 400
        data = json.loads(body)
        assert "invalid JSON" in data["message"]

    def test_post_import_missing_file_url_and_path(self, remote_handler, monkeypatch, mock_eagle_api):
        """/import 缺少 file_url 和 file_path 返回 400"""
        monkeypatch.setattr("eagle_watcher.server.remote.create_eagle_api", lambda cfg: mock_eagle_api)
        code, _, body = self._do_post(remote_handler, "/import", {"project": "test"})
        assert code == 400
        data = json.loads(body)
        assert "file_url or file_path" in data["message"]

    def test_post_import_with_url(self, remote_handler, monkeypatch, mock_eagle_api):
        """/import 使用 file_url 导入成功"""
        monkeypatch.setattr("eagle_watcher.server.remote.create_eagle_api", lambda cfg: mock_eagle_api)
        code, _, body = self._do_post(remote_handler, "/import", {
            "file_url": "http://example.com/img.jpg",
            "project": "测试项目",
            "tags": ["测试"],
        })
        assert code == 200
        data = json.loads(body)
        assert data["status"] == "success"

    def test_post_import_unknown_route(self, remote_handler, monkeypatch, mock_eagle_api):
        """/unknown POST 路由返回 404"""
        monkeypatch.setattr("eagle_watcher.server.remote.create_eagle_api", lambda cfg: mock_eagle_api)
        code, _, body = self._do_post(remote_handler, "/unknown", {})
        assert code == 404

    def test_get_eagle_offline_returns_503(self, remote_handler, monkeypatch):
        """_get_eagle Eagle 离线时返回 503"""
        offline_api = MagicMock()
        offline_api.ping.return_value = False
        monkeypatch.setattr("eagle_watcher.server.remote.create_eagle_api", lambda cfg: offline_api)
        result = remote_handler._get_eagle()
        assert result is None
        code, _, body = _parse_response(remote_handler.wfile.getvalue())
        assert code == 503


# ── 通用工具函数测试 ────────────────────────────────────────────


class TestServerUtilities:
    """Tests for server utility functions"""

    def test_invalidate_status_cache(self):
        """_invalidate_status_cache 清除缓存并重置离线状态"""
        import eagle_watcher.server._common as cm
        cm._status_cache["data"] = {"test": True}
        cm._status_cache["ts"] = 999
        cm._eagle_offline_since = 100

        cm._invalidate_status_cache()
        assert cm._status_cache["data"] is None
        assert cm._eagle_offline_since == 0

    def test_get_status_pool_creates_pool(self):
        """_get_status_pool 首次调用时创建线程池"""
        import eagle_watcher.server._common as cm
        cm._status_pool = None
        pool = cm._get_status_pool()
        assert pool is not None
        pool2 = cm._get_status_pool()
        assert pool is pool2
        pool.shutdown(wait=False)
        cm._status_pool = None

    def test_ensure_panel_token_returns_string(self):
        """_ensure_panel_token 返回非空字符串"""
        import eagle_watcher.server._common as cm
        cm._panel_token_initialized = False
        token = cm._ensure_panel_token()
        assert isinstance(token, str)
        assert len(token) > 0

    def test_ensure_panel_token_idempotent(self):
        """_ensure_panel_token 多次调用返回相同 token"""
        import eagle_watcher.server._common as cm
        cm._panel_token_initialized = False
        token1 = cm._ensure_panel_token()
        token2 = cm._ensure_panel_token()
        assert token1 == token2

    def test_panel_token_refresh_after_ttl(self):
        """Token 超过 TTL 后自动刷新"""
        import eagle_watcher.server._common as cm
        cm._panel_token_initialized = False
        token1 = cm._ensure_panel_token()
        cm._panel_token_created_at = time.time() - cm._PANEL_TOKEN_TTL - 1
        token2 = cm._ensure_panel_token()
        assert token1 != token2
