"""浮动 HUD 面板 — NSPanel + WKWebView"""
import logging
from typing import Optional

import AppKit
import WebKit
from Foundation import NSObject, NSURL, NSURLRequest
from WebKit import WKUserContentController, WKUserScript

_LOG = logging.getLogger("pyui")

# 模块级引用，供 server.py HTTP handler 调用 set_pinned
_current_panel: Optional['FloatingPanel'] = None

# 线程安全的待处理 pin 状态（HTTP handler 存，主线程 _tick 消费）
_pending_pinned: Optional[bool] = None

# 文件夹选择器：HTTP handler 触发，主线程 NSOpenPanel，JS 轮询结果
_pending_folder_picker: bool = False
_picker_result: Optional[str] = None  # None=无, ""=用户取消, path=选中路径

PANEL_WIDTH = 420
PANEL_HEIGHT = 640
PANEL_URL = "http://localhost:9801/panel"


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


# 窗口委托：失去 key 状态时不隐藏（用户通过关闭按钮或 Cmd+W 关闭）
class PanelDelegate(NSObject):
    def windowDidResignKey_(self, notification):
        pass

    def windowShouldClose_(self, sender):
        sender.orderOut_(None)
        return False


# WKWebView 导航委托：页面加载完成后重新聚焦，确保键盘可用
class WebViewDelegate(NSObject):
    def webView_didFinishNavigation_(self, webview, navigation):
        panel = webview.window()
        if panel:
            panel.makeFirstResponder_(webview)


class FloatingPanel:
    """浮动 HUD 面板"""  # noqa: D400

    def __init__(self):
        self._panel: Optional[AppKit.NSPanel] = None
        self._webview: Optional[WebKit.WKWebView] = None
        self._delegate: Optional[PanelDelegate] = None
        self._nav_delegate: Optional[WebViewDelegate] = None
        self._pinned: bool = False
        self._need_refresh: bool = True  # 首次打开或 HTML 变更后需要刷新

    def _ensure_panel(self):
        if self._panel is not None:
            return

        panel = HUDWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(0, 0, PANEL_WIDTH, PANEL_HEIGHT),
            AppKit.NSWindowStyleMaskTitled |
            AppKit.NSWindowStyleMaskFullSizeContentView |
            AppKit.NSWindowStyleMaskClosable |
            AppKit.NSWindowStyleMaskMiniaturizable |
            AppKit.NSWindowStyleMaskResizable,
            AppKit.NSBackingStoreBuffered,
            False,
        )

        panel.setTitlebarAppearsTransparent_(True)
        panel.setTitleVisibility_(AppKit.NSWindowTitleHidden)
        panel.setFloatingPanel_(True)
        panel.setHidesOnDeactivate_(False)
        panel.setLevel_(AppKit.NSNormalWindowLevel)  # 默认普通窗口层级；用户可置顶提升到 NSStatusWindowLevel
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

        # 顶部拖拽把手（左侧避开 traffic light ~70px，右侧留 200px 给操作按钮）
        drag_handle = DragHandleView.alloc().initWithFrame_(
            AppKit.NSMakeRect(70, PANEL_HEIGHT - 32, PANEL_WIDTH - 200, 32)
        )
        drag_handle.setAutoresizingMask_(
            AppKit.NSViewWidthSizable | AppKit.NSViewMinYMargin
        )
        content.addSubview_(drag_handle)

        # 导航委托（页面加载后重新聚焦，确保 WKWebView 键盘可用）
        nav_delegate = WebViewDelegate.alloc().init()
        webview.setNavigationDelegate_(nav_delegate)

        url = NSURL.URLWithString_(PANEL_URL)
        req = NSURLRequest.requestWithURL_(url)
        webview.loadRequest_(req)

        # 窗口委托
        delegate = PanelDelegate.alloc().init()
        panel.setDelegate_(delegate)

        global _current_panel
        self._panel = panel
        self._webview = webview
        self._delegate = delegate
        self._nav_delegate = nav_delegate
        _current_panel = self
        _LOG.info("FloatingPanel 已创建")

    def show(self):
        self._ensure_panel()
        # 首次打开或 HTML 变更后需要刷新页面，否则直接用缓存的
        if self._need_refresh and self._webview:
            self.refresh()
            self._need_refresh = False
        self._panel.setFrameOrigin_(AppKit.NSMakePoint(
            AppKit.NSScreen.mainScreen().visibleFrame().size.width - PANEL_WIDTH - 16,
            AppKit.NSScreen.mainScreen().visibleFrame().size.height - PANEL_HEIGHT - 10,
        ))
        AppKit.NSApp.activateIgnoringOtherApps_(True)
        self._panel.makeKeyAndOrderFront_(None)
        # WKWebView 需要成为 firstResponder 才能接收键盘输入
        self._panel.makeFirstResponder_(self._webview)

    def hide(self):
        if self._panel and self._panel.isVisible():
            self._panel.orderOut_(None)

    def toggle(self):
        if self._panel and self._panel.isVisible():
            if self._panel.isKeyWindow():
                # 已在前台 → 隐藏
                self.hide()
            else:
                # 在后台但可见 → 提到前台
                AppKit.NSApp.activateIgnoringOtherApps_(True)
                self._panel.makeKeyAndOrderFront_(None)
        else:
            self.show()

    def refresh(self):
        if self._webview:
            url = NSURL.URLWithString_(PANEL_URL)
            req = NSURLRequest.requestWithURL_(url)
            self._webview.loadRequest_(req)

    def is_visible(self) -> bool:
        return self._panel is not None and self._panel.isVisible()

    def set_pinned(self, pinned: bool):
        self._pinned = pinned
        if self._panel:
            new_level = AppKit.NSStatusWindowLevel if pinned else AppKit.NSFloatingWindowLevel
            self._panel.setLevel_(new_level)
            self._panel.setHidesOnDeactivate_(not pinned)
            if not pinned:
                AppKit.NSApp.activateIgnoringOtherApps_(True)
                self._panel.makeKeyAndOrderFront_(None)
            _LOG.info("面板置顶: %s (level=%s)", pinned, new_level)


