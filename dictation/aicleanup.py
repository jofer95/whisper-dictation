"""Optional on-device "AI cleanup" — Apple's Foundation Models via a Swift helper.

The rule-based clean-up in textproc.py is conservative: it never guesses. This
step does the fuzzy part Wispr Flow does with a cloud LLM (grammar, punctuation,
self-corrections, spoken lists) using the system model that ships with Apple
Intelligence on macOS 26+. Still 100% offline: the model runs on this Mac.

The Foundation Models API is Swift-only, so a tiny CLI does the work
(packaging/aicleanup/main.swift, bundled into the .app's Resources). We pipe the
text in, read the cleaned text out, and enforce a hard timeout.

Contract: `clean()` NEVER raises and NEVER makes things worse on failure. Any
problem — helper missing, old macOS, Apple Intelligence off, timeout, crash, or
an answer that doesn't look like a cleaned-up copy of the input (a translation,
a reply to a dictated question, a summary) — returns the rule-based text as-is.
"""

import os
import platform
import re
import subprocess
import threading
import time
from dataclasses import dataclass

from .log import get_logger

log = get_logger()

HELPER_NAME = "aicleanup"
HELPER_ENV = "WHISPER_AICLEANUP_HELPER"   # override the helper path (dev/tests)
MIN_MACOS = 26

# Hard timeout: 1.5 s for a normal dictation, stretched a little for long ones
# (the model produces ~200 chars/s warm, so a 500-char paragraph needs ~2.5 s).
TIMEOUT_BASE = 1.5
TIMEOUT_SHORT_CHARS = 200
TIMEOUT_PER_CHAR = 0.006
TIMEOUT_MAX = 4.0

WARM_EVERY = 20.0      # seconds; don't spawn a warm-up more often than this
CHECK_TIMEOUT = 3.0

# Sanity limits for accepting the model's answer.
MIN_LEN_RATIO = 0.5    # shorter than half the input -> it summarised / dropped content
MAX_LEN_RATIO = 1.5    # much longer -> it answered or elaborated
MAX_LEN_SLACK = 24     # chars of slack for list markers / punctuation on short input
MIN_OUTPUT_OVERLAP = 0.75  # share of output words that must come from the input
MIN_INPUT_KEPT = 0.5       # share of input words that must survive

_WORD = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass
class Result:
    text: str           # what to insert (cleaned, or the input on fallback)
    used: bool          # True when the AI output was accepted
    reason: str         # "ok", or why we fell back
    latency: float      # seconds spent in the helper (0 when it never ran)


# ----------------------------------------------------------------- helpers ----

def helper_path() -> str | None:
    """Where the helper binary lives: env override, inside the .app, or the repo build."""
    candidates = []
    if os.environ.get(HELPER_ENV):
        candidates.append(os.environ[HELPER_ENV])
    if os.environ.get("RESOURCEPATH"):          # set by py2app inside the .app
        candidates.append(os.path.join(os.environ["RESOURCEPATH"], HELPER_NAME))
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates.append(os.path.join(repo, "packaging", "aicleanup", "build", HELPER_NAME))
    for path in candidates:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def platform_supported() -> tuple[bool, str]:
    """Foundation Models needs macOS 26+ on Apple silicon."""
    if platform.system() != "Darwin":
        return False, "needs macOS"
    try:
        major = int(platform.mac_ver()[0].split(".")[0])
    except (ValueError, IndexError):
        major = 0
    if major < MIN_MACOS:
        return False, f"needs macOS {MIN_MACOS} or later"
    if platform.machine() != "arm64":
        return False, "needs an Apple silicon Mac"
    return True, "ok"


def timeout_for(text: str) -> float:
    extra = max(0, len(text) - TIMEOUT_SHORT_CHARS) * TIMEOUT_PER_CHAR
    return min(TIMEOUT_MAX, TIMEOUT_BASE + extra)


def _words(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text)]


