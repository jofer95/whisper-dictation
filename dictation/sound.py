"""Mute the system output while recording, then restore it.

If music is playing through the speakers it can leak into the mic and pollute the
transcription; muting output during capture avoids that. We read the prior mute
state on start and restore it exactly on stop (so we never un-mute audio the user
had muted themselves).

Uses `osascript` with the built-in `set volume` command. That's a system
scripting addition — it does NOT drive another app, so it needs no Automation
permission.
"""

import subprocess

from AppKit import NSSound

from .log import get_logger

log = get_logger()

# Cache NSSound objects so they aren't garbage-collected mid-playback.
_sound_cache = {}


def play_sound(name: str) -> None:
    """Play a named macOS system sound (e.g. 'Tink', 'Bottle') asynchronously."""
    try:
        snd = _sound_cache.get(name)
        if snd is None:
            snd = NSSound.soundNamed_(name)
            _sound_cache[name] = snd
        if snd is not None:
            snd.stop()   # rewind if it's still playing
            snd.play()
        else:
            log.warning(f"system sound {name!r} not found")
    except Exception:
        log.exception("could not play sound")


def get_output_muted():
    """Return True/False for current output mute, or None if it can't be read."""
    try:
        result = subprocess.run(
            ["osascript", "-e", "output muted of (get volume settings)"],
            capture_output=True, text=True, timeout=3,
        )
        return result.stdout.strip() == "true"
    except Exception:
        log.exception("could not read output mute state")
        return None


def set_output_muted(muted: bool):
    try:
        value = "true" if muted else "false"
        subprocess.run(
            ["osascript", "-e", f"set volume output muted {value}"],
            capture_output=True, text=True, timeout=3,
        )
    except Exception:
        log.exception("could not set output mute state")
