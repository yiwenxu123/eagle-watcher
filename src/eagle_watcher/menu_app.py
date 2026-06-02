"""菜单栏 — 托盘显示项目名 + 监控目录状态"""
import logging
import os
import threading
import time
from pathlib import Path

import rumps

from eagle_watcher.config import get_current_project, load_config
from eagle_watcher.pyui.panel import apply_pending_pinned, apply_pending_folder_picker

_LOG = logging.getLogger("menu")


class EagleWatcherMenu(rumps.App):

    def __init__(self):
        super().__init__(self._build_title(), icon=None)
        self._panel = None
        self._http_thread = None
        self._watch_header_title = "📂 监控目录"
        self._watch_header = rumps.MenuItem(self._watch_header_title)

        self.quit_button = None

        self.open_btn = rumps.MenuItem("🪟 打开面板", callback=self._on_open)
        self.quit_btn = rumps.MenuItem("🚪 退出", callback=lambda _: rumps.quit_application())
        self.menu = [self.open_btn, None, self._watch_header, self.quit_btn]

        # 在 __init__ 中重建一次菜单
        self._update_watch_items()

        self._start_http()
        rumps.Timer(self._tick, 5).start()

    @staticmethod
    def _build_title() -> str:
        cur = get_current_project()
        label = cur or "自动匹配"
        return (label[:19] + "…") if len(label) > 20 else label

    def _update_watch_items(self):
        try:
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

            self.menu.clear()
            self.menu.add(self.open_btn)
            self.menu.add(rumps.separator)
            self.menu.add(self._watch_header)
            for text in lines:
                self.menu.add(rumps.MenuItem(text))
            self.menu.add(rumps.separator)
            self.menu.add(self.quit_btn)
        except Exception as e:
            _LOG.error("重建菜单失败: %s", e, exc_info=True)

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
        try:
            self.title = self._build_title()
        except Exception as e:
            _LOG.error("_tick title 更新失败: %s", e)
        try:
            apply_pending_pinned()
        except Exception as e:
            _LOG.error("_tick pinned 失败: %s", e)
        try:
            apply_pending_folder_picker()
        except Exception as e:
            _LOG.error("_tick picker 失败: %s", e)
        try:
            if int(time.time()) % 60 < 5:
                self._update_watch_items()
        except Exception as e:
            _LOG.error("_tick watch items 失败: %s", e)

    def _on_open(self, _):
        if self._panel is None:
            try:
                from eagle_watcher.pyui.panel import FloatingPanel
                self._panel = FloatingPanel()
                _LOG.info("FloatingPanel 已初始化")
            except Exception as e:
                _LOG.error("面板初始化失败: %s", e)
                return
        self._panel.toggle()