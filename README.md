# Whisper Dictation HCP

Private, **100% offline** dictation for macOS — press a hotkey, talk, and your
speech is transcribed into whatever field has focus. A hardened fork of
[foges/whisper-dictation](https://github.com/foges/whisper-dictation), rebuilt
for privacy, security, and speed on Apple Silicon.

## Why this fork

| | Upstream fork | This build |
|---|---|---|
| Inference engine | PyTorch + openai-whisper (~2–3 GB) | **faster-whisper** (CTranslate2, no torch) |
| Cloud `openai` SDK | installed (unused, pulled aiohttp + 57 CVE advisories) | **removed** |
| `torch` CVEs (27) | present | **gone** (no torch) |
| Mic capture | pyaudio (needs `brew install portaudio`) | **sounddevice** (bundles PortAudio) |
| Accented text (á é í ñ) | per-char typing (can mangle) | **clipboard paste** (reliable) |
| Platforms | Apple Silicon focus | **Apple Silicon + Intel** |
| Network calls in code | none | none |
| Dependency pinning | stale lock from 2023 | freshly pinned (`requirements.txt`) |

## Requirements

- macOS on Apple Silicon (M1/M2/M3/…) or Intel.
- Python 3.10+
- `ffmpeg` (`brew install ffmpeg`) — for audio decoding.

## Install

```bash
cd whisper-dictation-hcp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt      # pinned, reproducible
```

## Permissions (one-time, macOS)

Grant the app (or your Terminal, while testing) permission under
**System Settings → Privacy & Security**:

- **Microphone** — to record your voice.
- **Accessibility** — to register the global hotkey and insert text.

## Usage

```bash
./run.sh                             # default: 'small' model, cmd+alt toggle
python -m dictation -m large-v3 -l es # higher accuracy, force Spanish
python -m dictation -k ctrl+shift_l  # Ctrl + LEFT Shift toggle
python -m dictation --double-cmd     # double-tap Right Command to start
python -m dictation --device MacBook # force a specific input device
python -m dictation --no-mute        # don't mute system audio while recording
python -m dictation -l es,en         # menu switcher between Spanish & English
```

Look for the 🎙️ icon in the menu bar. Toggle with **cmd + option** (default),
speak, toggle again — the text appears where your cursor is.
`🔴 mm:ss` = recording, `⏳` = transcribing.

Everything is also adjustable live from the menu:

- **Microphone** — pick your input (virtual/app devices like Zoom, Teams and
  "Capture Inactive" are hidden; defaults to the built-in mic).
- **Shortcut** — switch the toggle key (⌘+Option, ⌃+Option, ⌃+Left Shift,
  ⌘+Shift, ⌥+Space, or double-tap Right ⌘). Modifiers match by key code, so
  left/right sides are handled correctly.
- **Mute audio while recording** — mutes system output during capture so music
  doesn't leak into the mic, then restores your previous state. On by default.
- **Status** — shows what just happened (inserted / no speech / no audio / error).
  Full log at `~/Library/Logs/whisper-dictation-hcp.log`.

### Models

`small` (default): fast and accurate enough for everyday dictation. Use
`-m large-v3` for maximum accuracy, or `-m base` / `-m tiny` for the lowest
latency. Models download once from Hugging Face, then run offline from cache.

## Privacy

Your audio never leaves the machine. The **only** network access is the one-time
model download on first run. For a fully air-gapped install, pre-download the
model on a connected machine and copy `~/.cache/huggingface` across.

## Swapping the backend

All inference lives behind the `Transcriber` class in
`dictation/transcriber.py` — the single seam to swap engines (e.g. MLX on
Apple Silicon, or whisper.cpp) without touching the rest of the app.
