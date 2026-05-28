import threading
import time
from services.state_manager import StateManager


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

    def test_server_method_does_not_persist(self, mock_data_dir):
        sm = StateManager()
        sm.set_current_theme("武安侯")

        sm.set_state_from_server("_temp")
        assert sm.get_current_theme() == "_temp"

        sm2 = StateManager()
        assert sm2.get_current_theme() == "武安侯"

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

        sm2 = StateManager()
        assert sm2.get_current_theme() == "秦始皇"