def accept(original: str, candidate: str) -> tuple[bool, str]:
    """Is `candidate` plausibly a cleaned-up copy of `original` (not a rewrite)?

    Cheap lexical checks that catch the ways a small LLM goes wrong here:
    translating ("oye podemos…" -> "Hey, can we…"), answering a dictated
    question, refusing, summarising, or rambling on.
    """
    cand = candidate.strip()
    if not cand:
        return False, "empty output"
    n = len(original.strip())
    if len(cand) < n * MIN_LEN_RATIO:
        return False, f"output too short ({len(cand)} vs {n} chars)"
    if len(cand) > n * MAX_LEN_RATIO + MAX_LEN_SLACK:
        return False, f"output too long ({len(cand)} vs {n} chars)"
    src, out = _words(original), _words(cand)
    if not out:
        return False, "no words in output"
    if src:
        src_set, out_set = set(src), set(out)
        from_input = sum(1 for w in out if w in src_set) / len(out)
        if from_input < MIN_OUTPUT_OVERLAP:
            return False, f"output reworded/translated ({from_input:.0%} of words from input)"
        kept = sum(1 for w in src if w in out_set) / len(src)
        if kept < MIN_INPUT_KEPT:
            return False, f"output dropped too much ({kept:.0%} of input words kept)"
    return True, "ok"


def _match_leading_case(original: str, candidate: str) -> str:
    """The model sometimes lowercases the first word; textproc already capitalised it."""
    o = next((c for c in original if c.isalpha()), "")
    for i, c in enumerate(candidate):
        if c.isalpha():
            if o.isupper() and c.islower() and o.lower() == c:
                return candidate[:i] + c.upper() + candidate[i + 1:]
            break
    return candidate


# ------------------------------------------------------------------ client ----

class AICleanup:
    """Thin, failure-proof client for the Swift helper.

    `runner` is injectable (defaults to subprocess.run) so the fallback paths
    are unit-testable without the helper or macOS 26.
    """

    def __init__(self, path: str | None = None, runner=None, supported=None):
        self._path = path
        self._runner = runner or subprocess.run
        self._supported = supported            # (ok, reason) override for tests
        self._last_warm = 0.0
        self._lock = threading.Lock()

    @property
    def path(self) -> str | None:
        return self._path or helper_path()

    def _platform(self) -> tuple[bool, str]:
        return self._supported if self._supported is not None else platform_supported()

    def _precheck(self) -> tuple[str | None, str]:
        ok, reason = self._platform()
        if not ok:
            return None, reason
        path = self.path
        if not path:
            return None, "helper not installed"
        return path, "ok"

    def availability(self) -> tuple[bool, str]:
        """(usable, reason) — asks the helper whether the system model is ready."""
        path, reason = self._precheck()
        if not path:
            return False, reason
        try:
            proc = self._runner([path, "--check"], capture_output=True, text=True,
                                timeout=CHECK_TIMEOUT)
        except subprocess.TimeoutExpired:
            return False, "helper did not answer"
        except Exception as exc:
            return False, f"helper failed to run: {exc}"
        if proc.returncode == 0:
            return True, "ok"
        return False, (proc.stderr or "").strip() or f"helper exit {proc.returncode}"

    def warm(self) -> None:
        """Load the system model in the background so the next clean() is fast.

        Called when recording starts: by the time you stop talking it's hot.
        Non-blocking and throttled; failures are irrelevant (clean() copes).
        """
        path, _reason = self._precheck()
        now = time.time()
        with self._lock:
            if not path or now - self._last_warm < WARM_EVERY:
                return
            self._last_warm = now

        def run():
            try:
                self._runner([path, "--warm"], capture_output=True, timeout=10)
            except Exception:
                pass
        threading.Thread(target=run, name="ai-warm", daemon=True).start()

    def clean(self, text: str, language: str | None = None, style: str = "default",
              timeout: float | None = None) -> Result:
        if not text or not text.strip():
            return Result(text, False, "empty input", 0.0)
        path, reason = self._precheck()
        if not path:
            return Result(text, False, reason, 0.0)

        cmd = [path, "--style", style]
        if language and re.fullmatch(r"[a-z]{2,3}", language):
            cmd += ["--lang", language]
        limit = timeout if timeout is not None else timeout_for(text)
        t0 = time.time()
        try:
            proc = self._runner(cmd, input=text, capture_output=True, text=True,
                                encoding="utf-8", timeout=limit)
        except subprocess.TimeoutExpired:
            return Result(text, False, f"timed out after {limit:.1f}s", time.time() - t0)
        except Exception as exc:
            return Result(text, False, f"helper failed to run: {exc}", time.time() - t0)
        latency = time.time() - t0

        if proc.returncode != 0:
            err = (proc.stderr or "").strip().splitlines()
            return Result(text, False, f"helper exit {proc.returncode}: "
                          f"{err[-1] if err else 'no message'}", latency)
        candidate = (proc.stdout or "").strip()
        ok, why = accept(text, candidate)
        if not ok:
            return Result(text, False, f"rejected: {why}", latency)
        return Result(_match_leading_case(text, candidate), True, "ok", latency)
