"""The Recent Recordings window.

A WKWebView in a plain NSWindow: the list is content, and laying it out as
a document gives the transcripts room to breathe in a way a menu of
truncated single lines cannot.
"""

import logging

import AppKit
import objc
import WebKit
from Foundation import NSObject

from src.history_menu import reveal_in_finder
from src.recordings_view import build_items, render_html

logger = logging.getLogger(__name__)

WIDTH, HEIGHT = 560, 640
HANDLER_NAME = "vibepaste"


class _Bridge(NSObject):
    """Receives the window's button presses."""

    def initWithItems_(self, items):
        self = objc.super(_Bridge, self).init()
        if self is None:
            return None
        self._items = items
        return self

    def userContentController_didReceiveScriptMessage_(self, controller, message):
        try:
            body = message.body()
            index = int(body["index"])
            item = self._items[index]
        except (KeyError, IndexError, TypeError, ValueError) as e:
            logger.error(f"Ignoring malformed window message: {e}")
            return

        if body.get("action") == "copy":
            self._put_on_clipboard(item["text"])
        else:
            reveal_in_finder(item["path"])

    def _put_on_clipboard(self, text):
        # Not named _copy: ObjC gives copy-prefixed selectors special
        # memory-management meaning and PyObjC rejects the signature.
        try:
            import pyperclip

            pyperclip.copy(text)
        except Exception as e:
            logger.error(f"Copy failed: {e}")


class RecordingsWindow:
    """Shows the recordings list, reusing one window."""

    def __init__(self):
        self._window = None
        self._bridge = None
        self._webview = None

    def show(self, store):
        """Open the window, or bring it forward with fresh contents."""
        try:
            items = build_items(store)
            html = render_html(items)
        except Exception as e:
            logger.error(f"Could not build recordings window: {e}", exc_info=True)
            return

        if self._window is None:
            self._build_window()
        # The bridge is rebuilt with each render so its indices always match
        # the list currently on screen.
        self._install_bridge(items)
        self._webview.loadHTMLString_baseURL_(html, None)

        self._window.makeKeyAndOrderFront_(None)
        AppKit.NSApp.activateIgnoringOtherApps_(True)

    def _build_window(self):
        frame = AppKit.NSMakeRect(0, 0, WIDTH, HEIGHT)
        style = (
            AppKit.NSWindowStyleMaskTitled
            | AppKit.NSWindowStyleMaskClosable
            | AppKit.NSWindowStyleMaskResizable
        )
        window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame, style, AppKit.NSBackingStoreBuffered, False
        )
        window.setTitle_("Recent recordings")
        window.setTitlebarAppearsTransparent_(True)
        window.setMinSize_(AppKit.NSMakeSize(380, 320))
        window.center()
        # Closing must only hide the window: releasing it would leave this
        # object holding a freed NSWindow the next time the menu is used.
        window.setReleasedWhenClosed_(False)

        config = WebKit.WKWebViewConfiguration.alloc().init()
        webview = WebKit.WKWebView.alloc().initWithFrame_configuration_(frame, config)
        webview.setAutoresizingMask_(
            AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable
        )
        try:  # a normal page, so suppress the right-click "Reload" menu
            webview.setValue_forKey_(False, "allowsBackForwardNavigationGestures")
        except Exception:
            pass

        window.setContentView_(webview)
        self._window, self._webview = window, webview

    def _install_bridge(self, items):
        controller = self._webview.configuration().userContentController()
        controller.removeScriptMessageHandlerForName_(HANDLER_NAME)
        self._bridge = _Bridge.alloc().initWithItems_(items)
        controller.addScriptMessageHandler_name_(self._bridge, HANDLER_NAME)
