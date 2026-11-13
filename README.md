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
| Global hotkey | pynput (flaky in a packaged app) | **native event tap** (rock solid, swallows the combo) |
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
   │  ⌨️  HOLD    │        │  🧠  Whisper +    │        │  ✍️  Types it where │
   │  ⌃ + Option │  ───▶  │  offline cleanup  │  ───▶  │  your cursor is     │
   │  (Tink! 🔔) │        │  on your Mac      │        │  (Bottle! 🔔)       │
   └─────────────┘        └──────────────────┘        └─────────────────────┘
      you talk               100% local                 any app, any field
```

1. **Hold your shortcut** (default `⌃ + Option`, or `Fn 🌐`) and talk — release to insert. Like a walkie-talkie.
2. Long dictation? **Double-tap** the shortcut to go **hands-free**; tap once more to finish.
3. Changed your mind? **Esc** cancels. Text didn't land? **⌃⌘V** pastes the last one again.

---

## 🚀 Features

**Wispr Flow-style, but offline and free:**

- 🎙️ **Smart mode** — hold to talk, double-tap for hands-free, a lone accidental tap is ignored. (Toggle and Hold-only modes too.)
- ⎋ **Esc cancels**, **⌃⌘V pastes the last transcript**, and if your shortcut turns out to be part of another one (⌘⌥Esc, Fn+Delete…) the recording is silently dropped.
- 🌐 **Fn / Globe key** as the shortcut (the app tells you if macOS needs “Press 🌐 key to → Do Nothing”).
- 🧹 **Offline cleanup** — removes *um / uh / eh / mmm*, stutters (“the the”), and understands **“scratch that” / “borra eso”**, **“new line” / “nueva línea”**, **“new paragraph” / “punto y aparte”**, and corrections like *“at 2, actually 3”* → *“at 3”*.
- 📖 **Dictionary & snippets** — teach it names and jargon (“Acme Cloud”, not “Akmi Cloud”), fix words it keeps mishearing, and say a trigger to insert saved text. Hot-reloads, no restart.
- 🧭 **Context-aware** — reads the few characters around your cursor (Accessibility, nothing stored) to add the right space, lowercase *“And”* mid-sentence, drop the final period mid-line, and skip the final period in chat apps (Slack, Messages, WhatsApp, Teams…). Never pastes into password fields.
- 🌎 **Español + English** — auto-detect restricted to the languages you speak (no more Portuguese guesses on short clips), never translates.
- ⚡ **Fast** — ~0.8 s for a short phrase on Apple Silicon (half the previous latency).
- 📋 **Clipboard-safe** — your copied images/files/rich text survive a dictation, and clipboard managers don't log it.
- 🧾 **History & stats** — last dictations in the menu (saved locally, can be turned off), words today, total and your words-per-minute.
- 🪟 **Glass recording pill** above the Dock on the screen you're using: Cream, Liquid Glass, Dark HUD or Aurora; it warns you when the mic is silent.
- 🎚️ **Any microphone**, picked up live when you plug it in. 🔇 Optional mute of other audio. 🔔 Start/stop cues. 20-minute sessions.

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

| Do this | Get this |
|---|---|
| **Hold** the shortcut, talk, release | Text appears at your cursor |
| **Double-tap** the shortcut | Hands-free — tap again to finish |
| **Esc** while recording | Cancel, nothing is typed |
| **⌃⌘V** | Paste the last transcript again |
| Say **“scratch that”** / **“borra eso”** | Drops the previous sentence |
| Say **“new line”**, **“nueva línea”**, **“punto y aparte”** | Line / paragraph breaks |

Everything else lives in the 🎙️ menu: **History**, **Shortcut**, **Mode**,
**Microphone**, **Language**, **Speech model** (Small, or Large v3 Turbo for
maximum accuracy), **Smart formatting** switches, **Dictionary & snippets…**,
sounds, mute, pill style, permissions and the log.

`🎙️` idle · `🔴 mm:ss` recording · `⏳` transcribing.

### 📖 Dictionary & snippets

**🎙️ → Dictionary & snippets…** opens a small JSON file
(`~/Library/Application Support/whisper-dictation-hcp/dictionary.json`):

```json
{
  "words": ["Acme Cloud", "GitHub", "Jira"],
  "corrections": {"acme cloud": "Acme Cloud"},
  "snippets": {"my signature": "Thanks,\nJorge"}
}
```

Save it and your next dictation uses it.

---

## 🛠️ For developers

```bash
./scripts/build.sh              # py2app build + stable self-signed code signature
python -m dictation --help      # all CLI options
python -m dictation --self-test # verify a build boots & the engine runs
python -m unittest discover -s tests   # cleanup rules + shortcut state machine
# Full pipeline without a mic: plays a WAV through transcription → cleanup →
# paste into the focused app (run the built .app so it has Accessibility):
open "dist/Whisper Dictation.app" --args --simulate clip.wav
```

- `dictation/` — the app: `transcriber` (faster-whisper), `recorder` (sounddevice),
  `injector` (clipboard paste), `hotkey` (event tap + trigger state machine),
  `textproc` (offline cleanup), `context` (Accessibility cursor context),
  `store` (dictionary / history / stats), `overlay` (the pill), `app` (rumps menu bar),
  `sound`, `perms`, `prefs`, `log`.
- `scripts/sign_selfsigned.sh` — creates a stable signing identity so macOS keeps
  your permission grants across rebuilds. For company-wide (MDM) distribution,
  swap it for a Developer ID / org certificate + notarization.
- `QA_CHECKLIST.md` — run before each release.

---

## ⚠️ Known issues

- **Fn (🌐) shortcut:** macOS must be set to *System Settings → Keyboard → “Press 🌐 key to” → Do Nothing*, otherwise the emoji picker / input switcher fires too. The app detects this and tells you.
- **Context awareness is limited in some apps** (Slack, Teams, many Electron apps don't expose the text around the cursor). Dictation still works; spacing/capitals just aren't adjusted.
- **Cleanup is rule-based, not an LLM** (Wispr uses a cloud model). It only fires on explicit cues (“scratch that”, “2, no, 3”) so it never rewrites what you meant.

## 🙏 Credits & license

Built with deep gratitude on top of:

- [OpenAI Whisper](https://github.com/openai/whisper) — the speech-recognition model (MIT)
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — the fast on-device runtime (MIT)
- [foges/whisper-dictation](https://github.com/foges/whisper-dictation) — the original app we forked (MIT)

Released under the **MIT License**. Made with care for my team. 💚
