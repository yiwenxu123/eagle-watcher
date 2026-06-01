import json
import pytest
from unittest.mock import patch, MagicMock, ANY
from eagle_watcher.eagle_api import EagleAPI


def _mock_response(data: dict, status: int = 200):
    """Helper: create a mock urllib response that supports `with`."""
    r = MagicMock()
    r.read.return_value = json.dumps(data).encode("utf-8")
    r.status = status
    r.__enter__.return_value = r
    return r


class _MockedOpener:
    """Context-manager helper to patch api._opener.open with a side_effect.

    Usage:
        with _MockedOpener(api) as captured:
            api.some_method(...)
        # captured["body"] / captured["url"] available
    """

    def __init__(self, api, side_effect=None):
        self._api = api
        self._side_effect = side_effect
        self.captured = {}

    def __enter__(self):
        if self._side_effect:
            self._patcher = patch.object(self._api._opener, "open", side_effect=self._side_effect)
        else:
            def default_capture(req, **kwargs):
                self.captured["body"] = json.loads(req.data.decode())
                self.captured["url"] = str(getattr(req, "full_url", req))
                self.captured["method"] = req.method
                return _mock_response({"status": "success"})
            self._patcher = patch.object(self._api._opener, "open", side_effect=default_capture)
        self._mock = self._patcher.start()
        return self.captured

    def __exit__(self, *args):
        self._patcher.stop()
        return False


class TestEagleAPI:

    def test_ping_online(self):
        api = EagleAPI(base_url="http://localhost:41595")
        with patch("urllib.request.urlopen", return_value=_mock_response({"status": "success"})):
            assert api.ping() is True

    def test_ping_offline(self):
        api = EagleAPI(base_url="http://localhost:41595")
        import urllib.error
        with patch("eagle_watcher.eagle_api.EagleAPI._get", side_effect=urllib.error.URLError("timeout")):
            assert api.ping() is False

    def test_get_or_create_folder_existing(self):
        api = EagleAPI(base_url="http://localhost:41595")

        def mock_get(req, **kwargs):
            url = str(getattr(req, "full_url", req))
            if "folder/list" in url:
                return _mock_response({
                    "status": "success",
                    "data": [{"id": "abc", "name": "武安侯"}],
                })
            return _mock_response({"status": "success"})

        with patch.object(api._opener, "open", side_effect=mock_get):
            fid = api.get_or_create_folder("武安侯")
            assert fid == "abc"

    def test_update_item_sends_tags(self):
        api = EagleAPI(base_url="http://localhost:41595")
        with _MockedOpener(api) as captured:
            api.update_item("item_1", tags=["战国", "白起"])

        assert captured["body"]["id"] == "item_1"
        assert captured["body"]["tags"] == ["战国", "白起"]

    # ────────── Wave 1A: new tests ──────────

    def test_get_uses_context_manager(self):
        api = EagleAPI(base_url="http://localhost:41595")
        mock_resp = _mock_response({"status": "success", "data": []})
        with patch.object(api._opener, "open", return_value=mock_resp) as mock_urlopen:
            api._get("folder/list")
        mock_resp.__enter__.assert_called_once()

    def test_post_uses_context_manager(self):
        api = EagleAPI(base_url="http://localhost:41595")
        mock_resp = _mock_response({"status": "success"})
        with patch.object(api._opener, "open", return_value=mock_resp) as mock_urlopen:
            api._post("folder/create", {"folderName": "test"})
        mock_resp.__enter__.assert_called_once()

    def test_get_item_found(self):
        api = EagleAPI(base_url="http://localhost:41595")

        def mock_urlopen(req, **kwargs):
            return _mock_response({
                "status": "success",
                "data": [{"id": "abc123", "name": "test", "tags": ["tag1", "tag2"], "ext": "jpg"}],
            })

        with patch.object(api._opener, "open", side_effect=mock_urlopen):
            item = api.get_item("abc123")

        assert item is not None
        assert item["id"] == "abc123"
        assert item["tags"] == ["tag1", "tag2"]

    def test_get_item_not_found(self):
        api = EagleAPI(base_url="http://localhost:41595")

        def mock_urlopen(req, **kwargs):
            return _mock_response({"status": "success", "data": []})

        with patch.object(api._opener, "open", side_effect=mock_urlopen):
            item = api.get_item("nonexistent")

        assert item is None


