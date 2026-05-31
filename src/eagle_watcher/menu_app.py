"""菜单栏 — 极简托盘，主交互在 HUD 面板"""
import logging
import threading

import rumps

from eagle_watcher.config import get_current_project
from eagle_watcher.services.state_manager import get_state_manager

_LOG = logging.getLogger("menu")


class EagleWatcherMenu(rumps.App):
    """菜单栏托盘 — 显示当前项目 + 打开面板 + 退出"""

    def __init__(self):
        cur = get_current_project()
        super().__init__(f"📁 {cur or '自动匹配'}")
        self._panel = None
        self._http_thread = None

        # 禁用 rumps 自带的 Quit（我们自己加了一个）
        self.quit_button = None

        self.open_btn = rumps.MenuItem("🪟 打开面板", callback=self._on_open)
        self.quit_btn = rumps.MenuItem("🚪 退出", callback=lambda _: rumps.quit_application())

        self.menu = [self.open_btn, None, self.quit_btn]

        self._start_http()
        rumps.Timer(self._tick, 5).start()

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
        sm = get_state_manager()
        eagle_status = "🟢" if sm.get_eagle_online() else "🔴"
        watcher_status = "🟢" if sm.get_watcher_running() else "🔴"
        cur = get_current_project()
        self.title = f"{eagle_status} {watcher_status} {cur or '自动匹配'}"

    def _on_open(self, _):
        if self._panel is None:
            from eagle_watcher.pyui.panel import FloatingPanel
            self._panel = FloatingPanel()
            _LOG.info("FloatingPanel 已初始化")
        self._panel.toggle()
