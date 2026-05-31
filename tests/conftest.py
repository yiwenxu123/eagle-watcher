import json
import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch


@pytest.fixture(autouse=True)
def mock_data_dir(tmp_path, monkeypatch):
    from eagle_watcher.config import DATA_DIR as ORIGINAL

    fake_dir = tmp_path / ".eagle-watcher"
    fake_dir.mkdir()
    monkeypatch.setattr("eagle_watcher.config.DATA_DIR", fake_dir)
    monkeypatch.setattr("eagle_watcher.services.state_manager.DATA_DIR", fake_dir)
    monkeypatch.setattr("eagle_watcher.services.state_manager.STATE_PATH", fake_dir / "state.json")
    monkeypatch.setattr("eagle_watcher.knowledge.DATA_DIR", fake_dir)
    monkeypatch.setattr("eagle_watcher.knowledge.KNOWLEDGE_PATH", fake_dir / "knowledge.yaml")
    return fake_dir


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