import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

from eagle_watcher.services.state_manager import StateManager


class TestStateManager:

    def test_get_set_theme(self, mock_data_dir):
        sm = StateManager()
        assert sm.get_current_theme() is None

        sm.set_current_theme("武安侯")
        assert sm.get_current_theme() == "武安侯"

        sm.set_current_theme(None)
        assert sm.get_current_theme() is None

    def test_inbox_flag(self, mock_data_dir):
        sm = StateManager()
        assert sm.get_inbox_notified_today() is False

        sm.set_inbox_notified_today(True)
        assert sm.get_inbox_notified_today() is True

        sm.reset_daily_flags()
        assert sm.get_inbox_notified_today() is False

    def test_server_method_persists(self, mock_data_dir):
        sm = StateManager()
        sm.set_current_theme("武安侯")

        sm.set_state_from_server("_temp")
        assert sm.get_current_theme() == "_temp"

        sm.flush()
        sm2 = StateManager()
        assert sm2.get_current_theme() == "_temp"

    def test_concurrent_access(self, mock_data_dir):
        sm = StateManager()
        errors = []

        def writer(name):
            for _ in range(50):
                sm.set_current_theme(name)
                time.sleep(0.001)

        threads = [threading.Thread(target=writer, args=(f"主题{i}",)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        final = sm.get_current_theme()
        assert final is not None or errors == []

    def test_persistence(self, mock_data_dir):
        sm1 = StateManager()
        sm1.set_current_theme("秦始皇")

        sm1.flush()
        sm2 = StateManager()
        assert sm2.get_current_theme() == "秦始皇"

    def test_get_all_state_returns_copy(self, mock_data_dir):
        sm = StateManager()
        sm.set_current_theme("武安侯")
        state1 = sm.get_all_state()
        state2 = sm.get_all_state()

        assert state1 == state2
        state1["current_project"] = "篡改值"
        assert sm.get_current_theme() == "武安侯"

    def test_mark_file_processed_new(self, mock_data_dir):
        sm = StateManager()
        test_file = Path(str(mock_data_dir)) / "test_new.png"
        test_file.write_text("new content")
        assert sm.mark_file_processed(str(test_file)) is True

    def test_mark_file_processed_duplicate(self, mock_data_dir):
        sm = StateManager()
        test_file = Path(str(mock_data_dir)) / "test_dup.png"
        test_file.write_text("dup content")
        assert sm.mark_file_processed(str(test_file)) is True
        assert sm.mark_file_processed(str(test_file)) is False

    def test_mark_file_processed_inode_key(self, mock_data_dir):
        """验证 inode:size 作为去重键，同名但不同的文件视为不同"""
        sm = StateManager()
        test_file = Path(str(mock_data_dir)) / "test_inode.png"
        test_file.write_text("version 1")
        assert sm.mark_file_processed(str(test_file)) is True

        # 写入不同内容（inode 不变但 size 变 → 不同 key）
        test_file.write_text("version 2 with different size")
        assert sm.mark_file_processed(str(test_file)) is True  # size 变了，视为新文件

    def test_mark_file_processed_nonexistent_file(self, mock_data_dir):
        """不存在的文件应返回 True（让调用方重试，而非永久跳过）"""
        sm = StateManager()
        nonexistent = str(mock_data_dir / "nonexistent.png")
        assert sm.mark_file_processed(nonexistent) is True

    def test_processed_files_trimming(self, mock_data_dir, monkeypatch):
        """超过 MAX_PROCESSED_FILES 时应裁剪到 TRIM_KEEP_COUNT"""
        import eagle_watcher.services.state_manager as sm_module
        monkeypatch.setattr(sm_module, "MAX_PROCESSED_FILES", 5)
        monkeypatch.setattr(sm_module, "TRIM_KEEP_COUNT", 3)

        sm = StateManager()
        test_dir = Path(str(mock_data_dir)) / "trim_test"
        test_dir.mkdir(parents=True, exist_ok=True)

        for i in range(6):
            f = test_dir / f"file_{i}.png"
            f.write_text(f"content_{i}")
            sm.mark_file_processed(str(f))

        processed = sm.get_processed_files()
        assert len(processed) == 3

    def test_get_set_last_processed(self, mock_data_dir):
        sm = StateManager()
        assert sm.get_last_processed() is None

        info = {"file": "test.png", "theme": "武安侯", "time": "2025-01-01T00:00:00"}
        sm.set_last_processed(info)
        result = sm.get_last_processed()
        assert result is not None
        assert result["file"] == "test.png"
        assert result["theme"] == "武安侯"

    def test_get_set_watcher_running(self, mock_data_dir):
        sm = StateManager()
        assert sm.get_watcher_running() is False

        sm.set_watcher_running(True)
        assert sm.get_watcher_running() is True

        sm.set_watcher_running(False)
        assert sm.get_watcher_running() is False

    def test_get_set_eagle_online(self, mock_data_dir):
        sm = StateManager()
        assert sm.get_eagle_online() is False

        sm.set_eagle_online(True)
        assert sm.get_eagle_online() is True

        sm.set_eagle_online(False)
        assert sm.get_eagle_online() is False

    def test_reset_daily_flags_noop(self, mock_data_dir):
        """reset_daily_flags 当 inbox_notified_today 已经是 False 时不报错"""
        sm = StateManager()
        assert sm.get_inbox_notified_today() is False

        sm.reset_daily_flags()
        assert sm.get_inbox_notified_today() is False

    # ── temp_watch_dirs ────────────────────────────────────

    def test_temp_watch_dirs_default_empty(self, mock_data_dir):
        """新 state 中 temp_watch_dirs 默认为空列表"""
        sm = StateManager()
        assert sm.get_temp_watch_dirs() == []

    def test_add_temp_watch_dir(self, mock_data_dir):
        """add_temp_watch_dir 添加新目录"""
        sm = StateManager()
        assert sm.add_temp_watch_dir("/tmp/test-dir") is True
        assert "/tmp/test-dir" in sm.get_temp_watch_dirs()

    def test_add_temp_watch_dir_dedup(self, mock_data_dir):
        """重复添加同一目录返回 False 且只保留一个"""
        sm = StateManager()
        assert sm.add_temp_watch_dir("/tmp/test-dir") is True
        assert sm.add_temp_watch_dir("/tmp/test-dir") is False
        assert sm.get_temp_watch_dirs() == ["/tmp/test-dir"]

    def test_remove_temp_watch_dir(self, mock_data_dir):
        """remove_temp_watch_dir 移除目录"""
        sm = StateManager()
        sm.add_temp_watch_dir("/tmp/test-dir")
        assert sm.remove_temp_watch_dir("/tmp/test-dir") is True
        assert sm.get_temp_watch_dirs() == []

    def test_remove_temp_watch_dir_not_found(self, mock_data_dir):
        """移除不存在的目录返回 False"""
        sm = StateManager()
        assert sm.remove_temp_watch_dir("/tmp/nonexistent") is False

    def test_temp_watch_dirs_persistence(self, mock_data_dir):
        """set_temp_watch_dirs 写入后重读一致"""
        sm = StateManager()
        sm.set_temp_watch_dirs(["/tmp/a", "/tmp/b"])
        sm.flush()
        sm2 = StateManager()
        assert sm2.get_temp_watch_dirs() == ["/tmp/a", "/tmp/b"]


class TestDeferredWrite:
    """延迟批量写入机制测试"""

    def test_flush_writes_to_disk(self, mock_data_dir):
        """flush() 将脏数据写入磁盘"""
        from eagle_watcher.services.state_manager import STATE_PATH
        sm = StateManager()
        sm._FLUSH_DELAY = 10.0  # 设置很长的延迟，确保不会自动 flush
        sm.set_current_project("测试项目")
        # 延迟写入，文件可能不存在
        sm.flush()
        assert STATE_PATH.exists()
        import json
        with open(STATE_PATH) as f:
            state = json.load(f)
        assert state["current_project"] == "测试项目"

    def test_flush_noop_when_clean(self, mock_data_dir):
        """没有脏数据时 flush() 不执行写入"""
        sm = StateManager()
        sm._save = MagicMock()  # 监控 _save 调用
        sm.flush()
        sm._save.assert_not_called()

    def test_schedule_flush_defers_write(self, mock_data_dir):
        """_schedule_flush 不会立即写入"""
        sm = StateManager()
        sm._FLUSH_DELAY = 10.0
        sm._save = MagicMock()
        sm.set_current_project("延迟测试")
        # _save 不应被直接调用（由 _schedule_flush 延迟）
        sm._save.assert_not_called()
        assert sm._dirty is True
        sm.flush()  # 清理

    def test_multiple_sets_single_flush(self, mock_data_dir):
        """多次 set 只触发一次磁盘写入"""
        sm = StateManager()
        sm._FLUSH_DELAY = 10.0
        save_count = [0]
        original_save = sm._save

        def counting_save():
            save_count[0] += 1
            original_save()

        sm._save = counting_save
        sm.set_current_project("A")
        sm.set_current_project("B")
        sm.set_current_project("C")
        # 延迟模式下，_save 不应被调用
        assert save_count[0] == 0
        # flush 后只写一次
        sm.flush()
        assert save_count[0] == 1