def set_pinned(pinned: bool):
    """HTTP handler 调用的模块级函数（后台线程安全，不调用 AppKit）"""
    global _pending_pinned
    _pending_pinned = pinned


# ────────── 文件夹选择器（NSOpenPanel）──────────

def trigger_folder_picker():
    """HTTP handler 调用：标记需要打开文件夹选择器"""
    global _pending_folder_picker, _picker_result
    _pending_folder_picker = True
    _picker_result = None


def get_picker_result() -> tuple[str, Optional[str]]:
    """HTTP handler 调用：返回 (status, path)。
    status: 'idle' | 'pending' | 'done' | 'cancelled'
    """
    global _picker_result
    if _picker_result is None:
        return ("pending" if _pending_folder_picker else "idle", None)
    path = _picker_result
    _picker_result = None
    if path:
        return ("done", path)
    return ("cancelled", None)


def apply_pending_folder_picker():
    """在主线程调用：如果 pending，打开 NSOpenPanel 让用户选择文件夹"""
    global _pending_folder_picker, _picker_result
    if not _pending_folder_picker:
        return
    _LOG.info("apply_pending_folder_picker: 开始打开 NSOpenPanel")
    try:
        # LSUIElement 模式下必须先激活应用，否则 NSOpenPanel 无法接收事件
        AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        _LOG.info("NSApp activateIgnoringOtherApps 完成")
        panel = AppKit.NSOpenPanel.openPanel()
        panel.setCanChooseFiles_(False)
        panel.setCanChooseDirectories_(True)
        panel.setAllowsMultipleSelection_(False)
        _LOG.info("开始 runModal")
        result = panel.runModal()
        _pending_folder_picker = False  # 在 runModal 之后标记完成，避免 HTTP handler 读到暂态的 idle
        _LOG.info("runModal 返回: %s (NSModalResponseOK=%s)", result, AppKit.NSModalResponseOK)
        if result == AppKit.NSModalResponseOK:
            url = panel.URLs()[0]
            _picker_result = url.path()
            _LOG.info("文件夹选择器: %s", _picker_result)
        else:
            _picker_result = ""
            _LOG.info("文件夹选择器: 用户取消")
    except Exception as e:
        _LOG.error("文件夹选择器错误: %s", e, exc_info=True)
        _pending_folder_picker = False
        _picker_result = ""


def apply_pending_pinned():
    """在主线程调用，消费 _pending_pinned 并实际修改窗口层级"""
    global _pending_pinned
    if _pending_pinned is not None:
        pinned = _pending_pinned
        _pending_pinned = None
        global _current_panel
        if _current_panel is not None:
            _current_panel._pinned = pinned
            if _current_panel._panel:
                new_level = AppKit.NSStatusWindowLevel if pinned else AppKit.NSFloatingWindowLevel
                _current_panel._panel.setLevel_(new_level)
                # 置顶时：不因应用切换而隐藏；取消置顶时：恢复默认
                _current_panel._panel.setHidesOnDeactivate_(not pinned)
                if pinned:
                    # 置顶时提到前台让用户看到效果
                    AppKit.NSApp.activateIgnoringOtherApps_(True)
                    _current_panel._panel.makeKeyAndOrderFront_(None)
                _LOG.info("面板置顶: %s (level=%s)", pinned, new_level)
