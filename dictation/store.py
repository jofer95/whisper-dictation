"""Local, on-disk state — everything stays on this Mac.

  ~/Library/Application Support/whisper-dictation/
      dictionary.json   your words, corrections and snippets (hand-editable)
      history.jsonl     recent dictations (optional; "Save history" in the menu)
      stats.json        word counts / speaking time for the menu's stats line
"""

import datetime as _dt
import json
import os
import threading
import time

from .log import get_logger
from .prefs import PREFS_DIR

log = get_logger()

DICTIONARY_PATH = os.path.join(PREFS_DIR, "dictionary.json")
HISTORY_PATH = os.path.join(PREFS_DIR, "history.jsonl")
STATS_PATH = os.path.join(PREFS_DIR, "stats.json")

HISTORY_KEEP = 500

_TEMPLATE = {
    "_help": (
        "Edit, save, and your next dictation picks it up (no restart). "
        "'words': names / jargon to spell exactly like this (also biases recognition). "
        "'corrections': what Whisper hears -> what you want. "
        "'snippets': say the trigger, get the text."
    ),
    "words": ["GitHub", "README", "Jira", "Slack", "Wispr Flow"],
    "corrections": {"git hub": "GitHub", "wisper flow": "Wispr Flow"},
    "snippets": {"my signature": "Thanks,\nYour Name"},
}


def _ensure_dir():
    os.makedirs(PREFS_DIR, exist_ok=True)


def _atomic_write(path, text):
    _ensure_dir()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


# ------------------------------------------------------------ dictionary ----

class Dictionary:
    """Reloads dictionary.json whenever it changes on disk."""

    def __init__(self, path=DICTIONARY_PATH):
        self.path = path
        self._mtime = None
        self.words, self.corrections, self.snippets = [], {}, {}
        self.error = None
        if not os.path.exists(path):
            try:
                _atomic_write(path, json.dumps(_TEMPLATE, indent=2, ensure_ascii=False))
            except Exception:
                log.exception("could not create dictionary.json")
        self.reload()

    def reload(self):
        try:
            mtime = os.path.getmtime(self.path)
        except OSError:
            return
        if mtime == self._mtime:
            return
        self._mtime = mtime
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            self.words = [str(w) for w in data.get("words", []) if str(w).strip()]
            self.corrections = {str(k): str(v) for k, v in
                                (data.get("corrections") or {}).items()}
            self.snippets = {str(k): str(v) for k, v in (data.get("snippets") or {}).items()}
            self.error = None
            log.info(f"dictionary loaded: {len(self.words)} words, "
                     f"{len(self.corrections)} corrections, {len(self.snippets)} snippets")
        except Exception as exc:
            self.error = f"dictionary.json: {exc}"
            log.warning(f"keeping previous dictionary — {self.error}")

    def vocabulary(self):
        """Spellings to bias recognition toward (your words + correction targets)."""
        seen, out = set(), []
        for w in list(self.words) + list(self.corrections.values()):
            if w.lower() not in seen:
                seen.add(w.lower())
                out.append(w)
        return out


# --------------------------------------------------------------- history ----

class History:
    def __init__(self, enabled=True, path=HISTORY_PATH):
        self.path = path
        self.enabled = enabled
        self._lock = threading.Lock()
        self.items = []          # newest first: dicts with ts/text
        if enabled:
            self._load()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                lines = f.readlines()
        except FileNotFoundError:
            return
        except Exception:
            log.exception("could not read history")
            return
        items = []
        for line in lines[-HISTORY_KEEP:]:
            try:
                items.append(json.loads(line))
            except Exception:
                continue
        self.items = list(reversed(items))
        if len(lines) > HISTORY_KEEP:
            self._rewrite()

    def _rewrite(self):
        try:
            _atomic_write(self.path, "".join(
                json.dumps(i, ensure_ascii=False) + "\n" for i in reversed(self.items)))
        except Exception:
            log.exception("could not rewrite history")

    def add(self, text, **meta):
        entry = {"ts": round(time.time()), "text": text, **meta}
        with self._lock:
            self.items.insert(0, entry)
            del self.items[HISTORY_KEEP:]
            if self.enabled:
                try:
                    _ensure_dir()
                    with open(self.path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                except Exception:
                    log.exception("could not append history")

    def last(self):
        return self.items[0]["text"] if self.items else None

    def set_enabled(self, enabled):
        self.enabled = enabled
        if not enabled:
            self.clear(memory=False)

    def clear(self, memory=True):
        with self._lock:
            if memory:
                self.items = []
            try:
                os.remove(self.path)
            except FileNotFoundError:
                pass
            except Exception:
                log.exception("could not delete history file")


# ----------------------------------------------------------------- stats ----

class Stats:
    def __init__(self, path=STATS_PATH):
        self.path = path
        self.data = {"words": 0, "seconds": 0.0, "dictations": 0, "days": {}}
        try:
            with open(path, encoding="utf-8") as f:
                self.data.update(json.load(f))
        except FileNotFoundError:
            pass
        except Exception:
            log.exception("could not read stats")

    def add(self, words, seconds):
        today = _dt.date.today().isoformat()
        d = self.data
        d["words"] += words
        d["seconds"] += seconds
        d["dictations"] += 1
        d["days"][today] = d["days"].get(today, 0) + words
        # keep ~a year of daily counts
        if len(d["days"]) > 400:
            for k in sorted(d["days"])[:-400]:
                del d["days"][k]
        try:
            _atomic_write(self.path, json.dumps(d))
        except Exception:
            log.exception("could not save stats")

    def summary(self):
        d = self.data
        today = d["days"].get(_dt.date.today().isoformat(), 0)
        wpm = round(d["words"] / (d["seconds"] / 60)) if d["seconds"] > 30 else None
        parts = [f"Today: {today:,} words", f"total {d['words']:,}"]
        if wpm:
            parts.append(f"{wpm} wpm")
        return " · ".join(parts)
