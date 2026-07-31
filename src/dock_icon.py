"""Make the Dock tile do something useful.

rumps owns the NSApplication delegate and implements neither of the Dock
callbacks, so the tile is inert: clicking it does nothing and right-clicking
it offers only the system's own Options/Quit items. Both handlers are added
to rumps' delegate class rather than replacing the delegate, which would
break every menu callback rumps routes through it.
"""

import logging

logger = logging.getLogger(__name__)

MENU_TITLE = "Recent recordings…"

_handler = None
_target = None


def _build_target():
    """An object the Dock menu item can send its action to."""
    from Foundation import NSObject

    class _DockTarget(NSObject):
        def showRecordings_(self, _sender):
            if _handler is None:
                return
            try:
                _handler()
            except Exception as e:
                logger.error(f"Dock menu handler failed: {e}", exc_info=True)

    return _DockTarget.alloc().init()


def set_dock_icon_handler(callback):
    """Wire the Dock tile to `callback`. Safe to call more than once."""
    global _handler, _target
    _handler = callback

    try:
        import AppKit
        import objc
        from rumps.rumps import NSApp as RumpsDelegate
    except ImportError as e:
        logger.warning(f"Dock icon handlers unavailable: {e}")
        return

    if _target is None:
        _target = _build_target()

    if hasattr(RumpsDelegate, "applicationDockMenu_"):
        return  # already installed; the new callback arrives via _handler

    def applicationDockMenu_(self, app):
        """Right-clicking the tile: our item above the system's own."""
        menu = AppKit.NSMenu.alloc().init()
        item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            MENU_TITLE, objc.selector(None, selector=b"showRecordings:"), ""
        )
        item.setTarget_(_target)
        menu.addItem_(item)
        return menu

    def applicationShouldHandleReopen_hasVisibleWindows_(self, app, has_windows):
        """Left-clicking the tile when no window is open."""
        if _handler is not None:
            try:
                _handler()
            except Exception as e:
                logger.error(f"Dock icon handler failed: {e}", exc_info=True)
        return True

    try:
        objc.classAddMethods(RumpsDelegate, [
            objc.selector(applicationDockMenu_, signature=b"@@:@"),
            applicationShouldHandleReopen_hasVisibleWindows_,
        ])
        logger.info("Dock icon handlers installed")
    except Exception as e:
        logger.warning(f"Could not hook the Dock icon: {e}")
