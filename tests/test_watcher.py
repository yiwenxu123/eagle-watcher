"""
Tests for eagle_watcher/watcher.py

Covers: _is_processed, _trash_file, _check_result, _process_file,
        _on_file_detected, run_watcher
"""

import os
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from eagle_watcher.services.state_manager import get_state_manager


# ── Helpers ──────────────────────────────────────────────────────────


def _clear_module_state():
    """清理 watcher 的模块级状态，避免测试间泄漏"""
    import eagle_watcher.watcher as watcher
    watcher._processing_files.clear()
    with watcher._retry_lock:
        watcher._retry_queue.clear()


# ── _resolve_watch_dirs ──────────────────────────────────────────────


class TestResolveWatchDirs:

    def test_returns_downloads_from_config(self):
        """仅配置了 downloads → 返回 [downloads]"""
        from eagle_watcher.watcher import _resolve_watch_dirs
        cfg = {"paths": {"downloads": "/tmp", "extra_watch_dirs": []}}
        result = _resolve_watch_dirs(cfg)
        assert "/tmp" in result
        assert len(result) == 1

    def test_merges_extra_dirs(self, mock_data_dir):
        """合并 extra_watch_dirs 配置"""
        from eagle_watcher.watcher import _resolve_watch_dirs
        cfg = {"paths": {"downloads": str(mock_data_dir),
                         "extra_watch_dirs": [str(mock_data_dir)]}}
        result = _resolve_watch_dirs(cfg)
        # downloads 和 extra 指向同一目录 → 去重后 1 个
        assert len(result) == 1

    def test_dedup_same_directory(self, mock_data_dir):
        """downloads 和 extra_dirs 指向相同路径 → 去重"""
        from eagle_watcher.watcher import _resolve_watch_dirs
        cfg = {"paths": {"downloads": str(mock_data_dir), "extra_watch_dirs": []}}
        result = _resolve_watch_dirs(cfg, extra_dirs=[str(mock_data_dir)])
        assert len(result) == 1

    def test_skips_nonexistent_dirs(self):
        """不存在的目录被过滤"""
        from eagle_watcher.watcher import _resolve_watch_dirs
        cfg = {"paths": {"downloads": "", "extra_watch_dirs": ["/tmp/nonexistent-xyz"]}}
        result = _resolve_watch_dirs(cfg)
        assert len(result) == 0

    def test_empty_config_returns_empty(self):
        """空配置返回空列表"""
        from eagle_watcher.watcher import _resolve_watch_dirs
        cfg = {"paths": {}}
        result = _resolve_watch_dirs(cfg)
        assert result == []

    def test_reads_temp_dirs_from_state(self, mock_data_dir):
        """_resolve_watch_dirs 通过 temp_dirs 参数合并临时目录"""
        from eagle_watcher.watcher import _resolve_watch_dirs
        cfg = {"paths": {"downloads": "", "extra_watch_dirs": []}}
        result = _resolve_watch_dirs(cfg, temp_dirs=[str(mock_data_dir)])
        assert str(mock_data_dir) in result

    def test_skips_nonexistent_temp_dirs(self, mock_data_dir):
        """temp_dirs 中不存在的目录被过滤"""
        from eagle_watcher.watcher import _resolve_watch_dirs
        cfg = {"paths": {"downloads": str(mock_data_dir), "extra_watch_dirs": []}}
        result = _resolve_watch_dirs(cfg, temp_dirs=["/tmp/nonexistent-xyz-12345"])
        assert "/tmp/nonexistent-xyz-12345" not in result


# ── _is_processed ────────────────────────────────────────────────────


class TestIsProcessed:

    def test_new_file_returns_false(self, mock_data_dir):
        """新文件 -> mark_file_processed 返回 True -> _is_processed 返回 False"""
        sm = get_state_manager()
        # 创建一个真实存在的文件，让 mark_file_processed 能获取 inode+size
        p = Path(mock_data_dir) / "test_new.jpg"
        p.write_text("hello")

        from eagle_watcher.watcher import _is_processed
        result = _is_processed(str(p))
        assert result is False  # 未处理过

    def test_already_processed_returns_true(self, mock_data_dir):
        """已标记过的文件 -> _is_processed 返回 True"""
        sm = get_state_manager()
        p = Path(mock_data_dir) / "test_dup.jpg"
        p.write_text("hello")

        from eagle_watcher.watcher import _is_processed
        # _is_processed 现在是只读的，需要先手动标记
        sm.mark_file_processed(str(p))
        # 只读检查返回 True
        assert _is_processed(str(p)) is True


