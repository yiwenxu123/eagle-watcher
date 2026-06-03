"""Smoke tests for untested modules — import verification + minimal surface checks."""

import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ─────────────────────────────────────────────────────────────────────
# 处理 pyui/panel.py 的可选依赖（PyObjC → AppKit / WebKit）
# ─────────────────────────────────────────────────────────────────────
try:
    import eagle_watcher.pyui.panel  # noqa: F401
    _HAS_PYUI = True
except ImportError:
    _HAS_PYUI = False


# =====================================================================
# cli.py — main() exit behavior, no real Eagle calls
# =====================================================================

class TestCli:
    """eagle_watcher.cli — main() exit behavior"""

    def test_main_help_exits_zero(self):
        """--help prints help and exits 0 via argparse"""
        from eagle_watcher.cli import main
        with patch.object(sys, "argv", ["eagle-import", "--help"]):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0

    def test_main_no_args_exits_one(self):
        """No positional args → sys.exit(1)"""
        from eagle_watcher.cli import main
        with patch.object(sys, "argv", ["eagle-import"]):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 1

    def test_main_module_imports(self):
        """Module-level import does not raise"""
        import eagle_watcher.cli  # noqa: F401

    def test_main_function_is_callable(self):
        """main symbol is a callable function"""
        from eagle_watcher.cli import main
        assert callable(main)


# =====================================================================
# main.py — key functions, no UI/rumps import needed
# =====================================================================

class TestMain:
    """eagle_watcher.main — non-UI entry-point functions"""

    def test_module_imports(self):
        """Module imports on macOS without error"""
        import eagle_watcher.main  # noqa: F401

    def test_first_run_check_returns_false_when_config_exists(self, mock_data_dir):
        """first_run_check returns False when CONFIG_PATH exists"""
        from eagle_watcher.main import first_run_check
        assert first_run_check() is False

    def test_first_run_check_creates_config_when_missing(self, mock_data_dir, monkeypatch):
        """When CONFIG_PATH missing, first_run_check creates default config"""
        from eagle_watcher.config import CONFIG_PATH
        CONFIG_PATH.unlink(missing_ok=True)
        from eagle_watcher.main import first_run_check, CONFIG_PATH as MAIN_CFG_PATH
        # mock_data_dir patches eagle_watcher.config.CONFIG_PATH but
        # main.py captured its own reference at import time; sync it
        monkeypatch.setattr("eagle_watcher.main.CONFIG_PATH", CONFIG_PATH)
        result = first_run_check()
        assert result is False
        assert CONFIG_PATH.exists()

    def test_start_daily_reset_starts_daemon_thread(self, mock_data_dir):
        """start_daily_reset launches a daemon thread named 'daily-reset'"""
        from eagle_watcher.main import start_daily_reset
        names_before = {t.name for t in threading.enumerate()}
        start_daily_reset()
        names_after = {t.name for t in threading.enumerate()}
        assert "daily-reset" in (names_after - names_before)

    def test_function_symbols_exist(self):
        """Key public function references are accessible"""
        import eagle_watcher.main as m
        assert callable(m.start_daily_reset)
        assert callable(m.first_run_check)
        assert callable(m._wait_for_eagle)
        assert callable(m.main)


# =====================================================================
# notifier.py — notify() function
# =====================================================================

