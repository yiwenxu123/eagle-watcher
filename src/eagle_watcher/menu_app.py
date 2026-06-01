"""菜单栏 — 托盘显示项目名 + 监控目录状态"""
import logging
import os
import threading
import time
from pathlib import Path

import rumps

from eagle_watcher.config import get_current_project, load_config
from eagle_watcher.pyui.panel import apply_pending_pinned

_LOG = logging.getLogger("menu")


class EagleWatcherMenu(rumps.App):

    def __init__(self):
        super().__init__(self._build_title(), icon=None)
        self._panel = None
        self._http_thread = None
        self._watch_header = rumps.MenuItem("📂 监控目录")
        self._watch_items: list[rumps.MenuItem] = []

        self.quit_button = None

        self.open_btn = rumps.MenuItem("🪟 打开面板", callback=self._on_open)
        self.quit_btn = rumps.MenuItem("🚪 退出", callback=lambda _: rumps.quit_application())
        self.menu = [self.open_btn, None, self._watch_header, self.quit_btn]

        self._update_watch_items()

        self._start_http()
        rumps.Timer(self._tick, 5).start()

    @staticmethod
    def _build_title() -> str:
        cur = get_current_project()
        return (cur or "自动匹配")[:20]

    def _update_watch_items(self):
        cfg = load_config()
        lines = []
        downloads = cfg.get("paths", {}).get("downloads", "")
        if downloads:
            expanded = os.path.expanduser(downloads)
            status = "✅" if Path(expanded).is_dir() else "❌"
            lines.append(f"{status} {expanded}")
        extra = cfg.get("paths", {}).get("extra_watch_dirs", [])
        if isinstance(extra, list):
            for d in extra:
                expanded = os.path.expanduser(d)
                status = "✅" if Path(expanded).is_dir() else "❌"
                lines.append(f"  {status} {expanded}")
        if not lines:
            lines.append("  (无)")

        # 重建 watch 子菜单项
        for item in self._watch_items:
            if item in self.menu:
                self.menu.remove(item)
        self._watch_items.clear()
        for text in lines:
            item = rumps.MenuItem(text)
            self._watch_items.append(item)
            self.menu.insert_after(self._watch_header, item)

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
        if int(time.time()) % 60 < 5:
            self._update_watch_items()

    def _on_open(self, _):
        if self._panel is None:
            from eagle_watcher.pyui.panel import FloatingPanel
            self._panel = FloatingPanel()
            _LOG.info("FloatingPanel 已初始化")
        self._panel.toggle()
