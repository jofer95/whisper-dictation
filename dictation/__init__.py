"""Whisper Dictation HCP — private, offline dictation for macOS (Apple Silicon).

A hardened fork of foges/whisper-dictation:
  * MLX Whisper backend (Apple Neural Engine / GPU) — no PyTorch.
  * sounddevice for capture (bundles PortAudio — no Homebrew needed).
  * Clipboard-paste injection so accented Spanish characters land correctly.
  * No cloud SDK, no network calls, no telemetry. 100% local.
"""

__version__ = "1.8.0"