# ── _trash_file ──────────────────────────────────────────────────────


class TestTrashFile:

    @patch("AppKit.NSWorkspace")
    def test_trash_normal_path(self, mock_ns, mock_data_dir):
        """正常路径成功移入废纸篓"""
        mock_shared = mock_ns.sharedWorkspace.return_value
        mock_shared.recycleURLs_completionHandler_.return_value = True

        from eagle_watcher.watcher import _trash_file
        result = _trash_file("/Users/test/Downloads/photo.jpg")

        assert result is True
        mock_shared.recycleURLs_completionHandler_.assert_called_once()

    @patch("AppKit.NSWorkspace")
    def test_trash_path_with_quotes(self, mock_ns, mock_data_dir):
        """包含双引号的路径应当被正确传递"""
        mock_shared = mock_ns.sharedWorkspace.return_value
        mock_shared.recycleURLs_completionHandler_.return_value = True

        from eagle_watcher.watcher import _trash_file
        path = '/Users/test/Downloads/my "file".jpg'
        result = _trash_file(path)

        assert result is True
        mock_shared.recycleURLs_completionHandler_.assert_called_once()

    @patch("AppKit.NSWorkspace")
    def test_trash_failure_returns_false(self, mock_ns, mock_data_dir):
        """NSWorkspace 异常 -> _trash_file 返回 False"""
        mock_shared = mock_ns.sharedWorkspace.return_value
        mock_shared.recycleURLs_completionHandler_.side_effect = Exception("Workspace not available")

        from eagle_watcher.watcher import _trash_file
        result = _trash_file("/Users/test/Downloads/bad.jpg")

        assert result is False

    @patch("AppKit.NSWorkspace")
    def test_trash_nonzero_returncode(self, mock_ns, mock_data_dir):
        """NSWorkspace.recycleURLs 返回 False -> False"""
        mock_shared = mock_ns.sharedWorkspace.return_value
        mock_shared.recycleURLs_completionHandler_.return_value = False

        from eagle_watcher.watcher import _trash_file
        result = _trash_file("/Users/test/Downloads/fail.jpg")

        assert result is False


# ── _check_result ────────────────────────────────────────────────────


