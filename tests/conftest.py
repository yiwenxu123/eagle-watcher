import json
import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch


@pytest.fixture(autouse=True)
def mock_data_dir(tmp_path, monkeypatch):
    from config import DATA_DIR as ORIGINAL

    fake_dir = tmp_path / ".eagle-watcher"
    fake_dir.mkdir()
    monkeypatch.setattr("config.DATA_DIR", fake_dir)
    monkeypatch.setattr("services.state_manager.DATA_DIR", fake_dir)
    monkeypatch.setattr("services.state_manager.STATE_PATH", fake_dir / "state.json")
    monkeypatch.setattr("knowledge.DATA_DIR", fake_dir)
    monkeypatch.setattr("knowledge.KNOWLEDGE_PATH", fake_dir / "knowledge.yaml")
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