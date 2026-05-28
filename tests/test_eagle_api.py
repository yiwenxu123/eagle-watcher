import json
import pytest
from unittest.mock import patch
from eagle_api import EagleAPI


class TestEagleAPI:

    def test_ping_online(self):
        api = EagleAPI(base_url="http://localhost:41595")
        mock_resp = type("Resp", (), {"status": 200})()
        with patch("urllib.request.urlopen", return_value=mock_resp):
            assert api.ping() is True

    def test_ping_offline(self):
        api = EagleAPI(base_url="http://localhost:41595")
        with patch("urllib.request.urlopen", side_effect=Exception("timeout")):
            assert api.ping() is False

    def test_get_or_create_folder_existing(self):
        api = EagleAPI(base_url="http://localhost:41595")

        def mock_urlopen(req, **kwargs):
            url = str(getattr(req, "full_url", req))
            if "folder/list" in url:
                return type("R", (), {"read": lambda self: json.dumps({
                    "status": "success",
                    "data": [{"id": "abc", "name": "武安侯"}],
                }).encode("utf-8"), "status": 200, "__enter__": lambda s: s, "__exit__": lambda *a: None})()

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            fid = api.get_or_create_folder("武安侯")
            assert fid == "abc"

    def test_update_item_sends_tags(self):
        api = EagleAPI(base_url="http://localhost:41595")
        captured = {}

        def capture(req, **kwargs):
            captured["body"] = json.loads(req.data.decode())
            return type("R", (), {"read": lambda self: json.dumps({"status": "success"}).encode("utf-8"), "status": 200, "__enter__": lambda s: s, "__exit__": lambda *a: None})()

        with patch("urllib.request.urlopen", side_effect=capture):
            api.update_item("item_1", tags=["战国", "白起"])

        assert captured["body"]["id"] == "item_1"
        assert captured["body"]["tags"] == ["战国", "白起"]