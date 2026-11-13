"""Menu-bar app (rumps). Global hotkeys come from dictation.hotkey.

Threading model:
  * Main thread — AppKit, the menu, the overlay, the hotkey event tap and the
    trigger state machine. Nothing here may block: a slow callback freezes the
    menu bar, and macOS disables an event tap whose callback is slow.
  * Audio worker — ONE serial thread that does everything that can block, in
    order: open the mic, (un)mute output via osascript, close the mic,
    transcribe, clean up, paste. Being serial, "start then stop" can never run
    out of order, however fast you tap.
  * The 1-second rumps.Timer (`_tick`) renders the title / status / stats.
"""

import queue
import subprocess
import threading
import time

import numpy as np
import rumps

from . import aicleanup
from . import context as axcontext
from . import perms
from . import prefs
from . import textproc
from .hotkey import HotkeyMonitor, fn_key_setting
from .log import LOG_PATH, get_logger
from .overlay import WaveformOverlay
from .recorder import default_input_name, device_name, list_input_devices, rescan
from .sound import get_output_muted, play_sound, set_output_muted
from .store import DICTIONARY_PATH, Dictionary, History, Stats

log = get_logger()

# Below this peak amplitude we treat the clip as "no audio" (wrong mic / no perm).
SILENCE_PEAK = 0.005
SILENT_MIC_WARN_AFTER = 2.5   # seconds of total silence before warning

START_SOUND = "Tink"
STOP_SOUND = "Bottle"
CANCEL_SOUND = "Funk"

HISTORY_MENU = 10
HISTORY_PREVIEW = 48

MAX_TIME_DEFAULT = 20 * 60    # Wispr parity: 20-minute sessions

SHORTCUTS = [
    ("Fn (🌐)", "fn"),
    ("⌃ + ⌥", "ctrl+alt"),
    ("⌘ + ⌥", "cmd+alt"),
    ("⌃ + Left ⇧", "ctrl+shift_l"),
    ("⌘ + ⇧", "cmd+shift"),
    ("⌥ + Space", "alt+space"),
    ("⌘ + D", "cmd+d"),
    ("Double-tap Right ⌘", "double_cmd"),
]

MODES = [
    ("Smart — hold to talk, double-tap for hands-free", "smart"),
    ("Toggle — tap on / tap off", "toggle"),
    ("Hold — push-to-talk only", "hold"),
]

LANGUAGES = [
    ("Auto — Español + English", "auto:es,en"),
    ("Auto — any language", None),
    ("Español", "es"),
    ("English", "en"),
]

MODELS = [
    ("Small — fastest (default)", "small"),
    ("Large v3 Turbo — most accurate (1.6 GB download)", "large-v3-turbo"),
]

OVERLAY_STYLES = [
    ("Cream (original)", "cream"),
    ("Liquid Glass", "glass"),
    ("Dark HUD Glass", "hud"),
    ("Aurora Glass", "aurora"),
]

# Boolean settings shown as checkmarks: key -> (menu label, default)
TOGGLES = {
    "fillers": ("Remove filler words (um, eh…)", True),
    "commands": ("Voice commands (new line, scratch that, 2-no-3)", True),
    "context": ("Match surrounding text (spacing, capitals)", True),
    "casual": ("Casual punctuation in chat apps", True),
    "ai_cleanup": ("AI cleanup — on-device Apple Intelligence (macOS 26+)", False),
}


def _radio(items, current, callback):
    out = []
    for label, value in items:
        it = rumps.MenuItem(label, callback=callback)
        it.state = 1 if value == current else 0
        it._value = value
        out.append(it)
    return out