class TestEagleAPIAllEndpoints:
    """覆盖所有 EagleAPI 端点和方法参数"""

    # ── add_from_path ──

    def test_add_from_path_all_params(self):
        api = EagleAPI(base_url="http://localhost:41595")
        with _MockedOpener(api) as captured:
            api.add_from_path(
                "/path/to/file.jpg",
                name="My Image",
                tags=["设计", "灵感"],
                folder_id="folder_123",
                annotation="测试注释",
            )

        assert captured["body"]["path"] == "/path/to/file.jpg"
        assert captured["body"]["name"] == "My Image"
        assert captured["body"]["tags"] == ["设计", "灵感"]
        assert captured["body"]["folderId"] == "folder_123"
        assert captured["body"]["annotation"] == "测试注释"
        assert "item/addFromPath" in captured["url"]

    def test_add_from_path_required_only(self):
        api = EagleAPI(base_url="http://localhost:41595")
        with _MockedOpener(api) as captured:
            api.add_from_path("/path/to/file.jpg")

        assert captured["body"]["path"] == "/path/to/file.jpg"
        assert "name" not in captured["body"]
        assert "tags" not in captured["body"]
        assert "folderId" not in captured["body"]
        assert "annotation" not in captured["body"]

    # ── add_from_url ──

    def test_add_from_url_all_params(self):
        api = EagleAPI(base_url="http://localhost:41595")
        with _MockedOpener(api) as captured:
            api.add_from_url(
                "https://example.com/img.jpg",
                name="Web Image",
                tags=["web", "design"],
                folder_id="folder_456",
            )

        assert captured["body"]["url"] == "https://example.com/img.jpg"
        assert captured["body"]["name"] == "Web Image"
        assert captured["body"]["tags"] == ["web", "design"]
        assert captured["body"]["folderId"] == "folder_456"
        assert "item/addFromURL" in captured["url"]

    def test_add_from_url_required_only(self):
        api = EagleAPI(base_url="http://localhost:41595")
        with _MockedOpener(api) as captured:
            api.add_from_url("https://example.com/img.jpg")

        assert captured["body"]["url"] == "https://example.com/img.jpg"
        assert "name" not in captured["body"]
        assert "tags" not in captured["body"]
        assert "folderId" not in captured["body"]

    # ── list_items ──

    def test_list_items_all_filters(self):
        api = EagleAPI(base_url="http://localhost:41595")

        def mock_open(req, **kwargs):
            url = str(getattr(req, "full_url", req))
            assert "folders=folder_123" in url
            assert "tags=%E8%AE%BE%E8%AE%A1" in url
            assert "orderBy=createdTime" in url
            assert "limit=50" in url
            assert "offset=10" in url
            return _mock_response({"status": "success", "data": []})

        with patch.object(api._opener, "open", side_effect=mock_open):
            items = api.list_items(
                folders="folder_123",
                tags="设计",
                order_by="createdTime",
                limit=50,
                offset=10,
            )

        assert items == []

    def test_list_items_no_filters(self):
        api = EagleAPI(base_url="http://localhost:41595")

        def mock_open(req, **kwargs):
            url = str(getattr(req, "full_url", req))
            assert "?" not in url
            return _mock_response({"status": "success", "data": []})

        with patch.object(api._opener, "open", side_effect=mock_open):
            items = api.list_items()

        assert items == []

    # ── list_folders ──

    def test_list_folders(self):
        api = EagleAPI(base_url="http://localhost:41595")
        mock_data = {
            "status": "success",
            "data": [
                {"id": "f1", "name": "设计素材"},
                {"id": "f2", "name": "参考图"},
            ],
        }

        with patch.object(api._opener, "open", return_value=_mock_response(mock_data)):
            folders = api.list_folders()

        assert len(folders) == 2
        assert folders[0]["name"] == "设计素材"
        assert folders[1]["id"] == "f2"

    def test_list_folders_empty(self):
        api = EagleAPI(base_url="http://localhost:41595")

        with patch.object(api._opener, "open", return_value=_mock_response({"status": "success", "data": []})):
            folders = api.list_folders()

        assert folders == []

    # ── create_folder ──

    def test_create_folder(self):
        api = EagleAPI(base_url="http://localhost:41595")
        with _MockedOpener(api) as captured:
            result = api.create_folder("新文件夹")

        assert captured["body"]["folderName"] == "新文件夹"
        assert "folder/create" in captured["url"]
        assert result["status"] == "success"

    # ── delete_folder ──

    def test_delete_folder(self):
        api = EagleAPI(base_url="http://localhost:41595")
        with _MockedOpener(api) as captured:
            result = api.delete_folder("folder_to_delete")

        assert captured["body"]["folderId"] == "folder_to_delete"
        assert "folder/delete" in captured["url"]
        assert result is True

    # ── list_tags ──

    def test_list_tags(self):
        api = EagleAPI(base_url="http://localhost:41595")
        mock_data = {
            "status": "success",
            "data": [
                {"name": "设计", "id": "t1"},
                {"name": "灵感", "id": "t2"},
                {"name": "参考", "id": "t3"},
            ],
        }

        with patch.object(api._opener, "open", return_value=_mock_response(mock_data)):
            tags = api.list_tags()

        assert tags == ["设计", "灵感", "参考"]

    def test_list_tags_empty(self):
        api = EagleAPI(base_url="http://localhost:41595")

        with patch.object(api._opener, "open", return_value=_mock_response({"status": "success", "data": []})):
            tags = api.list_tags()

        assert tags == []

    # ── add_tags_to_item ──

    def test_add_tags_to_item(self):
        api = EagleAPI(base_url="http://localhost:41595")
        with _MockedOpener(api) as captured:
            result = api.add_tags_to_item("item_1", ["战国", "白起"])

        assert captured["body"]["itemId"] == "item_1"
        assert captured["body"]["tags"] == ["战国", "白起"]
        assert "item/addTag" in captured["url"]
        assert result["status"] == "success"

    # ── update_item ──

    def test_update_item_all_params(self):
        api = EagleAPI(base_url="http://localhost:41595")
        with _MockedOpener(api) as captured:
            api.update_item("item_1", tags=["战国"], annotation="测试注释", star=3)

        assert captured["body"]["id"] == "item_1"
        assert captured["body"]["tags"] == ["战国"]
        assert captured["body"]["annotation"] == "测试注释"
        assert captured["body"]["star"] == 3

    def test_update_item_partial_params(self):
        api = EagleAPI(base_url="http://localhost:41595")
        with _MockedOpener(api) as captured:
            api.update_item("item_1", star=5)

        assert captured["body"]["id"] == "item_1"
        assert captured["body"]["star"] == 5
        assert "tags" not in captured["body"]
        assert "annotation" not in captured["body"]

    # ── create_eagle_api 工厂函数 ──

    def test_create_eagle_api_with_config(self):
        from eagle_watcher.eagle_api import create_eagle_api
        api = create_eagle_api({
            "eagle": {"host": "http://custom-host:41595", "token": "test_token"},
        })
        assert api.base_url == "http://custom-host:41595"
        assert api.token == "test_token"

    def test_create_eagle_api_no_config(self, mock_data_dir):
        """不使用 config 参数时自动从 config.yaml 加载"""
        from eagle_watcher.eagle_api import create_eagle_api
        api = create_eagle_api()
        assert api.base_url == "http://localhost:41595"
        assert api.token == ""

    def test_create_eagle_api_no_config_with_token(self, mock_data_dir):
        """config.yaml 中有 token 时也应正确加载"""
        from eagle_watcher.eagle_api import create_eagle_api
        import yaml
        from eagle_watcher.config import CONFIG_PATH
        cfg = yaml.safe_load(open(CONFIG_PATH))
        cfg["eagle"]["token"] = "secret_token"
        with open(CONFIG_PATH, "w") as f:
            yaml.dump(cfg, f)

        api = create_eagle_api()
        assert api.token == "secret_token"