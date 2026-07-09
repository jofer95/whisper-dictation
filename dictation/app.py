"""Menu-bar app (rumps). Global hotkeys come from dictation.hotkey (NSEvent).

Design note: every AppKit/UI mutation happens on the main thread. The hotkey
monitor and the transcription worker only flip plain state flags and drive the
recorder; the 1-second rumps.Timer (`_tick`) is the single place that renders
the menu-bar title and the Status line. This keeps us off the "updating UI from
a background thread" footgun that the original app skated close to.
"""

import threading
import time
from collections import deque

import numpy as np
import rumps

from . import perms
from . import prefs
from .hotkey import HotkeyMonitor
from .log import get_logger
from .overlay import WaveformOverlay
from .recorder import list_input_devices, device_name
from .sound import get_output_muted, set_output_muted, play_sound

log = get_logger()

# Below this peak amplitude we treat the clip as "no audio" (wrong mic / no perm).
SILENCE_PEAK = 0.005

# Audio cues (macOS system sounds) for start/stop of recording.
START_SOUND = "Tink"
STOP_SOUND = "Bottle"

# History of recent dictations kept in the menu (rescue when focus was lost).
HISTORY_SIZE = 3
HISTORY_PREVIEW = 45  # chars shown in the menu label before truncating

# Selectable shortcuts: (menu label, spec). "double_cmd" is the special
# double-tap Right Command mode; everything else is a combo of tokens joined by
# "+". A bare modifier name (cmd/ctrl/alt/shift) matches either side; a sided
# name (shift_l, cmd_r, ...) matches only that side.
SHORTCUTS = [
    ("⌘ + Option", "cmd+alt"),
    ("⌃ + Option", "ctrl+alt"),
    ("⌃ + Left Shift", "ctrl+shift_l"),
    ("⌘ + Shift", "cmd+shift"),
    ("⌥ + Space", "alt+space"),
    ("Double-tap Right ⌘", "double_cmd"),
    ("⌘ + D (hold to talk)", "hold:cmd+d"),
]


