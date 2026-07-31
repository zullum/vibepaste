"""macOS permission checks.

VibePaste needs two *separate* TCC permissions, and they fail differently:

- Accessibility  — lets the event tap be created at all.
- Input Monitoring — lets that tap actually receive key events.

With Accessibility but not Input Monitoring the listener starts, reports
itself as running, and simply never sees a keystroke. Nothing raises. That
combination is worth naming explicitly, because it looks identical to a
broken hotkey.

Both are keyed to the *binary* that runs, which for this app is the
framework's Python.app, not the .app bundle. Changing the interpreter path
therefore silently revokes both.
"""

import logging
import threading

logger = logging.getLogger(__name__)

try:
    from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
except ImportError:
    AVCaptureDevice = None

try:
    from ApplicationServices import (
        AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt,
    )
except ImportError:  # not macOS, or PyObjC missing
    AXIsProcessTrustedWithOptions = None

try:
    from Quartz import (
        CGPreflightListenEventAccess, CGRequestListenEventAccess,
    )
except ImportError:
    CGPreflightListenEventAccess = None


def check_accessibility(prompt=False):
    """True if this process may create an event tap."""
    if AXIsProcessTrustedWithOptions is None:
        logger.warning("ApplicationServices unavailable; cannot check permissions")
        return True
    trusted = AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: prompt})
    logger.info(f"Accessibility: {'GRANTED' if trusted else 'DENIED'}")
    return bool(trusted)


AUTH_NOT_DETERMINED, AUTH_AUTHORIZED = 0, 3

MIC_USAGE_KEY = "NSMicrophoneUsageDescription"
MIC_USAGE_TEXT = "VibePaste records your voice to transcribe it to text."


def main_bundle_declares_microphone():
    """True if the main bundle declares the key *on disk*.

    Requesting microphone access without it does not fail — TCC kills the
    process outright (__TCC_CRASHING_DUE_TO_PRIVACY_VIOLATION__), and since
    macOS relaunches the app, that becomes a crash loop. Injecting the key
    into the in-memory info dictionary does not help: TCC reads the file.

    This is only satisfiable when the bundle's executable is a real binary,
    so that the main bundle is VibePaste.app rather than Python.app.
    """
    try:
        import plistlib

        import AppKit

        path = AppKit.NSBundle.mainBundle().bundlePath()
        with open(f"{path}/Contents/Info.plist", "rb") as handle:
            return bool(plistlib.load(handle).get(MIC_USAGE_KEY))
    except Exception:
        return False


def check_microphone(prompt=False, timeout=60):
    """True if this process may actually capture audio.

    This one fails the most quietly of all: when microphone access is
    missing, CoreAudio does not raise — it hands back a stream of digital
    silence. Recording "succeeds", the file is written, and Whisper, given
    pure silence, returns a stock hallucinated sentence. So the app appears
    to work while transcribing something the user never said.

    The Microphone pane has no "+" button, so this prompt is the only way
    for this binary to appear there at all.
    """
    if AVCaptureDevice is None:
        logger.warning("AVFoundation unavailable; cannot check Microphone")
        return True
    status = AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio)
    if status == AUTH_NOT_DETERMINED and prompt and not main_bundle_declares_microphone():
        logger.error(
            "Cannot ask for microphone access: the main bundle does not "
            "declare %s on disk, and requesting anyway would be killed by "
            "TCC. Recordings will be silent.", MIC_USAGE_KEY,
        )
        return False
    if status == AUTH_NOT_DETERMINED and prompt:
        answered = threading.Event()
        result = {}

        def handler(granted):
            result["granted"] = bool(granted)
            answered.set()

        AVCaptureDevice.requestAccessForMediaType_completionHandler_(
            AVMediaTypeAudio, handler
        )
        answered.wait(timeout)
        status = AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio)

    granted = status == AUTH_AUTHORIZED
    logger.info(f"Microphone: {'GRANTED' if granted else 'DENIED'}")
    return granted


def check_input_monitoring(prompt=False):
    """True if this process may receive key events from that tap.

    Requesting is what puts this binary into the System Settings list. The
    path is inside /opt, which the settings file picker will not browse to,
    so the prompt is the only practical way to add it.
    """
    if CGPreflightListenEventAccess is None:
        logger.warning("Quartz unavailable; cannot check Input Monitoring")
        return True
    granted = bool(CGPreflightListenEventAccess())
    if not granted and prompt:
        CGRequestListenEventAccess()
        granted = bool(CGPreflightListenEventAccess())
    logger.info(f"Input Monitoring: {'GRANTED' if granted else 'DENIED'}")
    return granted