class StatusBarApp(rumps.App):
    IDLE_ICON = "🎙️"
    REC_ICON = "🔴"
    BUSY_ICON = "⏳"

    def __init__(self, recorder, injector, settings, load_model):
        super().__init__("Whisper Dictation", title=self.IDLE_ICON, quit_button=None)
        self.recorder = recorder
        self.injector = injector
        self.s = settings              # live settings dict, persisted via prefs
        self._load_model = load_model  # (name) -> Transcriber, may download
        self.transcriber = None
        self._model_ready = False
        self._status = "loading model…"

        self.dictionary = Dictionary()
        self.history = History(enabled=self.s["history"])
        self.stats = Stats()
        self._overlay = WaveformOverlay(recorder.level, style=self.s["overlay_style"])
        self._ai = aicleanup.AICleanup()

        self.recording = False
        self.busy = False
        self._session = 0
        self._start_time = 0.0
        self._warned_silent = False
        self._warned_limit = False
        self._last_tap_retry = 0.0
        self._prev_muted = None
        self._ax_ok = False
        self._tick_count = 0
        self._history_dirty = True
        self._devices_sig = None
        self._activity = None

        self._jobs = queue.Queue()
        threading.Thread(target=self._worker_loop, name="audio-worker", daemon=True).start()

        self._hotkey = HotkeyMonitor(self._start, self._stop, self._cancel,
                                     on_paste_last=self._paste_last,
                                     is_recording=lambda: self.recording)
        self._build_menu()
        self.apply_hotkey()
        self._keep_awake()
        if self.s["hotkey"] == "fn":
            self._check_fn_setting(alert=False)

        self._clock = rumps.Timer(self._tick, 1)
        self._clock.start()
        if self.s["ai_cleanup"]:
            self._ai.warm()

    # ================================================================ menu ==
    def _build_menu(self):
        self._status_item = rumps.MenuItem("Status: …")
        self._stats_item = rumps.MenuItem(self.stats.summary())
        self._paste_item = rumps.MenuItem("Paste last transcript   ⌃⌘V",
                                          callback=lambda _: self._paste_last())
        self._start_item = rumps.MenuItem("Start dictation", callback=self._on_start_clicked)

        self._history_menu = rumps.MenuItem("History")
        self._mic_menu = rumps.MenuItem("Microphone")
        self._shortcut_menu = rumps.MenuItem("Shortcut")
        for it in _radio(SHORTCUTS, self.s["hotkey"], self._on_shortcut_clicked):
            self._shortcut_menu.add(it)
        self._mode_menu = rumps.MenuItem("Mode")
        for it in _radio(MODES, self.s["mode"], self._on_mode_clicked):
            self._mode_menu.add(it)
        self._lang_menu = rumps.MenuItem("Language")
        for it in _radio(LANGUAGES, self.s["language"], self._on_language_clicked):
            self._lang_menu.add(it)
        self._model_menu = rumps.MenuItem("Speech model")
        for it in _radio(MODELS, self.s["model"], self._on_model_clicked):
            self._model_menu.add(it)

        self._format_menu = rumps.MenuItem("Smart formatting")
        for key, (label, _default) in TOGGLES.items():
            it = rumps.MenuItem(label, callback=self._on_toggle_setting)
            it.state = 1 if self.s[key] else 0
            it._key = key
            self._format_menu.add(it)

        self._style_menu = rumps.MenuItem("Pill style")
        for it in _radio(OVERLAY_STYLES, self.s["overlay_style"], self._on_overlay_clicked):
            self._style_menu.add(it)

        def check(label, key, cb):
            it = rumps.MenuItem(label, callback=cb)
            it.state = 1 if self.s[key] else 0
            return it

        self._perms_item = rumps.MenuItem("Permissions: checking…")
        self.menu = [
            self._status_item,
            self._start_item,
            self._paste_item,
            self._history_menu,
            self._stats_item,
            None,
            self._shortcut_menu,
            self._mode_menu,
            self._mic_menu,
            self._lang_menu,
            self._model_menu,
            None,
            self._format_menu,
            rumps.MenuItem("Dictionary & snippets…", callback=self._on_edit_dictionary),
            None,
            check("Play start/stop sounds", "sounds", self._on_sounds_clicked),
            check("Mute other audio while dictating", "mute", self._on_mute_clicked),
            check("Show recording pill", "waveform", self._on_waveform_clicked),
            self._style_menu,
            None,
            self._perms_item,
            rumps.MenuItem("Grant / re-check permissions…", callback=self._on_grant_clicked),
            rumps.MenuItem("Open log", callback=lambda _: subprocess.Popen(["open", LOG_PATH])),
            rumps.MenuItem("Quit", callback=self._on_quit),
        ]
        self._refresh_devices(force=True)
        self._refresh_history()

    def _set_radio(self, menu, value):
        for it in menu.values():
            if hasattr(it, "_value"):
                it.state = 1 if it._value == value else 0

    def _refresh_devices(self, force=False):
        """Rebuild the Microphone submenu when devices come and go."""
        if not force and (self.recording or self.recorder.is_open):
            return
        rescan()
        devices = [name for _i, name in list_input_devices()]
        sig = (tuple(devices), default_input_name(), self.s["device"])
        if sig == self._devices_sig and not force:
            return
        self._devices_sig = sig
        menu = self._mic_menu
        if len(menu):
            menu.clear()
        items = [("System default" + (f" ({sig[1]})" if sig[1] else ""), None)]
        seen = set()
        for name in devices:
            if name not in seen:
                seen.add(name)
                items.append((name, name))
        saved = self.s["device"]
        if saved and saved not in seen:
            items.append((f"{saved} (not connected)", saved))
        for it in _radio(items, saved, self._on_device_clicked):
            menu.add(it)

    def _refresh_history(self):
        menu = self._history_menu
        if len(menu):
            menu.clear()
        items = self.history.items[:HISTORY_MENU]
        if not items:
            menu.add(rumps.MenuItem("(nothing yet)"))
        for i, entry in enumerate(items):
            preview = " ".join(entry["text"].split())
            if len(preview) > HISTORY_PREVIEW:
                preview = preview[:HISTORY_PREVIEW - 1] + "…"
            it = rumps.MenuItem(preview, callback=self._on_history_clicked)
            it._index = i
            menu.add(it)
        menu.add(None)
        keep = rumps.MenuItem("Save history on this Mac", callback=self._on_history_toggle)
        keep.state = 1 if self.s["history"] else 0
        menu.add(keep)
        menu.add(rumps.MenuItem("Clear history", callback=self._on_clear_history))
        self._paste_item.set_callback(
            (lambda _: self._paste_last()) if self.history.last() else None)

    # ========================================================== settings ==
    def _save(self):
        prefs.save(dict(self.s))

    def apply_hotkey(self):
        try:
            self._hotkey.set_combo(self.s["hotkey"], self.s["mode"])
        except ValueError as exc:
            log.error(str(exc))
            self.s["hotkey"] = "cmd+alt"
            self._hotkey.set_combo(self.s["hotkey"], self.s["mode"])

    def _on_shortcut_clicked(self, sender):
        self.s["hotkey"] = sender._value
        self._set_radio(self._shortcut_menu, sender._value)
        self.apply_hotkey()
        self._status = f"shortcut: {sender.title}"
        self._save()
        if sender._value == "fn":
            self._check_fn_setting(alert=True)

    def _check_fn_setting(self, alert=False):
        usage = fn_key_setting()
        if usage in (None, 0):
            return
        what = {1: "change the input source", 2: "show Emoji & Symbols",
                3: "start Apple Dictation"}.get(usage, "do something else")
        self._status = "set 🌐 key to 'Do Nothing' in Keyboard settings"
        if alert:
            rumps.alert(
                title="One setting for the Fn (🌐) shortcut",
                message=(f"Right now macOS uses the 🌐 key to {what}, which will fire "
                         "every time you dictate.\n\nOpen System Settings ▸ Keyboard and set "
                         "“Press 🌐 key to” → “Do Nothing”."))
            subprocess.Popen(["open", "x-apple.systempreferences:com.apple.Keyboard-Settings.extension"])

    def _on_mode_clicked(self, sender):
        self.s["mode"] = sender._value
        self._set_radio(self._mode_menu, sender._value)
        self.apply_hotkey()
        self._status = f"mode: {sender._value}"
        self._save()

    def _on_device_clicked(self, sender):
        self.s["device"] = sender._value
        self.recorder.device = sender._value
        self._set_radio(self._mic_menu, sender._value)
        self._status = f"mic: {device_name(sender._value)}"
        log.info(f"input device set to {sender._value!r}")
        self._save()

    def _on_language_clicked(self, sender):
        self.s["language"] = sender._value
        self._set_radio(self._lang_menu, sender._value)
        log.info(f"language set to {sender._value!r}")
        self._save()

    def _on_model_clicked(self, sender):
        name = sender._value
        if name == self.s["model"] and self._model_ready:
            return
        self.s["model"] = name
        self._set_radio(self._model_menu, name)
        self._save()
        self.load_model_async(name)

    def _on_toggle_setting(self, sender):
        self.s[sender._key] = not self.s[sender._key]
        sender.state = 1 if self.s[sender._key] else 0
        self._save()
        if sender._key == "ai_cleanup" and self.s["ai_cleanup"]:
            self._status = "AI cleanup: checking Apple Intelligence…"
            threading.Thread(target=self._check_ai, args=(sender,), name="ai-check",
                             daemon=True).start()

    def _check_ai(self, item):
        """Background: confirm the on-device model is usable; if not, turn the
        toggle back off and say why (on the main thread)."""
        ok, reason = self._ai.availability()
        log.info(f"AI cleanup availability: {ok} ({reason})")
        from PyObjCTools import AppHelper
        if ok:
            self._status = "AI cleanup on (on-device)"
            self._ai.warm()
            return

        def off():
            self.s["ai_cleanup"] = False
            item.state = 0
            self._save()
            self._status = f"AI cleanup unavailable: {reason}"
            rumps.alert(title="AI cleanup isn't available on this Mac",
                        message=(f"{reason[:1].upper()}{reason[1:]}.\n\nAI cleanup uses Apple's "
                                 "on-device model (Apple Intelligence, macOS 26+ on Apple "
                                 "silicon). Turn on Apple Intelligence in System Settings, "
                                 "then try again. Rule-based clean-up keeps working."))
        AppHelper.callAfter(off)

    def _on_overlay_clicked(self, sender):
        self.s["overlay_style"] = sender._value
        self._overlay.set_style(sender._value)
        self._set_radio(self._style_menu, sender._value)
        self._save()

    def _on_sounds_clicked(self, sender):
        self.s["sounds"] = not self.s["sounds"]
        sender.state = 1 if self.s["sounds"] else 0
        if self.s["sounds"]:
            play_sound(START_SOUND)
        self._save()

    def _on_mute_clicked(self, sender):
        self.s["mute"] = not self.s["mute"]
        sender.state = 1 if self.s["mute"] else 0
        self._save()

    def _on_waveform_clicked(self, sender):
        self.s["waveform"] = not self.s["waveform"]
        sender.state = 1 if self.s["waveform"] else 0
        if not self.s["waveform"]:
            self._overlay.hide()
        elif self.recording:
            self._overlay.show()
        self._save()

    def _on_edit_dictionary(self, _):
        subprocess.Popen(["open", "-t", DICTIONARY_PATH])
        self._status = "edit, save — picked up on your next dictation"

    def _on_history_clicked(self, sender):
        items = self.history.items
        if sender._index < len(items):
            self.injector.copy(items[sender._index]["text"])
            self._status = "copied from history"

    def _on_history_toggle(self, sender):
        self.s["history"] = not self.s["history"]
        self.history.set_enabled(self.s["history"])
        self._history_dirty = True
        self._save()

    def _on_clear_history(self, _):
        self.history.clear()
        self._history_dirty = True
        self._status = "history cleared"

    def _on_grant_clicked(self, _):
        self.check_permissions(prompt=True, open_settings=True)

    def _on_quit(self, _):
        if self.recording:
            self._cancel()
        self._hotkey.stop()
        rumps.quit_application()

    def check_permissions(self, prompt=False, open_settings=False):
        """Check Mic + Accessibility. Returns (accessibility_ok, mic_status)."""
        ax = perms.accessibility_trusted(prompt=False)
        mic = perms.microphone_status()
        self._ax_ok = ax
        self._perms_item.title = f"Permissions: {perms.summary()}"
        log.info(f"permissions: {perms.summary()}")
        if prompt and not ax:
            perms.accessibility_trusted(prompt=True)
        if prompt and mic in ("notDetermined", "unknown"):
            perms.request_microphone()
        if open_settings and not ax:
            perms.open_settings("Privacy_Accessibility")
        if open_settings and mic == "denied":
            perms.open_settings("Privacy_Microphone")
        return ax, mic

    # ====================================================== model loading ==
    def load_model_async(self, name):
        self._status = f"loading {name}… (first time downloads it)"
        log.info(f"loading model {name}")

        def run():
            try:
                t = self._load_model(name)
                self.transcriber = t        # swap atomically; old one keeps working until now
                self._model_ready = True
                self._status = "ready"
                log.info(f"speech model ready: {name}")
            except Exception as exc:
                log.exception("model load failed")
                if self.transcriber is None:
                    self._model_ready = False
                self._status = f"model {name} failed: {exc}"
        threading.Thread(target=run, name="model-loader", daemon=True).start()

    def set_transcriber(self, transcriber):
        self.transcriber = transcriber
        self._model_ready = True
        self._status = "ready"

    # ============================================ recording state machine ==
    # These three are called on the main thread (hotkey tap / menu / timer)
    # and must return immediately; blocking work goes to the audio worker.
    def _start(self):
        if self.recording or self.busy:
            return False
        if not self._model_ready:
            self._status = "still loading the speech model — one moment…"
            self._overlay.message("Loading speech model…", 1.8)
            return False
        self.recording = True
        self._session += 1
        self._start_time = time.time()
        self._warned_silent = False
        self._warned_limit = False
        if self.s["waveform"]:
            self._overlay.show()
        if self.s["ai_cleanup"]:
            self._ai.warm()             # load the model while you talk
        self._jobs.put((self._job_open, self._session))
        return True

    def _stop(self):
        if not self.recording:
            return
        self.recording = False
        self.busy = True
        self._hotkey.reset()
        if self.s["waveform"]:
            self._overlay.begin_loading()
        self._jobs.put((self._job_finish, self._session, False, time.time() - self._start_time))

    def _cancel(self):
        if not self.recording:
            return
        self.recording = False
        self._hotkey.reset()
        if self.s["waveform"]:
            self._overlay.message("Cancelled", 1.0)
        self._status = "cancelled"
        self._jobs.put((self._job_finish, self._session, True, time.time() - self._start_time))

    def _on_start_clicked(self, _):
        if self.recording:
            self._stop()
        else:
            self._start()

    def _paste_last(self):
        text = self.history.last()
        if not text:
            self._overlay.message("Nothing to paste yet", 1.5)
            return
        self._jobs.put((self._job_paste_last, text))

    # ========================================================== worker ==
    def _worker_loop(self):
        while True:
            job, *args = self._jobs.get()
            try:
                job(*args)
            except Exception:
                log.exception(f"worker job {getattr(job, '__name__', job)} failed")

    def _job_open(self, session):
        if session != self._session or not self.recording:
            return                      # cancelled before the mic even opened
        try:
            self.recorder.start()
        except Exception as exc:
            log.exception("failed to start recording")
            self.recording = False
            self._status = f"mic error: {exc}"
            self._overlay.message("Couldn't open the microphone", 2.5)
            from PyObjCTools import AppHelper
            AppHelper.callAfter(self._hotkey.reset)
            return
        if session != self._session or not self.recording:
            return
        if self.s["sounds"]:
            play_sound(START_SOUND)
        if self.s["mute"]:
            time.sleep(0.25)            # let the start cue finish before muting
            if session == self._session and self.recording:
                self._prev_muted = get_output_muted()
                if self._prev_muted is False:
                    set_output_muted(True)
                else:
                    self._prev_muted = None   # already muted / unknown: leave it alone

    def _restore_output(self):
        if self._prev_muted is False:
            set_output_muted(False)
        self._prev_muted = None

    def _job_finish(self, session, cancelled, seconds):
        try:
            audio = self.recorder.stop() if self.recorder.is_open else np.zeros(0, np.float32)
        except Exception:
            log.exception("failed to stop recording cleanly")
            audio = np.zeros(0, np.float32)
        self._restore_output()
        if cancelled:
            if self.s["sounds"]:
                play_sound(CANCEL_SOUND)
            log.info(f"dictation cancelled after {seconds:.1f}s")
            return
        if self.s["sounds"]:
            play_sound(STOP_SOUND)
        try:
            self._transcribe_and_insert(audio, seconds)
        finally:
            self.busy = False

    def _job_paste_last(self, text):
        try:
            self.injector.insert(text)
            self._status = "pasted last transcript"
        except Exception as exc:
            self._status = f"paste failed: {exc}"

    def _transcribe_and_insert(self, audio, seconds):
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if audio.size == 0 or peak < SILENCE_PEAK:
            self._status = "no audio — check mic & mic permission"
            log.warning(f"no/low audio (size={audio.size}, peak={peak:.4f})")
            self._overlay.message("No audio — check the microphone", 2.5)
            return

        lang_setting = self.s["language"]
        forced, allowed = None, None
        if lang_setting and lang_setting.startswith("auto:"):
            allowed = [x for x in lang_setting[5:].split(",") if x]
        elif lang_setting:
            forced = lang_setting

        self.dictionary.reload()
        t0 = time.time()
        transcriber = self.transcriber
        raw = transcriber.transcribe(audio, language=forced, allowed_languages=allowed,
                                     vocabulary=self.dictionary.vocabulary())
        lang = transcriber.last_language
        text = textproc.process(
            raw, language=lang,
            remove_filler_words=self.s["fillers"], voice_commands=self.s["commands"],
            words=self.dictionary.words, corrections=self.dictionary.corrections,
            snippets=self.dictionary.snippets)
        log.info(f"transcribed in {time.time() - t0:.2f}s [{lang}] raw={raw!r} -> {text!r}")
        if not text.strip():
            self._status = "no speech detected"
            self._overlay.finish()
            return

        ctx = axcontext.focused_context() if self._ax_ok else axcontext.Context()
        log.info(f"context: app={ctx.bundle_id} role={ctx.role} field={ctx.text_field} "
                 f"secure={ctx.secure} before={ctx.before!r} after={ctx.after!r}")
        if ctx.secure:
            self.injector.copy(text)
            self._overlay.message("Password field — copied instead (⌘V)", 3.0)
            self._status = "password field: copied, not pasted"
            self._record(text, raw, lang, seconds, ctx)
            return

        if self.s["ai_cleanup"]:
            text = self._ai_clean(text, lang, ctx)

        final = text
        if self.s["context"]:
            final = textproc.apply_context(
                text, ctx.before, ctx.after,
                casual=self.s["casual"] and ctx.bundle_id in textproc.CHAT_BUNDLES)
        elif self.s["casual"] and ctx.bundle_id in textproc.CHAT_BUNDLES:
            final = textproc.apply_context(text, casual=True)

        no_field = ctx.text_field is False
        self._record(text, raw, lang, seconds, ctx)
        try:
            self.injector.insert(final, keep_on_clipboard=no_field)
        except Exception as exc:
            self.injector.copy(text)
            self._status = f"paste failed ({exc}) — text is on the clipboard"
            self._overlay.message("Couldn't paste — copied, press ⌘V", 3.0)
            return
        if no_field:
            self._status = "no text field focused — copied"
            self._overlay.message("No text field — copied, press ⌘V", 3.0)
        elif not self._ax_ok:
            self._status = "copied — press ⌘V (grant Accessibility to auto-paste)"
            self._overlay.finish()
        else:
            self._status = f'inserted: "{text[:40]}"'
            self._overlay.finish()

    def _ai_clean(self, text, lang, ctx):
        """Optional on-device LLM pass. Always returns usable text: the
        rule-based `text` itself on any failure or timeout."""
        chat = self.s["casual"] and ctx.bundle_id in textproc.CHAT_BUNDLES
        try:
            res = self._ai.clean(text, language=lang, style="casual" if chat else "default")
        except Exception:                 # belt and braces: clean() shouldn't raise
            log.exception("AI cleanup crashed")
            return text
        if res.used:
            log.info(f"AI cleanup ok in {res.latency:.2f}s: {text!r} -> {res.text!r}")
        else:
            log.info(f"AI cleanup skipped after {res.latency:.2f}s ({res.reason}); "
                     f"using rule-based text")
        return res.text

    def _record(self, text, raw, lang, seconds, ctx):
        self.history.add(text, raw=raw, lang=lang, app=ctx.bundle_id, secs=round(seconds, 1))
        self.stats.add(len(text.split()), seconds)
        self._history_dirty = True

    # ================================================ app nap / keep awake ==
    def _keep_awake(self):
        """Menu-bar apps are App Nap candidates: timers (double-tap window,
        auto-stop) get coalesced and the event tap can lag. Opt out — it costs
        nothing while idle, and still lets the Mac sleep."""
        try:
            from Foundation import NSProcessInfo
            opts = 0x00FFFFFF & ~(1 << 20)   # NSActivityUserInitiatedAllowingIdleSystemSleep
            self._activity = NSProcessInfo.processInfo().beginActivityWithOptions_reason_(
                opts, "Responding to the dictation shortcut")
        except Exception:
            log.exception("could not disable App Nap")

    # ======================================================= main tick ==
    def _tick(self, _):
        now = time.time()
        if self.busy or not self._model_ready:
            self.title = self.BUSY_ICON
        elif self.recording:
            elapsed = int(now - self._start_time)
            minutes, seconds = divmod(elapsed, 60)
            self.title = f"{self.REC_ICON} {minutes:02d}:{seconds:02d}"
            max_time = self.s["max_time"]
            if max_time >= 120 and elapsed >= max_time - 60 and not self._warned_limit:
                self._warned_limit = True
                self._overlay.message("1 minute left", 2.0, resume_wave=True)
            if max_time and elapsed >= max_time:
                self._stop()
            elif (not self._warned_silent and elapsed >= SILENT_MIC_WARN_AFTER
                  and self.recorder.is_open and self.recorder.max_level < SILENCE_PEAK):
                self._warned_silent = True
                mic = self.recorder.active_device or "the microphone"
                self._overlay.message(f"No sound from {mic}", 2.5, resume_wave=True)
        else:
            self.title = self.IDLE_ICON
        self._start_item.title = "Stop dictation" if self.recording else "Start dictation"
        self._status_item.title = f"Status: {self._status}"

        if self._history_dirty:
            self._history_dirty = False
            self._refresh_history()
            self._stats_item.title = self.stats.summary()

        self._tick_count += 1
        if self._tick_count % 3 == 0:
            ax = perms.accessibility_trusted(prompt=False)
            need_tap = not self._hotkey.uses_tap and now - self._last_tap_retry > 30
            if ax and (not self._ax_ok or need_tap) and not self.recording:
                log.info("Accessibility available — (re)installing the shortcut tap")
                self._last_tap_retry = now
                self.apply_hotkey()
            self._ax_ok = ax
            self._perms_item.title = f"Permissions: {perms.summary()}"
            if self.dictionary.error:
                self._status = self.dictionary.error
        if self._tick_count % 5 == 0 and not self.recording and not self.busy:
            self._refresh_devices()