class TestCheckResult:

    def test_success_path_with_theme(self, mock_data_dir, tmp_path):
        """成功路径（有 theme）: 打印、更新状态、不触发 inbox 通知"""
        _clear_module_state()
        sm = get_state_manager()
        sm.set_inbox_notified_today(False)

        file_path = str(tmp_path / "test_success.jpg")
        Path(file_path).write_text("test")

        from eagle_watcher.watcher import _check_result

        with patch("eagle_watcher.watcher._trash_file", return_value=True) as mock_trash, \
             patch("eagle_watcher.watcher.notify") as mock_notify, \
             patch("eagle_watcher.watcher.load_config") as mock_load_cfg:

            mock_load_cfg.return_value = {"notifications": {"import_success": True}}

            result = {"status": "success"}
            _check_result(result, "test_success.jpg", "武安侯", ["战国", "武将"], file_path)

        # 更新 last_processed
        lp = sm.get_last_processed()
        assert lp is not None
        assert lp["status"] == "success"
        assert lp["theme"] == "武安侯"

        # 文件被移入废纸篓
        mock_trash.assert_called_once_with(file_path)

        # 主题非空 → 走 import_success 通知逻辑
        mock_notify.assert_called_once()
        assert "武安侯" in mock_notify.call_args[0][1]

    def test_success_path_no_theme_triggers_inbox_notification(self, mock_data_dir, tmp_path):
        """成功但无 theme -> inbox 通知（首次）"""
        _clear_module_state()
        sm = get_state_manager()
        sm.set_inbox_notified_today(False)

        file_path = str(tmp_path / "inbox_test.jpg")
        Path(file_path).write_text("test")

        from eagle_watcher.watcher import _check_result

        with patch("eagle_watcher.watcher._trash_file", return_value=True) as mock_trash, \
             patch("eagle_watcher.watcher.notify") as mock_notify:

            result = {"status": "success"}
            _check_result(result, "inbox_test.jpg", "", ["待分类"], file_path)

        # inbox 通知应被触发
        mock_notify.assert_called_once()
        assert "通用箱" in mock_notify.call_args[0][1] or "通用箱" in mock_notify.call_args[0][1]
        # inbox_notified_today 应被设为 True
        assert sm.get_inbox_notified_today() is True

    def test_success_path_no_theme_only_notifies_once(self, mock_data_dir, tmp_path):
        """无 theme + 今日已通知过 -> 不再弹通知"""
        _clear_module_state()
        sm = get_state_manager()
        sm.set_inbox_notified_today(True)

        file_path = str(tmp_path / "inbox_dup.jpg")
        Path(file_path).write_text("test")

        from eagle_watcher.watcher import _check_result

        with patch("eagle_watcher.watcher._trash_file", return_value=True) as mock_trash, \
             patch("eagle_watcher.watcher.notify") as mock_notify:

            result = {"status": "success"}
            _check_result(result, "inbox_dup.jpg", "", ["待分类"], file_path)

        # 不应再通知
        mock_notify.assert_not_called()

    def test_success_no_notifications_setting(self, mock_data_dir, tmp_path):
        """有 theme 但 import_success=False -> 不通知"""
        _clear_module_state()
        sm = get_state_manager()

        file_path = str(tmp_path / "no_notif.jpg")
        Path(file_path).write_text("test")

        from eagle_watcher.watcher import _check_result

        with patch("eagle_watcher.watcher._trash_file", return_value=True), \
             patch("eagle_watcher.watcher.notify") as mock_notify, \
             patch("eagle_watcher.watcher.load_config") as mock_load_cfg:

            mock_load_cfg.return_value = {"notifications": {"import_success": False}}

            result = {"status": "success"}
            _check_result(result, "no_notif.jpg", "武安侯", ["战国"], file_path)

        mock_notify.assert_not_called()

    def test_success_trash_fallback_to_delete(self, mock_data_dir, tmp_path):
        """废纸篓失败 -> 保留原始文件（不再 os.remove 回退）"""
        _clear_module_state()
        file_path = str(tmp_path / "fallback.jpg")
        Path(file_path).write_text("test")

        from eagle_watcher.watcher import _check_result

        with patch("eagle_watcher.watcher._trash_file", return_value=False), \
             patch("eagle_watcher.watcher.os.remove") as mock_remove, \
             patch("eagle_watcher.watcher.notify"), \
             patch("eagle_watcher.watcher.load_config") as mock_cfg:

            mock_cfg.return_value = {"notifications": {"import_success": False}}

            result = {"status": "success"}
            _check_result(result, "fallback.jpg", "武安侯", ["战国"], file_path)

        mock_remove.assert_not_called()

    def test_success_file_not_exists(self, mock_data_dir):
        """文件已不存在 -> 跳过删除"""
        _clear_module_state()
        from eagle_watcher.watcher import _check_result

        with patch("eagle_watcher.watcher._trash_file") as mock_trash, \
             patch("eagle_watcher.watcher.os.remove") as mock_remove, \
             patch("eagle_watcher.watcher.notify"), \
             patch("eagle_watcher.watcher.load_config") as mock_cfg:

            mock_cfg.return_value = {"notifications": {"import_success": False}}

            result = {"status": "success"}
            _check_result(result, "gone.jpg", "武安侯", ["战国"],
                          file_path="/nonexistent/file.jpg")

        mock_trash.assert_not_called()
        mock_remove.assert_not_called()

    def test_failure_path(self, mock_data_dir):
        """失败路径：打印错误、更新状态为 failed"""
        _clear_module_state()
        sm = get_state_manager()

        from eagle_watcher.watcher import _check_result

        result = {"status": "error", "message": "API unavailable"}
        _check_result(result, "fail.jpg", "", [], "")

        lp = sm.get_last_processed()
        assert lp is not None
        assert lp["status"] == "failed"
        assert "API unavailable" in lp.get("error", "")


# ── _process_file ────────────────────────────────────────────────────


