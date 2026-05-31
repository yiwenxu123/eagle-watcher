"""浮动 HUD 面板 — NSPanel + WKWebView"""
import logging
from typing import Optional

import AppKit
import WebKit
from Foundation import NSObject, NSURL, NSURLRequest
from WebKit import WKUserContentController, WKUserScript

from eagle_watcher.pyui.server import is_pinned

_LOG = logging.getLogger("pyui")

PANEL_WIDTH = 420
PANEL_HEIGHT = 640
PANEL_URL = "http://localhost:9800/panel"


# NSPanel 子类：允许成为 key window（LSUIElement 模式下需要）
class HUDWindow(AppKit.NSPanel):
    def canBecomeKeyWindow(self):
        return True

    def canBecomeMainWindow(self):
        return False


# JS 控制台桥接
class ConsoleBridge(NSObject):
    def userContentController_didReceiveScriptMessage_(self, controller, message):
        body = message.body()
        _LOG.info("[JS] %s", body)


# 顶部拖拽把手：手动追踪 mouseDragged 移动窗口
# 注：不用 mouseDownCanMoveWindow（PyObjC 下不可靠），直接重写 mouse 事件
class DragHandleView(AppKit.NSView):
    def mouseDown_(self, event):
        self._drag_start = event.locationInWindow()

    def mouseDragged_(self, event):
        panel = self.window()
        if panel is None:
            return
        current = event.locationInWindow()
        dx = current.x - self._drag_start.x
        dy = current.y - self._drag_start.y
        frame = panel.frame()
        panel.setFrameOrigin_(
            AppKit.NSMakePoint(
                frame.origin.x + dx,
                frame.origin.y + dy,
            )
        )
        self._drag_start = current


# 窗口委托：失去 key 状态时自动隐藏（置顶时夺回 key window）
class PanelDelegate(NSObject):
    def windowDidResignKey_(self, notification):
        if is_pinned():
            panel = notification.object()
            AppKit.NSApp.activateIgnoringOtherApps_(True)
            panel.makeKeyAndOrderFront_(None)
            return
        panel = notification.object()
        panel.orderOut_(None)

    def windowShouldClose_(self, sender):
        sender.orderOut_(None)
        return False


class FloatingPanel:
    """浮动 HUD 面板"""  # noqa: D400

    def __init__(self):
        self._panel: Optional[AppKit.NSPanel] = None
        self._webview: Optional[WebKit.WKWebView] = None
        self._delegate: Optional[PanelDelegate] = None

    def _ensure_panel(self):
        if self._panel is not None:
            return

        panel = HUDWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(0, 0, PANEL_WIDTH, PANEL_HEIGHT),
            AppKit.NSWindowStyleMaskTitled |
            AppKit.NSWindowStyleMaskFullSizeContentView |
            AppKit.NSWindowStyleMaskBorderless |
            AppKit.NSWindowStyleMaskClosable |
            AppKit.NSWindowStyleMaskResizable,
            AppKit.NSBackingStoreBuffered,
            False,
        )

        panel.setTitlebarAppearsTransparent_(True)
        panel.setTitleVisibility_(AppKit.NSWindowTitleHidden)
        panel.setFloatingPanel_(True)
        panel.setLevel_(AppKit.NSFloatingWindowLevel)
        panel.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces |
            AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        panel.setOpaque_(False)
        panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        panel.setMovableByWindowBackground_(True)
        panel.setMinSize_(AppKit.NSMakeSize(360, 400))
        panel.setMaxSize_(AppKit.NSMakeSize(800, 1200))

        # 圆角容器
        content = AppKit.NSView.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, 0, PANEL_WIDTH, PANEL_HEIGHT)
        )
        content.setWantsLayer_(True)
        content.layer().setCornerRadius_(16)
        content.layer().setMasksToBounds_(True)
        panel.setContentView_(content)

        # WKWebView
        content_controller = WKUserContentController.alloc().init()
        console_bridge = ConsoleBridge.alloc().init()
        content_controller.addScriptMessageHandler_name_(console_bridge, "pyuiLog")

        inject_script = WKUserScript.alloc().initWithSource_injectionTime_forMainFrameOnly_(
            """
            (function() {
                const orig = { log: console.log, error: console.error, warn: console.warn };
                function bridge(level, args) {
                    try {
                        window.webkit.messageHandlers.pyuiLog.postMessage(
                            '[' + level + '] ' + Array.from(args).join(' ')
                        );
                    } catch(e) {
                        console.warn("[JS bridge] caught exception:", e);
                    }
                    orig[level].apply(console, args);
                }
                console.log = function() { bridge('log', arguments); };
                console.error = function() { bridge('error', arguments); };
                console.warn = function() { bridge('warn', arguments); };
            })();
            """,
            WebKit.WKUserScriptInjectionTimeAtDocumentStart,
            True,
        )
        content_controller.addUserScript_(inject_script)

        config = WebKit.WKWebViewConfiguration.alloc().init()
        config.setUserContentController_(content_controller)

        webview = WebKit.WKWebView.alloc().initWithFrame_configuration_(
            content.bounds(), config
        )
        webview.setAutoresizingMask_(
            AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable
        )
        content.addSubview_(webview)

        # 顶部拖拽把手（覆盖在 WKWebView 之上，18px 高，避开 traffic light 区域）
        drag_handle = DragHandleView.alloc().initWithFrame_(
            AppKit.NSMakeRect(40, PANEL_HEIGHT - 18, PANEL_WIDTH - 40, 18)
        )
        drag_handle.setAutoresizingMask_(
            AppKit.NSViewWidthSizable | AppKit.NSViewMinYMargin
        )
        content.addSubview_(drag_handle)

        url = NSURL.URLWithString_(PANEL_URL)
        req = NSURLRequest.requestWithURL_(url)
        webview.loadRequest_(req)

        # 窗口委托
        delegate = PanelDelegate.alloc().init()
        panel.setDelegate_(delegate)

        self._panel = panel
        self._webview = webview
        self._delegate = delegate
        _LOG.info("FloatingPanel 已创建")

    def show(self):
        self._ensure_panel()
        screen = AppKit.NSScreen.mainScreen().visibleFrame()
        x = screen.size.width - PANEL_WIDTH - 16
        y = screen.size.height - PANEL_HEIGHT - 10
        self._panel.setFrameOrigin_(AppKit.NSMakePoint(x, y))
        # 激活应用 + key + 显示，确保 WKWebView 可交互
        AppKit.NSApp.activateIgnoringOtherApps_(True)
        self._panel.makeKeyAndOrderFront_(None)

    def hide(self):
        if self._panel and self._panel.isVisible():
            self._panel.orderOut_(None)

    def toggle(self):
        if self._panel and self._panel.isVisible():
            self.hide()
        else:
            self.show()

    def refresh(self):
        if self._webview:
            url = NSURL.URLWithString_(PANEL_URL)
            req = NSURLRequest.requestWithURL_(url)
            self._webview.loadRequest_(req)

    def is_visible(self) -> bool:
        return self._panel is not None and self._panel.isVisible()
