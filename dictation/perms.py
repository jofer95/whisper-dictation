"""macOS permission checks: Accessibility (for the hotkey + pasting) and
Microphone (for recording). Lets the app validate everything up front and report
clearly, instead of failing silently.
"""

from .log import get_logger

log = get_logger()

_AUDIO_MEDIA_TYPE = "soun"  # AVMediaTypeAudio
_MIC_STATUS = {0: "notDetermined", 1: "restricted", 2: "denied", 3: "authorized"}


def accessibility_trusted(prompt: bool = False) -> bool:
    """Whether this process may post/observe key events. prompt=True shows the
    macOS 'grant Accessibility' dialog and deep-links to Settings."""
    try:
        from ApplicationServices import (
            AXIsProcessTrustedWithOptions,
            kAXTrustedCheckOptionPrompt,
        )
        return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: prompt}))
    except Exception:
        log.exception("accessibility check failed")
        return True  # don't block if we can't determine it


def microphone_status() -> str:
    """One of: notDetermined, restricted, denied, authorized, unknown."""
    try:
        from AVFoundation import AVCaptureDevice
        raw = AVCaptureDevice.authorizationStatusForMediaType_(_AUDIO_MEDIA_TYPE)
        return _MIC_STATUS.get(raw, "unknown")
    except Exception:
        log.exception("microphone status check failed")
        return "unknown"


def request_microphone(callback=None) -> None:
    """Trigger the microphone permission prompt if status is notDetermined."""
    try:
        from AVFoundation import AVCaptureDevice

        def handler(granted):
            log.info(f"microphone access granted={bool(granted)}")
            if callback:
                callback(bool(granted))

        AVCaptureDevice.requestAccessForMediaType_completionHandler_(
            _AUDIO_MEDIA_TYPE, handler
        )
    except Exception:
        log.exception("microphone request failed")


def open_settings(pane: str) -> None:
    """Open a Privacy & Security pane, e.g. 'Privacy_Accessibility' / 'Privacy_Microphone'."""
    import subprocess
    try:
        subprocess.Popen(
            ["open", f"x-apple.systempreferences:com.apple.preference.security?{pane}"]
        )
    except Exception:
        log.exception("could not open settings pane")


def summary() -> str:
    """Short human-readable permission summary for the menu/log."""
    ax = "✅" if accessibility_trusted(False) else "⚠️ off"
    mic = microphone_status()
    mic_icon = "✅" if mic == "authorized" else f"⚠️ {mic}"
    return f"Accessibility {ax} · Microphone {mic_icon}"