class TestProcessFile:

    @patch("eagle_watcher.watcher.decide")
    def test_process_import_action(self, mock_decide, mock_data_dir, mock_eagle_api, tmp_path):
        """action == 'import': 调用 add_from_path + _check_result"""
        _clear_module_state()
        mock_decide.return_value = {
            "action": "import",
            "theme": "武安侯",
            "tags": ["战国", "武将"],
            "folder": "武安侯",
        }

        file_path = str(tmp_path / "白起.jpg")
        Path(file_path).write_text("fake_image")

        from eagle_watcher.watcher import _process_file
        with patch("eagle_watcher.watcher._check_result") as mock_check:
            _process_file(mock_eagle_api, file_path)

        # add_from_path 被调用
        mock_eagle_api.add_from_path.assert_called_once()
        call_kwargs = mock_eagle_api.add_from_path.call_args[1]
        assert call_kwargs["name"] == "白起"
        assert call_kwargs["tags"] == ["战国", "武将"]
        assert call_kwargs["folder_id"] == "mock-folder-id"

        # _check_result 被调用（位置参数：result, filename, theme, tags, file_path）
        mock_check.assert_called_once()
        assert mock_check.call_args[0][1] == "白起.jpg"

    @patch("eagle_watcher.watcher.decide")
    def test_process_inbox_action(self, mock_decide, mock_data_dir, mock_eagle_api, tmp_path):
        """action == 'inbox': 进通用箱，tags 默认 ['待分类']"""
        _clear_module_state()
        mock_decide.return_value = {
            "action": "inbox",
            "theme": None,
            "tags": ["待分类"],
            "folder": None,
        }

        file_path = str(tmp_path / "unknown_art.png")
        Path(file_path).write_text("fake")

        from eagle_watcher.watcher import _process_file
        with patch("eagle_watcher.watcher._check_result") as mock_check:
            _process_file(mock_eagle_api, file_path)

        mock_eagle_api.add_from_path.assert_called_once()
        call_kwargs = mock_eagle_api.add_from_path.call_args[1]
        assert call_kwargs["tags"] == ["待分类"]
        # folder_id 应为 None（decide 返回的 folder 为空）
        assert call_kwargs["folder_id"] is None

    @patch("eagle_watcher.watcher.decide")
    def test_process_ai_analyze_success(self, mock_decide, mock_data_dir, mock_eagle_api, tmp_path):
        """action == 'ai_analyze' + AI 成功 -> 使用 AI tags + name"""
        _clear_module_state()
        mock_decide.return_value = {
            "action": "ai_analyze",
            "theme": None,
            "tags": ["待分类"],
            "folder": None,
        }

        file_path = str(tmp_path / "123456.jpg")
        Path(file_path).write_text("fake")

        from eagle_watcher.watcher import _process_file

        with patch("eagle_watcher.watcher.analyze_image") as mock_ai, \
             patch("eagle_watcher.watcher._check_result") as mock_check:

            mock_ai.return_value = {
                "tags": ["风景", "山水"],
                "name": "山水风景",
            }

            _process_file(mock_eagle_api, file_path)

        # AI tags 被合并到 add_from_path 的 tags 中
        call_kwargs = mock_eagle_api.add_from_path.call_args[1]
        assert "山水风景" == call_kwargs["name"]
        assert "待分类" in call_kwargs["tags"]
        assert "风景" in call_kwargs["tags"]
        assert "山水" in call_kwargs["tags"]

        # _check_result 以 ai_tags 为 tags 参数
        mock_check.assert_called_once()
        check_tags = mock_check.call_args[0][3]
        assert "风景" in check_tags

    @patch("eagle_watcher.watcher.decide")
    def test_process_ai_analyze_failure(self, mock_decide, mock_data_dir, mock_eagle_api, tmp_path):
        """action == 'ai_analyze' + AI 失败 -> 回退到通用箱"""
        _clear_module_state()
        mock_decide.return_value = {
            "action": "ai_analyze",
            "theme": None,
            "tags": ["待分类"],
            "folder": None,
        }

        file_path = str(tmp_path / "1234567890.jpg")
        Path(file_path).write_text("fake")

        from eagle_watcher.watcher import _process_file

        with patch("eagle_watcher.watcher.analyze_image", return_value=None) as mock_ai, \
             patch("eagle_watcher.watcher._check_result") as mock_check:

            _process_file(mock_eagle_api, file_path)

        # AI 失败 -> 以 tags=["待分类"] 调用 add_from_path
        call_kwargs = mock_eagle_api.add_from_path.call_args[1]
        assert call_kwargs["tags"] == ["待分类"] or "待分类" in call_kwargs["tags"]

        # _check_result 被调用（theme=""，tags=["待分类"]）
        mock_check.assert_called_once()
        assert mock_check.call_args[0][2] == ""  # theme is empty string

    @patch("eagle_watcher.watcher.decide")
    def test_process_no_tags_adds_default(self, mock_decide, mock_data_dir, mock_eagle_api, tmp_path):
        """action == 'import' 但 tags 为空 -> 默认 ['待分类']"""
        _clear_module_state()
        mock_decide.return_value = {
            "action": "import",
            "theme": None,
            "tags": [],
            "folder": None,
        }

        file_path = str(tmp_path / "untagged.png")
        Path(file_path).write_text("fake")

        from eagle_watcher.watcher import _process_file
        with patch("eagle_watcher.watcher._check_result"):
            _process_file(mock_eagle_api, file_path)

        call_kwargs = mock_eagle_api.add_from_path.call_args[1]
        assert call_kwargs["tags"] == ["待分类"]


