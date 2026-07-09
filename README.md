<div align="center">

# 🎙️ Whisper Dictation

### Your voice → text, anywhere on your Mac. Private, offline, and free.

Press a shortcut. Talk. Watch your words appear in whatever app you're in —
Slack, Notes, an email, this very README. No cloud. No subscription. No catch.

![macOS](https://img.shields.io/badge/macOS-11%2B-black?logo=apple&logoColor=white)
![Apple Silicon + Intel](https://img.shields.io/badge/Apple%20Silicon%20%2B%20Intel-supported-success)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)
![100% Offline](https://img.shields.io/badge/100%25-offline-brightgreen)
![No Telemetry](https://img.shields.io/badge/telemetry-none-critical)
![License](https://img.shields.io/badge/License-MIT-yellow)

</div>

---

## ✨ What is this?

A free, private, on-device dictation app for macOS — a home-grown alternative to
paid tools like Wispr Flow. It lives quietly in your menu bar (🎙️), waits for a
keyboard shortcut, listens, and types out what you said **right where your cursor
is**. Everything happens **on your own Mac** — your voice never touches the
internet.

> 💡 Fun fact: this whole README's first draft was dictated with the app itself.

---

## 🧬 What it's built on (standing on open-source shoulders)

This project didn't appear from thin air — it's built on two great open-source
projects:

- 🧠 **The brain → [OpenAI Whisper](https://github.com/openai/whisper).** We took
  OpenAI's world-class speech-recognition model as our foundation. To make it
  fast and light on a Mac, we run it through
  [**faster-whisper**](https://github.com/SYSTRAN/faster-whisper) (a CTranslate2
  port) instead of PyTorch — same Whisper accuracy, a fraction of the size.

- 🛠️ **The skeleton → [foges/whisper-dictation](https://github.com/foges/whisper-dictation).**
  We **forked and modernized** this existing menu-bar app. It solved the hard
  parts (global hotkey + menu bar + typing into other apps), but it was
  unmaintained and carried some baggage. We cleaned it up and made it solid.

### 🔧 What we changed / improved

| | Original | **This build** |
|---|---|---|
| Engine | PyTorch + openai-whisper (~2–3 GB) | **faster-whisper** (no PyTorch) |
| Unused cloud SDK | shipped `openai` (pulled aiohttp) | **removed** |
| Known dependency CVEs | **125** (in a 2023-era lockfile) | **0** (freshly audited) |
| Global hotkey | pynput (flaky in a packaged app) | **native NSEvent** (rock solid) |
| Microphone | first device it found | **live picker** (great for USB/Continuity mics) |
| Accents (á é í ñ) | per-character typing (mangled) | **clipboard paste** (perfect) |
| Extras | — | **mute music while recording, start/stop sounds, remembers your settings** |
| Signing | ad-hoc | **stable code signature** (permissions persist) |

---

## 🔒 100% offline & private

The **only** time this app uses the internet is a one-time model download on
first launch. After that, pull the Wi-Fi plug and it keeps working forever.

- ✅ Your audio is transcribed **on-device** and never uploaded.
- ✅ No accounts, no telemetry, no analytics, no network calls in the code.
- ✅ Want a fully air-gapped install? Pre-download the model once and copy it over.

---

## 🎬 How it works (the 10-second version)

```
   ┌─────────────┐        ┌──────────────────┐        ┌─────────────────────┐
   │  ⌨️  Press   │        │  🧠  Whisper      │        │  ✍️  Types it where │
   │  ⌘ + Option │  ───▶  │  transcribes      │  ───▶  │  your cursor is     │
   │  (Tink! 🔔) │        │  on your Mac      │        │  (Bottle! 🔔)       │
   └─────────────┘        └──────────────────┘        └─────────────────────┘
      you talk               100% local                 any app, any field
```

1. **Press your shortcut** (default `⌘ + Option`) — you hear a little *Tink* and the menu-bar icon turns 🔴.
2. **Just talk**, normally, in English, Spanish, or both mixed together.
3. **Press again** — a *Bottle* pop, and your words appear instantly where you were typing. ✨

---

## 🚀 Features

- 🌎 **Multilingual, no translation** — speak English, Spanish, or *spanglish* and it writes exactly what you said.
- 🎚️ **Pick any microphone** from the menu (USB, Continuity Camera, built-in…).
- ⌨️ **Configurable shortcut** — `⌘+Option`, `⌃+Option`, `⌃+Left Shift`, double-tap Right ⌘, and more.
- 🔇 **Auto-mutes your music** while recording, then restores it (so songs don't leak into the mic).
- 🔔 **Audio cues** on start/stop so you know it's listening — without looking.
- 💾 **Remembers your settings** (mic, shortcut, language, mute, sounds) between launches.
- 🩺 **Self-diagnosing** — a Status line + log at `~/Library/Logs/whisper-dictation-hcp.log`.

---

## 📦 Install (build from source)

**Requirements:** macOS 11+ (Apple Silicon or Intel), Python 3.10+, and `ffmpeg`
(`brew install ffmpeg`).

```bash
git clone https://github.com/jofer95/whisper-dictation-hcp.git
cd whisper-dictation-hcp
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

./scripts/build.sh        # builds + code-signs the .app into dist/
cp -R "dist/Whisper Dictation.app" /Applications/
open "/Applications/Whisper Dictation.app"
```

Prefer to run from source without packaging? Just `./run.sh`.

### 🔑 One-time permissions (macOS)

Grant the app, under **System Settings → Privacy & Security**:

- **Accessibility** — to register the global shortcut and paste text.
- **Microphone** — to hear you (prompted on first recording).

The menu's **"Grant / re-check permissions…"** item opens the right pane for you.

---

## ⌨️ Using it

Look for 🎙️ in the menu bar. Everything is adjustable live from its menu:

| Menu item | What it does |
|---|---|
| **Start / Stop Dictation** | Toggle recording by mouse (same as the shortcut). |
| **Microphone** | Choose your input device. |
| **Shortcut** | Change the toggle keys. |
| **Language** | Auto (best for mixing), Español, or English. |
| **Mute audio while recording** | Silence output during capture. |
| **Play start/stop sounds** | The *Tink* / *Bottle* cues. |
| **Status / Permissions** | What just happened, and permission health. |

`🎙️` idle · `🔴 mm:ss` recording · `⏳` transcribing.

**Models:** defaults to `small` (fast + accurate). Use `-m large-v3` for maximum
accuracy on heavily mixed-language speech.

---

## 🛠️ For developers

```bash
./scripts/build.sh              # py2app build + stable self-signed code signature
python -m dictation --help      # all CLI options
python -m dictation --self-test # verify a build boots & the engine runs
```

- `dictation/` — the app: `transcriber` (faster-whisper), `recorder` (sounddevice),
  `injector` (clipboard paste), `hotkey` (NSEvent), `app` (rumps menu bar),
  `sound`, `perms`, `prefs`, `log`.
- `scripts/sign_selfsigned.sh` — creates a stable signing identity so macOS keeps
  your permission grants across rebuilds. For company-wide (MDM) distribution,
  swap it for a Developer ID / org certificate + notarization.
- `QA_CHECKLIST.md` — run before each release.

---

## ⚠️ Known issues

- **Hold-to-talk (`⌘+D`) can clash with an app's own `⌘+D` shortcut.** The
  push-to-talk *hold* mode is bound to `⌘+D`. It tries to "swallow" the key so it
  doesn't reach the app underneath, but a few apps still catch it — most notably
  **Google Chrome**, where `⌘+D` bookmarks the current page (adds it to your
  favorites). So holding to dictate while Chrome is focused may also create a
  bookmark. **Workaround:** pick a non-conflicting shortcut from
  **🎙️ → Shortcut** (e.g. `⌘+Option`, `⌃+Option`, or `⌃+Left Shift`), or avoid
  hold mode while using Chrome. A user-configurable hold key is on the roadmap.

## 🙏 Credits & license

Built with deep gratitude on top of:

- [OpenAI Whisper](https://github.com/openai/whisper) — the speech-recognition model (MIT)
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — the fast on-device runtime (MIT)
- [foges/whisper-dictation](https://github.com/foges/whisper-dictation) — the original app we forked (MIT)

Released under the **MIT License**. Made with care for my team. 💚