class TestNotifier:
    """eagle_watcher.notifier — macOS notification via osascript"""

    def test_notify_calls_osascript(self):
        """notify() invokes osascript via subprocess.run"""
        from eagle_watcher.notifier import notify
        with patch("eagle_watcher.notifier.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            notify("Title", "Message")
        mock_run.assert_called_once()
        args, _ = mock_run.call_args
        assert args[0][0] == "osascript"
        assert 'display notification "Message"' in args[0][-1]

    def test_notify_escapes_special_chars(self):
        """Escapes double quotes and newlines in message"""
        from eagle_watcher.notifier import notify
        with patch("eagle_watcher.notifier.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            notify('Title "with" quotes', 'Line1\nLine2')
        mock_run.assert_called_once()
        args, _ = mock_run.call_args
        safe = args[0][-1]
        assert "\\" in safe or "''" in safe  # some form of escaping applied
        assert "\n" not in safe  # newline replaced

    def test_notify_empty_args_does_not_raise(self):
        """Empty title/message should not crash"""
        from eagle_watcher.notifier import notify
        with patch("eagle_watcher.notifier.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            notify("", "")
        mock_run.assert_called_once()

    def test_notify_file_not_found(self):
        """osascript 不可用时（FileNotFoundError）不崩溃"""
        from eagle_watcher.notifier import notify
        with patch("eagle_watcher.notifier.subprocess.run", side_effect=FileNotFoundError):
            notify("Title", "Message")  # should not raise

    def test_notify_timeout(self):
        """subprocess 超时时不崩溃"""
        import subprocess
        from eagle_watcher.notifier import notify
        with patch("eagle_watcher.notifier.subprocess.run", side_effect=subprocess.TimeoutExpired("osascript", 5)):
            notify("Title", "Message")  # should not raise

    def test_notify_os_error(self):
        """OSError 时不崩溃"""
        from eagle_watcher.notifier import notify
        with patch("eagle_watcher.notifier.subprocess.run", side_effect=OSError("permission denied")):
            notify("Title", "Message")  # should not raise


# =====================================================================
# keychain.py — macOS Keychain get/set/delete
# =====================================================================

class TestKeychain:
    """eagle_watcher.keychain — macOS security CLI wrappers"""

    def test_get_token_returns_empty_on_failure(self):
        """get_token returns '' when security exits non-zero"""
        from eagle_watcher.keychain import get_token
        with patch("eagle_watcher.keychain.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stdout = ""
            assert get_token() == ""

    def test_get_token_returns_token_on_success(self):
        """get_token returns stdout when security exits 0"""
        from eagle_watcher.keychain import get_token
        with patch("eagle_watcher.keychain.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "my-api-token\n"
            assert get_token() == "my-api-token"

    def test_get_token_handles_timeout(self):
        """get_token returns '' when subprocess times out"""
        from eagle_watcher.keychain import get_token
        with patch("eagle_watcher.keychain.subprocess.run", side_effect=TimeoutExpired("cmd", 5)):
            assert get_token() == ""

    def test_set_token_returns_true_on_success(self):
        """set_token returns True when security exits 0"""
        from eagle_watcher.keychain import set_token
        with patch("eagle_watcher.keychain.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            assert set_token("new-token") is True

    def test_set_token_returns_false_on_failure(self):
        """set_token returns False when security exits 1"""
        from eagle_watcher.keychain import set_token
        with patch("eagle_watcher.keychain.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            assert set_token("new-token") is False

    def test_delete_token_returns_bool(self):
        """delete_token returns True on success, False on failure"""
        from eagle_watcher.keychain import delete_token
        with patch("eagle_watcher.keychain.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            assert delete_token() is True
            mock_run.return_value.returncode = 1
            assert delete_token() is False

    def test_module_constants_exist(self):
        """SERVICE and ACCOUNT constants are defined"""
        from eagle_watcher.keychain import SERVICE, ACCOUNT
        assert SERVICE == "eagle-watcher"
        assert ACCOUNT == "eagle-token"


# Need ImportError for timeout test above
try:
    from subprocess import TimeoutExpired
except ImportError:
    pass


# =====================================================================
# services/file_watcher.py — polling watcher + helpers
# =====================================================================

class TestFileWatcherHelpers:
    """eagle_watcher.services.file_watcher — _is_temp, _is_allowed, _wait_for_stable"""

    def test_is_temp_dot_prefix(self):
        """_is_temp returns True for files starting with '.'"""
        from eagle_watcher.services.file_watcher import _is_temp
        assert _is_temp(".DS_Store")
        assert _is_temp(".hidden")
        assert not _is_temp("normal.jpg")

    def test_is_temp_temp_extensions(self):
        """_is_temp returns True for temp download extensions"""
        from eagle_watcher.services.file_watcher import _is_temp
        assert _is_temp("file.crdownload")
        assert _is_temp("file.tmp")
        assert _is_temp("file.part")
        assert not _is_temp("document.pdf")

    def test_is_allowed_common_formats(self):
        """_is_allowed returns True for image/video/design formats"""
        from eagle_watcher.services.file_watcher import _is_allowed
        assert _is_allowed("photo.jpg")
        assert _is_allowed("design.svg")
        assert _is_allowed("video.mp4")
        assert _is_allowed("font.ttf")
        assert _is_allowed("archive.zip")

    def test_is_allowed_rejects_non_design(self):
        """_is_allowed returns False for code/data formats"""
        from eagle_watcher.services.file_watcher import _is_allowed
        assert not _is_allowed("script.py")
        assert not _is_allowed("data.json")
        assert not _is_allowed("notes.txt")
        assert not _is_allowed("sheet.xlsx")

    def test_wait_for_stable_missing_file(self):
        """_wait_for_stable returns False for non-existent file"""
        from eagle_watcher.services.file_watcher import _wait_for_stable
        assert _wait_for_stable("/nonexistent/path/file.png", interval=0.01, checks=2) is False

    def test_wait_for_stable_stable_file(self, tmp_path):
        """_wait_for_stable returns True for a stable file"""
        from eagle_watcher.services.file_watcher import _wait_for_stable
        f = tmp_path / "stable.png"
        f.write_text("content")
        assert _wait_for_stable(str(f), interval=0.01, checks=2) is True

    def test_temp_extensions_constant(self):
        """TEMP_EXTENSIONS is a frozenset of known suffixes"""
        from eagle_watcher.services.file_watcher import TEMP_EXTENSIONS
        assert isinstance(TEMP_EXTENSIONS, frozenset)
        assert ".crdownload" in TEMP_EXTENSIONS
        assert ".tmp" in TEMP_EXTENSIONS


class TestPollingWatcher:
    """eagle_watcher.services.file_watcher — PollingWatcher construction"""

    def test_construct_with_empty_dir(self, tmp_path):
        """PollingWatcher can be instantiated with an empty directory"""
        from eagle_watcher.services.file_watcher import PollingWatcher
        callback = MagicMock()
        watcher = PollingWatcher(str(tmp_path), callback, interval=0.1)
        assert watcher._path == str(tmp_path)
        assert watcher._known_inodes == {}

    def test_construct_prescan_populates_inodes(self, tmp_path):
        """Constructor _scan populates known_inodes from existing files"""
        (tmp_path / "test.jpg").write_text("img")
        (tmp_path / "doc.pdf").write_text("doc")
        from eagle_watcher.services.file_watcher import PollingWatcher
        callback = MagicMock()
        watcher = PollingWatcher(str(tmp_path), callback, interval=0.1)
        assert len(watcher._known_inodes) == 2

    def test_construct_ignores_temp_files(self, tmp_path):
        """Constructor _scan skips .crdownload and .tmp files"""
        (tmp_path / "real.jpg").write_text("img")
        (tmp_path / "partial.crdownload").write_text("partial")
        from eagle_watcher.services.file_watcher import PollingWatcher
        callback = MagicMock()
        watcher = PollingWatcher(str(tmp_path), callback, interval=0.1)
        assert len(watcher._known_inodes) == 1

    def test_start_and_stop(self, tmp_path):
        """start begins polling, stop stops it cleanly"""
        from eagle_watcher.services.file_watcher import PollingWatcher
        callback = MagicMock()
        watcher = PollingWatcher(str(tmp_path), callback, interval=0.1)
        assert watcher._running is False
        watcher.start()
        assert watcher._running is True
        assert watcher._thread is not None
        watcher.stop()
        assert watcher._running is False


class TestCreateWatcher:
    """eagle_watcher.services.file_watcher — create_watcher factory"""

    def test_create_watcher_returns_watcher(self, tmp_path):
        """create_watcher returns a watcher instance"""
        from eagle_watcher.services.file_watcher import create_watcher
        callback = MagicMock()
        watcher = create_watcher(str(tmp_path), callback, poll_interval=0.1)
        assert watcher is not None
        assert hasattr(watcher, "start")
        assert hasattr(watcher, "stop")

    def test_create_watcher_callback_is_preserved(self, tmp_path):
        """The callback passed to create_watcher is wired through"""
        from eagle_watcher.services.file_watcher import create_watcher
        callback = MagicMock()
        watcher = create_watcher(str(tmp_path), callback, poll_interval=0.1)
        assert watcher._callback is callback


# =====================================================================
# services/sort_service.py — SortService class
# =====================================================================

class TestSortService:
    """eagle_watcher.services.sort_service — SortService class"""

    def test_unsorted_tag_constant(self):
        """UNSORTED_TAG = '待分类'"""
        from eagle_watcher.services.sort_service import UNSORTED_TAG
        assert UNSORTED_TAG == "待分类"

    def test_init_stores_eagle_api(self, mock_eagle_api):
        """Constructor stores the EagleAPI instance"""
        from eagle_watcher.services.sort_service import SortService
        svc = SortService(mock_eagle_api)
        assert svc.eagle is mock_eagle_api

    def test_get_inbox_items_delegates_to_eagle(self, mock_eagle_api):
        """get_inbox_items calls eagle.list_items with 待分类 tag"""
        from eagle_watcher.services.sort_service import SortService
        svc = SortService(mock_eagle_api)
        result = svc.get_inbox_items()
        mock_eagle_api.list_items.assert_called_once_with(tags="待分类")
        assert result == []

    def test_analyze_no_match(self, mock_eagle_api):
        """analyze returns empty match when filename doesn't match KB"""
        from eagle_watcher.services.sort_service import SortService
        svc = SortService(mock_eagle_api)
        item = {"name": "test", "ext": "jpg", "id": "1", "tags": []}
        result = svc.analyze(item)
        assert result["suggested_theme"] == "（未匹配）"
        assert result["confidence"] == 0
        assert result["filename"] == "test.jpg"

    def test_analyze_item_shape_preserved(self, mock_eagle_api):
        """analyze preserves the original item dict in result"""
        from eagle_watcher.services.sort_service import SortService
        svc = SortService(mock_eagle_api)
        item = {"id": "abc", "name": "poster", "ext": "png", "tags": ["screenshot"]}
        result = svc.analyze(item)
        assert result["item"] is item

    def test_confirm_calls_update_item(self, mock_eagle_api):
        """confirm calls eagle.update_item with merged tags"""
        from eagle_watcher.services.sort_service import SortService
        svc = SortService(mock_eagle_api)
        mock_eagle_api.update_item.return_value = {"status": "success"}
        item = {"id": "item-1", "tags": ["待分类", "screenshot"], "name": "test", "ext": "jpg"}
        result = svc.confirm(item, "设计", ["海报", "排版"], "test.jpg")
        assert result is True
        mock_eagle_api.update_item.assert_called_once()
        _call_args = mock_eagle_api.update_item.call_args[1]
        assert "待分类" not in _call_args["tags"]
        assert "screenshot" in _call_args["tags"]
        assert "海报" in _call_args["tags"]

    def test_confirm_returns_false_on_api_failure(self, mock_eagle_api):
        """confirm returns False when eagle.update_item fails"""
        from eagle_watcher.services.sort_service import SortService
        svc = SortService(mock_eagle_api)
        mock_eagle_api.update_item.return_value = {"status": "error"}
        item = {"id": "item-1", "tags": ["待分类"], "name": "test", "ext": "jpg"}
        result = svc.confirm(item, "设计", [], "test.jpg")
        assert result is False


# =====================================================================
# server.py — constants, handler classes, start functions
# =====================================================================

class TestServerConstants:
    """eagle_watcher.server — host/port constants"""

    def test_host_and_ports(self):
        """HOST, REMOTE_PORT, PANEL_PORT are correct"""
        from eagle_watcher.server import HOST, REMOTE_PORT, PANEL_PORT
        assert HOST == "127.0.0.1"
        assert REMOTE_PORT == 9800
        assert PANEL_PORT == 9801

    def test_handler_classes_exist(self):
        """BaseHandler, RemoteHandler, PanelHandler are classes"""
        from eagle_watcher.server import BaseHandler, RemoteHandler, PanelHandler
        assert isinstance(BaseHandler, type)
        assert issubclass(RemoteHandler, BaseHandler)
        assert issubclass(PanelHandler, BaseHandler)

    def test_server_functions_are_callable(self):
        """start_remote_server and start_panel_server exist and are callable"""
        from eagle_watcher.server import start_remote_server, start_panel_server, start_server
        assert callable(start_remote_server)
        assert callable(start_panel_server)
        assert start_server is start_remote_server

    def test_module_imports(self):
        """Module imports without error"""
        import eagle_watcher.server  # noqa: F401


# =====================================================================
# pyui/panel.py — FloatingPanel (requires PyObjC)
# =====================================================================

@pytest.mark.skipif(not _HAS_PYUI, reason="PyObjC not available (panel.py requires AppKit/WebKit)")
class TestPanel:
    """eagle_watcher.pyui.panel — FloatingPanel, set_pinned, folder picker"""

    def test_floating_panel_class_exists(self):
        """FloatingPanel class can be imported"""
        from eagle_watcher.pyui.panel import FloatingPanel
        assert isinstance(FloatingPanel, type)
        assert hasattr(FloatingPanel, "__init__")
        assert hasattr(FloatingPanel, "show")
        assert hasattr(FloatingPanel, "hide")
        assert hasattr(FloatingPanel, "toggle")
        assert hasattr(FloatingPanel, "refresh")

    def test_floating_panel_lazy_init(self):
        """FloatingPanel constructor does not create panel (lazy _ensure_panel)"""
        from eagle_watcher.pyui.panel import FloatingPanel
        panel = FloatingPanel()
        assert panel._panel is None
        assert panel._pinned is False
        assert panel._need_refresh is True

    def test_module_constants(self):
        """PANEL_WIDTH, PANEL_HEIGHT, PANEL_URL are defined"""
        from eagle_watcher.pyui.panel import PANEL_WIDTH, PANEL_HEIGHT, PANEL_URL
        assert PANEL_WIDTH == 420
        assert PANEL_HEIGHT == 640
        assert PANEL_URL == "http://localhost:9801/panel"

    def test_module_functions_exist(self):
        """set_pinned, trigger_folder_picker, get_picker_result are callable"""
        from eagle_watcher.pyui.panel import set_pinned, trigger_folder_picker, get_picker_result
        assert callable(set_pinned)
        assert callable(trigger_folder_picker)
        assert callable(get_picker_result)

    def test_set_pinned_updates_pending(self):
        """set_pinned sets module-level _pending_pinned flag (thread-safe)"""
        import eagle_watcher.pyui.panel as panel_mod
        panel_mod._pending_pinned = None
        panel_mod.set_pinned(True)
        assert panel_mod._pending_pinned is True
        panel_mod.set_pinned(False)
        assert panel_mod._pending_pinned is False

    def test_get_picker_result_idle(self):
        """get_picker_result returns ('idle', None) when no picker is pending"""
        import eagle_watcher.pyui.panel as panel_mod
        panel_mod._pending_folder_picker = False
        panel_mod._picker_result = None
        status, path = panel_mod.get_picker_result()
        assert status == "idle"
        assert path is None

    def test_get_picker_result_pending(self):
        """get_picker_result returns ('pending', None) when picker is active"""
        import eagle_watcher.pyui.panel as panel_mod
        panel_mod._pending_folder_picker = True
        panel_mod._picker_result = None
        status, path = panel_mod.get_picker_result()
        assert status == "pending"
        assert path is None

    def test_trigger_folder_picker_sets_pending(self):
        """trigger_folder_picker sets pending flag and clears result"""
        import eagle_watcher.pyui.panel as panel_mod
        panel_mod._pending_folder_picker = False
        panel_mod._picker_result = "/old/path"
        panel_mod.trigger_folder_picker()
        assert panel_mod._pending_folder_picker is True
        assert panel_mod._picker_result is None