# ── _on_file_detected ────────────────────────────────────────────────


class TestOnFileDetected:

    def setup_method(self):
        _clear_module_state()

    @patch("eagle_watcher.watcher._process_file")
    @patch("eagle_watcher.watcher._is_processed")
    def test_dedup_by_processing_set(self, mock_is_processed, mock_process,
                                     mock_data_dir, mock_eagle_api):
        """_processing_files 中已存在 -> 跳过"""
        import eagle_watcher.watcher as watcher

        file_path = "/path/to/dup.jpg"
        watcher._processing_files.add(file_path)

        watcher._on_file_detected(mock_eagle_api, file_path)

        # _is_processed / _process_file 均不应被调用
        mock_is_processed.assert_not_called()
        mock_process.assert_not_called()

    @patch("eagle_watcher.watcher._process_file")
    def test_calls_process_file(self, mock_process, mock_data_dir, mock_eagle_api, tmp_path):
        """首次检测 -> 调用 _process_file"""
        import eagle_watcher.watcher as watcher
        _clear_module_state()

        # _is_processed 在 test 中通过临时文件绕过
        file_path = str(tmp_path / "new_file.png")
        Path(file_path).write_text("data")

        watcher._on_file_detected(mock_eagle_api, file_path)

        mock_process.assert_called_once_with(mock_eagle_api, file_path)
        # 完成后从 set 中移除
        assert file_path not in watcher._processing_files

    @patch("eagle_watcher.watcher._process_file")
    def test_retry_on_exception(self, mock_process, mock_data_dir, mock_eagle_api, tmp_path):
        """_process_file 抛出异常 -> 加入重试队列"""
        import eagle_watcher.watcher as watcher
        _clear_module_state()

        mock_process.side_effect = ValueError("Something broke")

        file_path = str(tmp_path / "retry_me.png")
        Path(file_path).write_text("data")

        watcher._on_file_detected(mock_eagle_api, file_path, attempt=0)

        # 应加入重试队列
        with watcher._retry_lock:
            assert len(watcher._retry_queue) == 1
            assert watcher._retry_queue[0][0] == file_path
            assert watcher._retry_queue[0][1] == 1  # attempt + 1

    @patch("eagle_watcher.watcher._process_file")
    def test_max_retries_exhausted(self, mock_process, mock_data_dir, mock_eagle_api, tmp_path):
        """超过最大重试次数 -> 不加入重试队列"""
        import eagle_watcher.watcher as watcher
        _clear_module_state()

        mock_process.side_effect = ValueError("Still broken")

        file_path = str(tmp_path / "final_fail.png")
        Path(file_path).write_text("data")

        watcher._on_file_detected(mock_eagle_api, file_path, attempt=watcher._MAX_RETRIES)

        with watcher._retry_lock:
            assert len(watcher._retry_queue) == 0

    @patch("eagle_watcher.watcher._process_file")
    def test_retry_logs_message(self, mock_process, mock_data_dir, mock_eagle_api, tmp_path):
        """重试处理时打印日志"""
        import eagle_watcher.watcher as watcher
        _clear_module_state()

        mock_process.side_effect = ValueError("Retry error")

        file_path = str(tmp_path / "retry_log.png")
        Path(file_path).write_text("data")

        with patch("eagle_watcher.watcher._LOG") as mock_log:
            watcher._on_file_detected(mock_eagle_api, file_path, attempt=1)

        mock_log.info.assert_any_call("重试处理文件：%s（第 %d 次）", "retry_log.png", 1)
        mock_log.warning.assert_any_call(
            "加入重试队列: %s (第 %d 次)", "retry_log.png", 2
        )


