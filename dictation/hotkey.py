"""Global shortcuts: a pure trigger state machine + a Quartz event tap.

Activation modes (apply to whichever shortcut is selected):

  * smart  — Wispr Flow style. HOLD the shortcut to talk, release to insert
             (push-to-talk). DOUBLE-TAP it to go hands-free; tap once more to
             stop. A single accidental tap is discarded.
  * toggle — tap to start, tap again to stop (a re-tap within 0.5 s of starting
             cancels instead — "oops, didn't mean to").
  * hold   — push-to-talk only.

Everywhere: Esc cancels a recording, ⌃⌘V pastes the last transcript, and if the
shortcut turns out to be part of another shortcut (e.g. ⌘⌥ + Esc, Fn + Delete)
the recording is cancelled silently.

Plumbing: one active CGEventTap on the main run loop sees key-down/up and
modifier changes. Unlike NSEvent monitors it can SWALLOW events, so ⌥Space no
longer types a non-breaking space and ⌘D no longer bookmarks the page in Chrome.
If the tap can't be created (no Accessibility yet) we fall back to observe-only
NSEvent monitors with the same logic.

Modifiers are matched by the device-dependent bits of the event flags, so left
and right sides are distinguished: 'shift_l' = physical left shift, while bare
'shift' matches either side.
"""

import time

from .log import get_logger

log = get_logger()

# ------------------------------------------------------------- key tables ----

# Device-dependent modifier bits (same values in NSEvent and CGEvent flags).
SIDE_FLAG = {
    "cmd_l": 0x08, "cmd_r": 0x10,
    "ctrl_l": 0x01, "ctrl_r": 0x2000,
    "alt_l": 0x20, "alt_r": 0x40,
    "shift_l": 0x02, "shift_r": 0x04,
}
BASE_FLAG = {"cmd": 0x18, "ctrl": 0x2001, "alt": 0x60, "shift": 0x06}
ALL_SIDE_BITS = 0x18 | 0x2001 | 0x60 | 0x06
FN_FLAG = 0x800000           # kCGEventFlagMaskSecondaryFn
FN_KEYCODES = (63, 179)      # kVK_Function / the 🌐 globe key on newer keyboards
RIGHT_CMD_KEYCODE = 54

# macOS virtual keycodes (ANSI positions) for the non-modifier part of a combo.
KEYCODE = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8,
    "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17,
    "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "9": 25, "7": 26,
    "8": 28, "0": 29, "o": 31, "u": 32, "i": 34, "p": 35, "l": 37, "j": 38,
    "k": 40, "n": 45, "m": 46, "return": 36, "tab": 48, "space": 49,
    "escape": 53, "esc": 53, "f13": 105, "f14": 107, "f15": 113,
}
ESC = 53
V_KEY = 9
PASTE_LAST_MASK = BASE_FLAG["ctrl"] | BASE_FLAG["cmd"]   # ⌃⌘V

# ------------------------------------------------------------ the trigger ----


class Trigger:
    """Parsed shortcut spec. Kinds: 'mods' (modifier-only chord), 'key'
    (modifiers + one key), 'fn', 'double_cmd'."""

    def __init__(self, spec: str):
        self.spec = spec
        self.kind = "mods"
        self.required = []          # list of bitmasks; each needs ≥1 bit down
        self.keycode = None
        if spec == "double_cmd":
            self.kind = "double_cmd"
            return
        tokens = [t.strip().lower() for t in spec.split("+") if t.strip()]
        if tokens == ["fn"]:
            self.kind = "fn"
            return
        for tok in tokens:
            if tok in SIDE_FLAG:
                self.required.append(SIDE_FLAG[tok])
            elif tok in BASE_FLAG:
                self.required.append(BASE_FLAG[tok])
            elif tok in KEYCODE:
                self.keycode = KEYCODE[tok]
                self.kind = "key"
            else:
                raise ValueError(f"unknown key {tok!r} in shortcut {spec!r}")
        if self.kind == "mods" and not self.required:
            raise ValueError(f"empty shortcut {spec!r}")
        self.allowed = 0
        for m in self.required:
            self.allowed |= m

    def mods_ok(self, flags: int) -> bool:
        """All required modifiers down and no other modifier (⌘⌥⇧ ≠ ⌘⌥)."""
        if not all(flags & m for m in self.required):
            return False
        return not (flags & ALL_SIDE_BITS & ~self.allowed)


# --------------------------------------------------------- state machine ----

IDLE, HOLD, TAPPED, LOCK_DOWN, LOCKED = "idle", "hold", "tapped", "lock_down", "locked"


class TriggerMachine:
    """Turns trigger press/release (+ 'another key was pressed') into
    start/stop/cancel. Pure Python: time and timers are injected, so it is
    unit-tested in tests/test_hotkey.py."""

    TAP_MAX = 0.30        # shorter press = a tap, not push-to-talk
    DOUBLE_TAP = 0.45     # second tap within this after the first = hands-free
    QUICK_CANCEL = 0.50   # toggle re-tapped this fast = cancel, not stop
    CHORD_CANCEL = 1.00   # another key this soon after pressing = it was a shortcut

    def __init__(self, start, stop, cancel, schedule, clock=time.monotonic):
        self._start, self._stop, self._cancel = start, stop, cancel
        self._schedule = schedule
        self._clock = clock
        self.mode = "smart"
        self.state = IDLE
        self._t_press = 0.0
        self._t_start = 0.0
        self._gen = 0
        self._swallow_release = False

    def reset(self):
        """The app ended the recording itself (menu, time limit, error)."""
        self.state = IDLE
        self._gen += 1

    @property
    def recording(self):
        return self.state != IDLE

    def _begin(self, now, state):
        if not self._start():
            self.state = IDLE
            return
        self._t_press = self._t_start = now
        self.state = state

    def _end(self, cancel=False):
        self.state = IDLE
        self._gen += 1
        (self._cancel if cancel else self._stop)()

    # ---- events ----
    def press(self):
        now = self._clock()
        if self.state == IDLE:
            first = {"smart": HOLD, "hold": HOLD, "toggle": LOCK_DOWN}[self.mode]
            self._begin(now, first)
        elif self.state == TAPPED:          # second tap → hands-free
            self.state = LOCK_DOWN
            self._gen += 1
        elif self.state == LOCKED:          # tap while hands-free → done
            quick = self.mode == "toggle" and now - self._t_start < self.QUICK_CANCEL
            self._swallow_release = True
            self._end(cancel=quick)
        self._t_press = now

    def release(self):
        now = self._clock()
        if self._swallow_release:
            self._swallow_release = False
            return
        if self.state == HOLD:
            if self.mode == "smart" and now - self._t_press < self.TAP_MAX:
                self.state = TAPPED
                self._gen += 1
                gen = self._gen
                self._schedule(self.DOUBLE_TAP, lambda: self._tap_timeout(gen))
            else:
                self._end()
        elif self.state == LOCK_DOWN:
            self.state = LOCKED

    def other_key(self):
        """A non-trigger key went down while the trigger was held."""
        if self.state in (HOLD, LOCK_DOWN) and self._clock() - self._t_press < self.CHORD_CANCEL:
            self._end(cancel=True)

    def escape(self) -> bool:
        if self.state == IDLE:
            return False
        self._end(cancel=True)
        return True

    def _tap_timeout(self, gen):
        if gen == self._gen and self.state == TAPPED:
            self._end(cancel=True)          # lone accidental tap: discard


class DoubleCmdMachine(TriggerMachine):
    """Legacy mode: double-tap Right ⌘ starts, a single tap stops."""

    def press(self):
        now = self._clock()
        if self.state == LOCKED:
            self._end()
        elif now - self._t_press < 0.5:
            self._begin(now, LOCKED)
        self._t_press = now

    def release(self):
        pass

    def other_key(self):
        self._t_press = 0.0   # ⌘C, ⌘V… aren't double-taps


# -------------------------------------------------------- event plumbing ----


class HotkeyMonitor:
    """Feeds keyboard events into a TriggerMachine. Must be created and
    configured on the main thread (the tap lives on the main run loop)."""

    def __init__(self, on_start, on_stop, on_cancel, on_paste_last=None,
                 is_recording=None):
        from PyObjCTools import AppHelper

        def schedule(delay, fn):
            AppHelper.callLater(delay, fn)

        self._callbacks = (on_start, on_stop, on_cancel, schedule)
        self._on_paste_last = on_paste_last
        self._is_recording = is_recording or (lambda: False)
        self.trigger = None
        self.machine = None
        self._down = False
        self._tap = self._source = None
        self._global = self._local = None

    # ---- configuration ----
    def set_combo(self, spec, mode="smart"):
        if spec.startswith("hold:"):          # legacy "hold:cmd+d"
            spec, mode = spec[len("hold:"):], "hold"
        if mode == "hybrid":
            mode = "smart"
        self._remove()
        self.trigger = Trigger(spec)
        cls = DoubleCmdMachine if self.trigger.kind == "double_cmd" else TriggerMachine
        self.machine = cls(*self._callbacks)
        self.machine.mode = mode if mode in ("smart", "toggle", "hold") else "smart"
        self._down = False
        if not self._install_tap():
            self._install_monitors()
        log.info(f"hotkey set to {spec} (mode={self.machine.mode}, "
                 f"{'event tap' if self._tap else 'observe-only monitors'})")

    def reset(self):
        if self.machine is not None:
            self.machine.reset()

    def stop(self):
        self._remove()

    # ---- the core: one handler for both the tap and the monitors ----
    def handle(self, kind, keycode, flags, autorepeat=False) -> bool:
        """kind: 'down' | 'up' | 'flags'. Returns True to swallow the event."""
        trig, m = self.trigger, self.machine
        if trig is None:
            return False
        recording = m.recording or self._is_recording()

        if kind == "down" and keycode == ESC and recording:
            m.escape()
            return True
        if (kind == "down" and keycode == V_KEY and self._on_paste_last
                and (flags & PASTE_LAST_MASK) and not (flags & BASE_FLAG["alt"])
                and all(flags & b for b in (BASE_FLAG["ctrl"], BASE_FLAG["cmd"]))):
            if not autorepeat:
                self._on_paste_last()
            return True

        if trig.kind == "key":
            hit = keycode == trig.keycode
            if kind == "down" and hit and trig.mods_ok(flags):
                if not autorepeat and not self._down:
                    self._down = True
                    m.press()
                return True                     # swallow the combo (and repeats)
            if kind == "up" and hit and self._down:
                self._down = False
                m.release()
                return True
            if kind == "flags" and self._down and not trig.mods_ok(flags):
                self._down = False
                m.release()
                return False
            if kind == "down" and self._down:
                m.other_key()
            return False

        if trig.kind == "fn":
            if kind == "flags" and keycode in FN_KEYCODES:
                down = bool(flags & FN_FLAG)
                if down and not self._down:
                    self._down = True
                    m.press()
                elif not down and self._down:
                    self._down = False
                    m.release()
            elif kind == "down" and self._down:
                m.other_key()                   # Fn+Delete, Fn+arrows, Fn+F-keys…
            elif kind == "flags" and self._down and flags & ALL_SIDE_BITS:
                m.other_key()                   # Fn+⌃… is a system shortcut
            return False

        if trig.kind == "double_cmd":
            if kind == "flags" and keycode == RIGHT_CMD_KEYCODE:
                down = bool(flags & SIDE_FLAG["cmd_r"])
                if down and not self._down:
                    m.press()
                self._down = down
            elif kind == "down":
                m.other_key()
            return False

        # Modifier-only chord.
        if kind == "flags":
            ok = trig.mods_ok(flags)
            extra = bool(flags & ALL_SIDE_BITS & ~trig.allowed)
            if ok and not self._down:
                self._down = True
                m.press()
            elif not ok and self._down:
                self._down = False
                if extra:
                    m.other_key()               # ⌘⌥ became ⌘⌥⇧: another shortcut
                m.release()
        elif kind == "down" and self._down:
            m.other_key()
        return False

    # ---- Quartz event tap (preferred: can swallow) ----
    def _install_tap(self):
        try:
            from Quartz import (
                CFMachPortCreateRunLoopSource, CFRunLoopAddSource, CFRunLoopGetCurrent,
                CGEventMaskBit, CGEventTapCreate, CGEventTapEnable,
                kCFRunLoopCommonModes, kCGEventFlagsChanged, kCGEventKeyDown,
                kCGEventKeyUp, kCGHeadInsertEventTap, kCGSessionEventTap,
                kCGEventTapOptionDefault,
            )
            mask = (CGEventMaskBit(kCGEventKeyDown) | CGEventMaskBit(kCGEventKeyUp)
                    | CGEventMaskBit(kCGEventFlagsChanged))
            tap = CGEventTapCreate(kCGSessionEventTap, kCGHeadInsertEventTap,
                                   kCGEventTapOptionDefault, mask, self._tap_callback, None)
            if not tap:
                log.warning("could not create event tap (Accessibility not granted yet?)")
                return False
            source = CFMachPortCreateRunLoopSource(None, tap, 0)
            CFRunLoopAddSource(CFRunLoopGetCurrent(), source, kCFRunLoopCommonModes)
            CGEventTapEnable(tap, True)
            self._tap, self._source = tap, source
            return True
        except Exception:
            log.exception("event tap setup failed")
            return False

    def _tap_callback(self, _proxy, etype, event, _refcon):
        try:
            from Quartz import (
                CGEventGetFlags, CGEventGetIntegerValueField, CGEventTapEnable,
                kCGEventFlagsChanged, kCGEventKeyDown, kCGEventKeyUp,
                kCGEventTapDisabledByTimeout, kCGEventTapDisabledByUserInput,
                kCGKeyboardEventAutorepeat, kCGKeyboardEventKeycode,
            )
            if etype in (kCGEventTapDisabledByTimeout, kCGEventTapDisabledByUserInput):
                log.warning("event tap was disabled by macOS — re-enabling")
                if self._tap is not None:
                    CGEventTapEnable(self._tap, True)
                return event
            kind = {kCGEventKeyDown: "down", kCGEventKeyUp: "up",
                    kCGEventFlagsChanged: "flags"}.get(etype)
            if kind is None:
                return event
            keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
            flags = int(CGEventGetFlags(event))
            repeat = bool(CGEventGetIntegerValueField(event, kCGKeyboardEventAutorepeat))
            if self.handle(kind, keycode, flags, repeat):
                return None
        except Exception:
            log.exception("event tap callback failed")
        return event

    # ---- NSEvent monitors (fallback: observe-only) ----
    def _install_monitors(self):
        from AppKit import (NSEvent, NSEventMaskFlagsChanged, NSEventMaskKeyDown,
                            NSEventMaskKeyUp)
        mask = NSEventMaskFlagsChanged | NSEventMaskKeyDown | NSEventMaskKeyUp

        def on_event(event):
            try:
                from AppKit import NSEventTypeFlagsChanged, NSEventTypeKeyDown
                t = event.type()
                kind = ("flags" if t == NSEventTypeFlagsChanged
                        else "down" if t == NSEventTypeKeyDown else "up")
                repeat = kind != "flags" and bool(event.isARepeat())
                self.handle(kind, int(event.keyCode()), int(event.modifierFlags()), repeat)
            except Exception:
                log.exception("hotkey monitor failed")

        def on_local(event):
            on_event(event)
            return event

        self._global = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(mask, on_event)
        self._local = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(mask, on_local)

    def _remove(self):
        from AppKit import NSEvent
        for monitor in (self._global, self._local):
            if monitor is not None:
                try:
                    NSEvent.removeMonitor_(monitor)
                except Exception:
                    pass
        self._global = self._local = None
        if self._tap is not None:
            try:
                from Quartz import (CFRunLoopGetCurrent, CFRunLoopRemoveSource,
                                    CGEventTapEnable, kCFRunLoopCommonModes)
                CGEventTapEnable(self._tap, False)
                if self._source is not None:
                    CFRunLoopRemoveSource(CFRunLoopGetCurrent(), self._source,
                                          kCFRunLoopCommonModes)
            except Exception:
                pass
        self._tap = self._source = None

    @property
    def uses_tap(self):
        return self._tap is not None


def fn_key_setting():
    """macOS 'Press 🌐 key to' setting: 0 = Do Nothing (what Fn-as-shortcut
    needs), 1 = Change Input Source, 2 = Show Emoji & Symbols, 3 = Start
    Dictation. Returns None if unknown."""
    try:
        from Foundation import NSUserDefaults
        d = NSUserDefaults.alloc().initWithSuiteName_("com.apple.HIToolbox")
        value = d.objectForKey_("AppleFnUsageType")
        return None if value is None else int(value)
    except Exception:
        return None