class StatusBarApp(rumps.App):
    IDLE_ICON = "🎙️"
    REC_ICON = "🔴"
    BUSY_ICON = "⏳"

    def __init__(self, recorder, transcriber, injector,
                 languages=None, language=None, max_time=60, hotkey="cmd+alt",
                 mute_while_recording=True, play_sounds=True, show_waveform=True):
        super().__init__("Whisper Dictation", title=self.IDLE_ICON, quit_button="Quit")
        self.recorder = recorder
        self.transcriber = transcriber
        self.injector = injector
        self.languages = languages
        self.current_language = language
        self.max_time = max_time
        self.mute_while_recording = mute_while_recording
        self.play_sounds = play_sounds
        self.show_waveform = show_waveform
        self._overlay = WaveformOverlay(recorder.level)

        self.recording = False
        self.busy = False
        self._start_time = 0.0
        self._status = "ready"
        self.hotkey_spec = hotkey
        self._prev_muted = None
        self._ax_ok = False
        self._hotkey = HotkeyMonitor(self.toggle, is_recording=lambda: self.recording)

        # --- microphone submenu (System Default + every input device) ---
        self._device_by_label = {"System Default": None}
        sys_item = rumps.MenuItem("System Default", callback=self._on_device_clicked)
        sys_item.state = 1 if self.recorder.device is None else 0
        device_items = [sys_item, None]
        seen = set()
        for idx, name in list_input_devices():
            label = name if name not in seen else f"{name}  [{idx}]"
            seen.add(name)
            self._device_by_label[label] = idx
            item = rumps.MenuItem(label, callback=self._on_device_clicked)
            item.state = 1 if idx == self.recorder.device else 0
            device_items.append(item)

        # --- shortcut submenu ---
        self._spec_by_label = {}
        shortcut_items = []
        for label, spec in SHORTCUTS:
            self._spec_by_label[label] = spec
            item = rumps.MenuItem(label, callback=self._on_shortcut_clicked)
            item.state = 1 if spec == hotkey else 0
            shortcut_items.append(item)

        # --- language submenu (Auto is best for mixed ES/EN; never translates) ---
        self._lang_by_label = {"Auto (mixed languages)": None, "Español": "es", "English": "en"}
        if languages:
            for lang in languages:
                self._lang_by_label.setdefault(lang, lang)
        lang_items = []
        for label, code in self._lang_by_label.items():
            it = rumps.MenuItem(label, callback=self._on_language_clicked)
            it.state = 1 if code == self.current_language else 0
            lang_items.append(it)

        self._status_item = rumps.MenuItem("Status: ready", callback=None)
        self._perms_item = rumps.MenuItem("Permissions: checking…", callback=None)
        self._perms_summary = "checking…"
        self._tick_count = 0

        self._mute_item = rumps.MenuItem(
            "Mute audio while recording", callback=self._on_mute_toggle_clicked)
        self._mute_item.state = 1 if self.mute_while_recording else 0

        self._sounds_item = rumps.MenuItem(
            "Play start/stop sounds", callback=self._on_sounds_toggle_clicked)
        self._sounds_item.state = 1 if self.play_sounds else 0

        self._waveform_item = rumps.MenuItem(
            "Show waveform while recording", callback=self._on_waveform_toggle_clicked)
        self._waveform_item.state = 1 if self.show_waveform else 0

        # Last 3 dictations, newest first. Fixed slots we retitle on the main
        # thread; clicking one copies the full text (rescue for lost focus).
        self._history = deque(maxlen=HISTORY_SIZE)  # newest first, auto-capped
        self._history_dirty = False
        self._history_items = [
            rumps.MenuItem("(empty)", callback=None) for _ in range(HISTORY_SIZE)
        ]

        self.menu = [
            rumps.MenuItem("Start Dictation", callback=self._on_start_clicked),
            rumps.MenuItem("Stop Dictation", callback=self._on_stop_clicked),
            None,
            ("Microphone", device_items),
            ("Shortcut", shortcut_items),
            ("Language", lang_items),
            self._mute_item,
            self._sounds_item,
            self._waveform_item,
            None,
            ("History (click to copy)", self._history_items),
            None,
            self._status_item,
            self._perms_item,
            rumps.MenuItem("Grant / re-check permissions…",
                           callback=self._on_grant_clicked),
            None,
        ]

        self.apply_hotkey(hotkey)

        self._clock = rumps.Timer(self._tick, 1)
        self._clock.start()

    # ---- hotkey management ----
    def apply_hotkey(self, spec):
        self._hotkey.set_combo(spec)
        self.hotkey_spec = spec

    # ---- menu callbacks (main thread) ----
    def _on_start_clicked(self, _):
        self._start()

    def _on_stop_clicked(self, _):
        self._stop()

    def _save_prefs(self):
        dev = None if self.recorder.device is None else device_name(self.recorder.device)
        prefs.save({
            "device": dev,
            "hotkey": self.hotkey_spec,
            "language": self.current_language,
            "mute": self.mute_while_recording,
            "sounds": self.play_sounds,
            "waveform": self.show_waveform,
        })

    def _on_device_clicked(self, sender):
        idx = self._device_by_label.get(sender.title)
        self.recorder.device = idx
        for label in self._device_by_label:
            self.menu["Microphone"][label].state = 1 if label == sender.title else 0
        log.info(f"input device set to '{device_name(idx)}' (index={idx})")
        self._status = f"mic: {device_name(idx)}"
        self._save_prefs()

    def _on_shortcut_clicked(self, sender):
        spec = self._spec_by_label.get(sender.title)
        if spec is None:
            return
        self.apply_hotkey(spec)
        for label in self._spec_by_label:
            self.menu["Shortcut"][label].state = 1 if label == sender.title else 0
        self._status = f"shortcut: {sender.title}"
        self._save_prefs()

    def _on_language_clicked(self, sender):
        self.current_language = self._lang_by_label.get(sender.title)
        for label in self._lang_by_label:
            self.menu["Language"][label].state = 1 if label == sender.title else 0
        log.info(f"language set to {self.current_language!r}")
        self._save_prefs()

    # ---- dictation history (append off-thread, render on the main thread) ----
    def _add_history(self, text):
        """Called from the transcription worker: record the text and mark the
        menu dirty. The actual menu mutation happens in _tick (main thread)."""
        self._history.appendleft(text)  # deque(maxlen) drops the oldest for us
        self._history_dirty = True

    def _refresh_history(self):
        for i, slot in enumerate(self._history_items):
            if i < len(self._history):
                text = self._history[i]
                preview = " ".join(text.split())  # collapse newlines for the label
                if len(preview) > HISTORY_PREVIEW:
                    preview = preview[:HISTORY_PREVIEW - 1] + "…"
                slot.title = preview
                slot.set_callback(self._on_history_clicked)
            else:
                slot.title = "(empty)"
                slot.set_callback(None)

    def _on_history_clicked(self, sender):
        idx = next((i for i, it in enumerate(self._history_items) if it is sender), None)
        if idx is None or idx >= len(self._history):
            return
        self.injector.copy(self._history[idx])
        self._status = "copied from history"
        log.info(f"copied history[{idx}] to clipboard")

    def _on_mute_toggle_clicked(self, sender):
        self.mute_while_recording = not self.mute_while_recording
        sender.state = 1 if self.mute_while_recording else 0
        log.info(f"mute-while-recording = {self.mute_while_recording}")
        self._save_prefs()

    def _on_sounds_toggle_clicked(self, sender):
        self.play_sounds = not self.play_sounds
        sender.state = 1 if self.play_sounds else 0
        log.info(f"play-sounds = {self.play_sounds}")
        if self.play_sounds:
            play_sound(START_SOUND)  # preview when enabling
        self._save_prefs()

    def _on_waveform_toggle_clicked(self, sender):
        self.show_waveform = not self.show_waveform
        sender.state = 1 if self.show_waveform else 0
        log.info(f"show-waveform = {self.show_waveform}")
        if not self.show_waveform:
            self._overlay.hide()  # drop it immediately if we're mid-recording
        elif self.recording:
            self._overlay.show()
        self._save_prefs()

    def _on_grant_clicked(self, _):
        # Explicit user action: prompt AND open the relevant Settings panes.
        self.check_permissions(prompt=True, open_settings=True)

    def check_permissions(self, prompt=False, open_settings=False):
        """Check Mic + Accessibility. Logs state FIRST (always), then optionally
        prompts / deep-links to Settings. Returns (accessibility_ok, mic_status).
        """
        ax = perms.accessibility_trusted(prompt=False)  # non-intrusive read
        mic = perms.microphone_status()
        self._perms_summary = perms.summary()
        self._ax_ok = ax
        log.info(f"permissions: {self._perms_summary}")

        if prompt and not ax:
            perms.accessibility_trusted(prompt=True)      # show the system dialog
        if prompt and mic in ("notDetermined", "unknown"):
            perms.request_microphone()
        if open_settings and not ax:
            perms.open_settings("Privacy_Accessibility")
        if open_settings and mic == "denied":
            perms.open_settings("Privacy_Microphone")
        return ax, mic

    # ---- state transitions (any thread) ----
    def toggle(self):
        log.info(f"toggle fired (was recording={self.recording}, busy={self.busy})")
        if self.recording:
            self._stop()
        else:
            self._start()

    def _start(self):
        if self.recording or self.busy:
            return
        # Cue first, while output isn't muted yet, so it's audible.
        if self.play_sounds:
            play_sound(START_SOUND)
        try:
            self.recorder.start()
        except Exception as exc:
            self._status = f"mic error: {exc}"
            log.exception("failed to start recording")
            return
        self.recording = True
        self._start_time = time.time()
        if self.show_waveform:
            self._overlay.show()
        if self.mute_while_recording:
            self._prev_muted = get_output_muted()
            set_output_muted(True)

    def _stop(self):
        if not self.recording:
            return
        self.recording = False
        # Keep the pill up but flip it to a spinner while we transcribe; the
        # worker hides it via overlay.finish() when it's done.
        if self.show_waveform:
            self._overlay.begin_loading()
        if self.mute_while_recording:
            # restore exactly what the user had before we muted
            set_output_muted(bool(self._prev_muted))
            self._prev_muted = None
        self.busy = True
        audio = self.recorder.stop()
        # Cue after the stream is closed (so it isn't captured) and output restored.
        if self.play_sounds:
            play_sound(STOP_SOUND)
        threading.Thread(
            target=self._transcribe_and_inject, args=(audio,), daemon=True
        ).start()

    def _transcribe_and_inject(self, audio):
        try:
            peak = float(np.max(np.abs(audio))) if audio.size else 0.0
            if audio.size == 0 or peak < SILENCE_PEAK:
                self._status = "no audio — check mic & mic permission"
                log.warning(f"no/low audio (size={audio.size}, peak={peak:.4f})")
                return
            text = self.transcriber.transcribe(audio, self.current_language)
            if not text:
                self._status = "no speech detected"
                log.info("transcription empty")
                return
            # Record before inject: if focus was lost and the paste misses, the
            # text is still recoverable from the History menu.
            self._add_history(text)
            self.injector.insert(text)
            if perms.accessibility_trusted(prompt=False):
                self._status = f'inserted: "{text[:40]}"'
            else:
                self._status = "copied — press ⌘V (grant Accessibility to auto-paste)"
        except Exception as exc:
            self._status = f"error: {exc} (Accessibility?)"
            log.exception("transcribe/inject failed")
        finally:
            self.busy = False
            self._overlay.finish()  # hide the loading pill (hops to main thread)

    # ---- title + status rendering, max_time (main thread) ----
    def _tick(self, _):
        if self.busy:
            self.title = self.BUSY_ICON
        elif self.recording:
            elapsed = int(time.time() - self._start_time)
            minutes, seconds = divmod(elapsed, 60)
            self.title = f"{self.REC_ICON} {minutes:02d}:{seconds:02d}"
            if self.max_time and elapsed >= self.max_time:
                self._stop()
        else:
            self.title = self.IDLE_ICON
        self._status_item.title = f"Status: {self._status}"

        if self._history_dirty:
            self._refresh_history()
            self._history_dirty = False

        # Refresh permissions every ~3s. If Accessibility was just granted,
        # re-register the hotkey monitor so it starts working without a relaunch.
        self._tick_count += 1
        if self._tick_count % 3 == 0:
            ax = perms.accessibility_trusted(prompt=False)
            if ax and not self._ax_ok:
                log.info("Accessibility granted — re-registering hotkey monitor")
                self.apply_hotkey(self.hotkey_spec)
            self._ax_ok = ax
            self._perms_summary = perms.summary()
        self._perms_item.title = f"Permissions: {self._perms_summary}"