# ── run_watcher ──────────────────────────────────────────────────────


class TestRunWatcher:

    def setup_method(self):
        _clear_module_state()

    @patch("eagle_watcher.watcher.create_watcher")
    @patch("eagle_watcher.watcher.load_config")
    def test_run_watcher_creates_watcher(self, mock_load_cfg, mock_create_watcher,
                                         mock_data_dir, mock_eagle_api):
        """run_watcher 创建 watcher 并启动"""
        _clear_module_state()
        mock_load_cfg.return_value = {
            "paths": {
                "downloads": str(mock_data_dir),
                "watch_interval": 1.0,
            },
        }
        mock_watcher = MagicMock()
        mock_create_watcher.return_value = mock_watcher

        from eagle_watcher.watcher import run_watcher

        # 我们需要中断 while 循环，使用 side_effect 模拟 KeyboardInterrupt
        interrupt_after_call = [0]

        def fake_create_watcher(*args, **kwargs):
            interrupt_after_call[0] += 1
            return mock_watcher

        mock_create_watcher.side_effect = fake_create_watcher

        with patch("eagle_watcher.watcher.time.sleep", side_effect=[None, KeyboardInterrupt]):
            run_watcher(mock_eagle_api)

        # create_watcher 被调用
        mock_create_watcher.assert_called_once()
        args, kwargs = mock_create_watcher.call_args
        assert args[0] == str(mock_data_dir)  # downloads 目录

        # watcher 被启动、停止
        mock_watcher.start.assert_called_once()
        mock_watcher.stop.assert_called_once()

    @patch("eagle_watcher.watcher.create_watcher")
    @patch("eagle_watcher.watcher.load_config")
    def test_run_watcher_processes_retry_queue(self, mock_load_cfg, mock_create_watcher,
                                               mock_data_dir, mock_eagle_api, tmp_path):
        """run_watcher 在循环中处理重试队列"""
        _clear_module_state()
        mock_load_cfg.return_value = {
            "paths": {
                "downloads": str(mock_data_dir),
                "watch_interval": 1.0,
            },
        }
        mock_watcher = MagicMock()
        mock_create_watcher.return_value = mock_watcher

        # 预先填充重试队列
        import eagle_watcher.watcher as watcher
        retry_file = str(tmp_path / "retry_in_loop.png")
        Path(retry_file).write_text("retry data")
        with watcher._retry_lock:
            watcher._retry_queue.append((retry_file, 1))

        from eagle_watcher.watcher import run_watcher

        with patch("eagle_watcher.watcher._on_file_detected") as mock_on_detected, \
             patch("eagle_watcher.watcher.time.sleep", side_effect=[None, KeyboardInterrupt]):

            run_watcher(mock_eagle_api)

        # 重试队列被处理，_on_file_detected 以 attempt=1 被调用
        mock_on_detected.assert_any_call(mock_eagle_api, retry_file, attempt=1)

    @patch("eagle_watcher.watcher.create_watcher")
    @patch("eagle_watcher.watcher.load_config")
    def test_run_watcher_skips_nonexistent_retry(self, mock_load_cfg, mock_create_watcher,
                                                 mock_data_dir, mock_eagle_api):
        """重试文件已不存在 -> 跳过并记录警告"""
        _clear_module_state()
        mock_load_cfg.return_value = {
            "paths": {
                "downloads": str(mock_data_dir),
                "watch_interval": 1.0,
            },
        }
        mock_watcher = MagicMock()
        mock_create_watcher.return_value = mock_watcher

        import eagle_watcher.watcher as watcher
        with watcher._retry_lock:
            watcher._retry_queue.append(("/nonexistent/path.jpg", 2))

        from eagle_watcher.watcher import run_watcher

        with patch("eagle_watcher.watcher._on_file_detected") as mock_on_detected, \
             patch("eagle_watcher.watcher.time.sleep", side_effect=[None, KeyboardInterrupt]), \
             patch("eagle_watcher.watcher._LOG") as mock_log:

            run_watcher(mock_eagle_api)

        # _on_file_detected 不应被调用（文件不存在）
        mock_on_detected.assert_not_called()
        mock_log.warning.assert_any_call("重试文件已不存在: %s", "/nonexistent/path.jpg")

    @patch("eagle_watcher.watcher.create_watcher")
    @patch("eagle_watcher.watcher.load_config")
    def test_run_watcher_downloads_not_exist(self, mock_load_cfg, mock_create_watcher,
                                              mock_data_dir, mock_eagle_api):
        """下载目录不存在 -> 提前返回"""
        _clear_module_state()
        nonexistent = str(mock_data_dir / "nonexistent")
        mock_load_cfg.return_value = {
            "paths": {
                "downloads": nonexistent,
                "watch_interval": 1.0,
            },
        }

        from eagle_watcher.watcher import run_watcher

        with patch("eagle_watcher.watcher.ensure_data_dir"):
            run_watcher(mock_eagle_api)

        # watcher 不应被创建
        mock_create_watcher.assert_not_called()

    @patch("eagle_watcher.watcher.create_watcher")
    @patch("eagle_watcher.watcher.load_config")
    def test_callback_invokes_on_file_detected(self, mock_load_cfg, mock_create_watcher,
                                               mock_data_dir, mock_eagle_api):
        """watcher 回调函数调用 _on_file_detected"""
        _clear_module_state()
        # 收集回调
        registered_callback = None

        def capture_callback(*args, **kwargs):
            nonlocal registered_callback
            registered_callback = kwargs.get("callback") or args[1]
            return mock_watcher

        mock_watcher = MagicMock()
        mock_create_watcher.side_effect = capture_callback

        mock_load_cfg.return_value = {
            "paths": {
                "downloads": str(mock_data_dir),
                "watch_interval": 1.0,
            },
        }

        with patch("eagle_watcher.watcher.time.sleep", side_effect=[KeyboardInterrupt]):
            from eagle_watcher.watcher import run_watcher
            run_watcher(mock_eagle_api)

        # 验证回调存在
        assert registered_callback is not None, "create_watcher 应接收到一个 callback 参数"

        # 手动触发回调
        with patch("eagle_watcher.watcher._on_file_detected") as mock_on_detected:
            registered_callback("/some/file.jpg")
            mock_on_detected.assert_called_once_with(mock_eagle_api, "/some/file.jpg")


