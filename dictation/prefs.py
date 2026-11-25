"""Persist user preferences (chosen mic, shortcut, language, mute) between runs.

Stored as JSON at ~/Library/Application Support/whisper-dictation/prefs.json.
(Builds before 1.14 used a folder named "whisper-dictation-hcp"; it is moved
over automatically on first launch, so settings, dictionary and history survive.)
The device is saved by NAME (not index) because indices shift as devices are
plugged/unplugged; the name is resolved back to an index at startup.
"""

import json
import os

from .log import get_logger

log = get_logger()

PREFS_DIR = os.path.expanduser("~/Library/Application Support/whisper-dictation")
_LEGACY_DIR = os.path.expanduser("~/Library/Application Support/whisper-dictation-hcp")


def _migrate_legacy_dir():
    try:
        if not os.path.exists(PREFS_DIR) and os.path.isdir(_LEGACY_DIR):
            os.rename(_LEGACY_DIR, PREFS_DIR)
            log.info(f"moved settings from {_LEGACY_DIR} to {PREFS_DIR}")
    except Exception:
        log.exception("could not migrate the legacy settings folder")


_migrate_legacy_dir()
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
