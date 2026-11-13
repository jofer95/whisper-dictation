# QA Checklist — Whisper Dictation

Run through this before tagging a release. Check the menu-bar **Status** line and
`~/Library/Logs/whisper-dictation-hcp.log` when something looks off.

Legend: ⌘⌥ = default toggle. "Status" = the menu's `Status:` line.

## 0. Install & launch
- [ ] `./scripts/build.sh` completes and prints `signature verifies ✅`.
- [ ] App copies to `/Applications` and launches; 🎙️ appears in the menu bar.
- [ ] `codesign --verify --deep --strict "/Applications/Whisper Dictation.app"` passes.
- [ ] Menu shows: Start/Stop, Microphone, Shortcut, Language, Mute toggle, Status, Permissions, Grant, Quit.

## 1. Permissions
- [ ] First launch (or after `tccutil reset Accessibility io.github.jofer95.whisper-dictation`) prompts for Accessibility.
- [ ] After granting, `Permissions:` line shows `Accessibility ✅ · Microphone ✅` within ~3 s **without relaunch**.
- [ ] First recording prompts for Microphone (if not already granted); allow it.
- [ ] `Grant / re-check permissions…` opens the correct Settings panes when something is missing.

## 2. Basic dictation
- [ ] **English:** focus a text field, ⌘⌥, speak a sentence, ⌘⌥ → text is inserted correctly.
- [ ] **Spanish (accents):** dictate "canción, mañana, corazón" → accents render correctly (á é í ó ú ñ).
- [ ] **Mixed / spanglish:** dictate a sentence mixing English + Spanish → both languages preserved, **nothing translated**.
- [ ] Status shows `inserted: "…"` after a successful dictation.

## 3. Microphone
- [ ] Menu → Microphone lists all inputs (built-in, USB/camera, Continuity, etc.).
- [ ] "System Default" is checked by default and records from the OS-selected input.
- [ ] Selecting a specific mic records from it (verify with the log `recording started on '…'`).
- [ ] Clamshell/lid-closed: recording works from the USB/Continuity mic (not the dead built-in).
- [ ] Unplugging the selected device then recording falls back gracefully (Status shows an error, not a crash).

## 4. Shortcuts
- [ ] ⌘+Option toggles reliably (multiple times).
- [ ] Menu → Shortcut → each preset works: ⌃+Option, ⌘+Shift, ⌥+Space.
- [ ] **⌃ + Left Shift** triggers only with the LEFT shift, not the right.
- [ ] Double-tap Right ⌘: double-tap starts, single tap stops.
- [ ] Log shows `toggle fired` on each keyboard trigger (confirms it's the hotkey, not the menu).
- [ ] Holding the combo does not double-fire.

## 5. Mute while recording
- [ ] Play music → ⌘⌥ → output mutes → stop → music resumes.
- [ ] If audio was already muted before recording, it stays muted after (state restored exactly).
- [ ] Toggle "Mute audio while recording" off → music keeps playing during recording.

## 6. Language
- [ ] Language menu: Auto (default), Español, English (+ any `-l` extras).
- [ ] Auto handles mixed languages without translating.
- [ ] Forcing Español transcribes Spanish accurately; forcing English likewise.

## 7. Preferences persistence
- [ ] Change mic, shortcut, language, and mute via the menu.
- [ ] Quit and relaunch → all four choices are remembered (check `prefs applied: …` in the log).
- [ ] Preferences file exists at `~/Library/Application Support/whisper-dictation-hcp/prefs.json`.
- [ ] A saved mic that's currently unplugged falls back to system default without error.

## 8. Edge cases
- [ ] Recording pure silence → Status `no speech detected` (no garbage inserted).
- [ ] Recording with the wrong/no mic → Status `no audio — check mic & mic permission`.
- [ ] Long recording warns at 19 min and auto-stops + transcribes at `--max-time` (default 20 min).
- [ ] Rapid start/stop doesn't crash or wedge the app.
- [ ] Quit fully stops the app (no lingering process: `pgrep -f "Whisper Dictation"`).

## 9. Privacy (spot check)
- [ ] After first-run model download, disconnect Wi-Fi → dictation still works fully offline.
- [ ] No unexpected network connections during dictation (e.g., verify with Little Snitch / `nettop`).

## v1.13 — Wispr-style flow

- [ ] Smart mode: hold shortcut ≥1 s, speak, release → text inserted.
- [ ] Smart mode: quick single tap → pill appears then disappears, nothing typed.
- [ ] Smart mode: double-tap → hands-free (keeps recording after release); tap again → inserted.
- [ ] Esc while recording → "Cancelled", nothing typed, Esc not delivered to the app.
- [ ] ⌘⌥ + Esc (with ⌘⌥ shortcut) → Force Quit opens, recording silently cancelled.
- [ ] ⌥+Space toggle → no non-breaking space typed into the document.
- [ ] ⌃⌘V → pastes the last transcript.
- [ ] Say "… scratch that …" / "nueva línea" / "a las 2, no, a las 3" → cleaned output.
- [ ] Dictionary: add a word, save, dictate it → exact spelling, no restart.
- [ ] Copy an image, dictate, ⌘V → the image is still on the clipboard.
- [ ] Dictate into a password field → not pasted, "copied instead" message.
- [ ] Click the desktop (no text field), dictate → "No text field — copied" message, ⌘V works.
- [ ] Plug in AirPods while the app runs → they appear under Microphone.
- [ ] Select a silent/virtual mic → "No sound from …" warning after ~2.5 s.
- [ ] Two monitors: pill appears above the Dock on the screen with the mouse.
- [ ] `python -m unittest discover -s tests` passes.


## AI cleanup (optional, on-device — macOS 26+, Apple Intelligence)

- [ ] Off by default on a fresh install (no `ai_cleanup` in prefs) — dictation behaves exactly as before.
- [ ] Smart formatting → AI cleanup ON, on a Mac with Apple Intelligence: dictate "let's meet at 2 actually 3 on Thursday" → "Let's meet at 3 on Thursday."; log shows `AI cleanup ok in X.XXs`.
- [ ] Mixed ES/EN sentence ("oye podemos hacer el deploy mañana porque el build de staging is still failing") → punctuation fixed, nothing translated.
- [ ] Dictate a question ("what time is the meeting") → the question is inserted, not an answer.
- [ ] Apple Intelligence off (or macOS < 26) → turning the toggle on shows an alert and it switches back off; dictation keeps working.
- [ ] `--self-test` on the built .app prints `AI cleanup helper: …/Contents/Resources/aicleanup; available: (True, 'ok')`.
- [ ] Release build: `codesign -dvv "dist/Whisper Dictation.app/Contents/Resources/aicleanup"` shows the Developer ID and `flags=0x10000(runtime)`.
- [ ] `AICLEANUP_LIVE=1 python -m unittest tests.test_aicleanup.Live` passes.