# ── _reconcile_watchers ──────────────────────────────────


class TestReconcileWatchers:

    def test_starts_watcher_for_temp_dir(self, mock_data_dir):
        """temp_dirs 中有新目录 -> 创建并启动 watcher"""
        from eagle_watcher.watcher import _reconcile_watchers

        mock_watcher = MagicMock()
        watchers = {}
        configured = set()

        def fake_create(*args, **kwargs):
            return mock_watcher

        with patch("eagle_watcher.watcher.create_watcher", side_effect=fake_create):
            _reconcile_watchers(watchers, configured, lambda fp: None, 1.0,
                                temp_dirs=[str(mock_data_dir)])

        assert str(mock_data_dir) in watchers
        mock_watcher.start.assert_called_once()

    def test_stops_watcher_for_removed_temp_dir(self, mock_data_dir):
        """已从 temp_dirs 移除 -> 停止 watcher"""
        from eagle_watcher.watcher import _reconcile_watchers

        mock_watcher = MagicMock()
        watchers = {str(mock_data_dir): mock_watcher}
        configured = set()

        # 不传 temp_dirs → 相当于全部移除
        _reconcile_watchers(watchers, configured, lambda fp: None, 1.0)

        assert str(mock_data_dir) not in watchers
        mock_watcher.stop.assert_called_once()

    def test_skips_nonexistent_temp_dir(self, mock_data_dir):
        """temp_dirs 中有不存在的目录 -> 不创建 watcher"""
        from eagle_watcher.watcher import _reconcile_watchers

        watchers = {}
        configured = set()

        with patch("eagle_watcher.watcher.create_watcher") as mock_create:
            _reconcile_watchers(watchers, configured, lambda fp: None, 1.0,
                                temp_dirs=["/tmp/nonexistent-xyz-12345"])

        mock_create.assert_not_called()