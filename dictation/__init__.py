"""Whisper Dictation — private, offline dictation for macOS (Apple Silicon).

A hardened fork of foges/whisper-dictation:
  * faster-whisper (CTranslate2) backend — no PyTorch, runs fully offline.
  * Wispr Flow-style UX: hold / double-tap shortcuts, Esc to cancel, offline
    clean-up (fillers, "scratch that", backtrack), dictionary & snippets,
    context-aware spacing, paste-last, local history & stats.
  * sounddevice for capture (bundles PortAudio — no Homebrew needed).
  * Clipboard-paste injection so accented Spanish characters land correctly.
  * No cloud SDK, no network calls, no telemetry. 100% local.
"""

__version__ = "1.13.0"
