"""菜单栏 — 极简托盘，只显示项目名"""
import logging
import threading
from pathlib import Path

import rumps

from eagle_watcher.config import get_current_project
from eagle_watcher.pyui.panel import apply_pending_pinned

_LOG = logging.getLogger("menu")


class EagleWatcherMenu(rumps.App):

    def __init__(self):
        super().__init__(self._build_title(), icon=None)
        self._panel = None
        self._http_thread = None

        self.quit_button = None

        self.open_btn = rumps.MenuItem("🪟 打开面板", callback=self._on_open)
        self.quit_btn = rumps.MenuItem("🚪 退出", callback=lambda _: rumps.quit_application())
        self.menu = [self.open_btn, None, self.quit_btn]

        self._start_http()
        rumps.Timer(self._tick, 5).start()

    @staticmethod
    def _build_title() -> str:
        cur = get_current_project()
        return (cur or "自动匹配")[:20]

    def _start_http(self):
        def _run():
            try:
                from eagle_watcher.pyui.server import start_server
                start_server()
            except Exception as e:
                try:
                    from eagle_watcher.notifier import notify
                    notify("素材管家", "⚠️ 面板服务器启动失败")
                except Exception:
                    pass
                _LOG.error("HTTP server error: %s", e)
        self._http_thread = threading.Thread(target=_run, daemon=True)
        self._http_thread.start()

    def _tick(self, _):
        self.title = self._build_title()
        apply_pending_pinned()

    def _on_open(self, _):
        if self._panel is None:
            from eagle_watcher.pyui.panel import FloatingPanel
            self._panel = FloatingPanel()
            _LOG.info("FloatingPanel 已初始化")
        self._panel.toggle()
