"""Persist user preferences (chosen mic, shortcut, language, mute) between runs.

Stored as JSON at ~/Library/Application Support/whisper-dictation-hcp/prefs.json.
The device is saved by NAME (not index) because indices shift as devices are
plugged/unplugged; the name is resolved back to an index at startup.
"""

import json
import os

from .log import get_logger

log = get_logger()

PREFS_DIR = os.path.expanduser("~/Library/Application Support/whisper-dictation-hcp")
PREFS_PATH = os.path.join(PREFS_DIR, "prefs.json")


def load() -> dict:
    try:
        with open(PREFS_PATH) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        log.exception("could not read prefs")
        return {}


def save(prefs: dict) -> None:
    try:
        os.makedirs(PREFS_DIR, exist_ok=True)
        with open(PREFS_PATH, "w") as f:
            json.dump(prefs, f, indent=2)
        log.info(f"saved prefs: {prefs}")
    except Exception:
        log.exception("could not save prefs")
