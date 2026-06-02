import json
import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch


@pytest.fixture(autouse=True)
def mock_data_dir(tmp_path, monkeypatch):
    from eagle_watcher.config import DATA_DIR as ORIGINAL

    fake_dir = tmp_path / ".eagle-watcher"
    fake_dir.mkdir(parents=True, exist_ok=True)
    (fake_dir / "cache").mkdir(parents=True, exist_ok=True)

    # 创建测试用 config.yaml
    test_config = {
        "eagle": {"host": "http://localhost:41595", "token": ""},
        "paths": {"downloads": str(Path.home() / "Downloads"), "watch_interval": 2.0},
        "notifications": {"inbox_reminder": True, "import_success": True},
    }
    import yaml
    with open(fake_dir / "config.yaml", "w") as f:
        yaml.dump(test_config, f)

    # 创建测试用 themes.yaml（供 test_decide_paths 等使用）
    test_themes = {
        "categories": {},
        "projects": {
            "武安侯": {"category": "历史", "default_tags": ["战国", "武将"], "created_at": "2025-01-01"},
            "秦始皇": {"category": "历史", "default_tags": ["秦朝", "文物"], "created_at": "2025-01-01"},
            "海报参考": {"category": "设计", "default_tags": ["海报"], "created_at": "2025-01-01"},
        },
    }
    with open(fake_dir / "themes.yaml", "w") as f:
        yaml.dump(test_themes, f)

    monkeypatch.setattr("eagle_watcher.config.DATA_DIR", fake_dir)
    monkeypatch.setattr("eagle_watcher.config.CONFIG_PATH", fake_dir / "config.yaml")
    monkeypatch.setattr("eagle_watcher.config.THEMES_PATH", fake_dir / "themes.yaml")
    monkeypatch.setattr("eagle_watcher.config.KNOWLEDGE_PATH", fake_dir / "knowledge.yaml")
    monkeypatch.setattr("eagle_watcher.services.state_manager.DATA_DIR", fake_dir)
    monkeypatch.setattr("eagle_watcher.services.state_manager.STATE_PATH", fake_dir / "state.json")
    monkeypatch.setattr("eagle_watcher.knowledge.DATA_DIR", fake_dir)
    monkeypatch.setattr("eagle_watcher.knowledge.KNOWLEDGE_PATH", fake_dir / "knowledge.yaml")
    monkeypatch.setattr("eagle_watcher.ai_tagger.CACHE_DIR", fake_dir / "cache")

    # 重置 server 模块的 Eagle 离线状态缓存，避免跨测试污染
    import eagle_watcher.server as sv
    sv._eagle_offline_since = 0

    return fake_dir


@pytest.fixture
def mock_eagle_api():
    """返回一个 mock EagleAPI 实例，所有方法返回安全默认值。"""
    api = MagicMock()
    api.ping.return_value = True
    api.list_items.return_value = []
    api.update_item.return_value = {"status": "success"}
    api.list_folders.return_value = []
    api.get_or_create_folder.return_value = "mock-folder-id"
    api.add_from_path.return_value = {"status": "success"}
    api.add_from_url.return_value = {"status": "success"}
    return api


@pytest.fixture
def mock_config():
    """返回一个可控的测试用配置字典。"""
    return {
        "eagle": {"host": "http://localhost:41595", "token": ""},
        "paths": {"downloads": str(Path.home() / "Downloads"), "watch_interval": 2.0},
        "notifications": {"inbox_reminder": True, "import_success": True},
        "ai": {"api_key": ""},
    }


class MockHTTPResponse:
    def __init__(self, json_data: dict, status: int = 200):
        self._json_data = json_data
        self.status = status
    def read(self):
        return json.dumps(self._json_data).encode("utf-8")
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass