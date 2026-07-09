"""Global hotkey via AppKit NSEvent monitors, plus a Quartz event tap for
push-to-talk ("hold") on key combos.

Two activation modes apply to whatever shortcut is selected:

  * toggle — press once to start, press again to stop.
  * hold   — keep the combo held to dictate; release to stop (push-to-talk).

pynput's event-tap listener does not fire from inside a py2app bundle, so we use
NSEvent global/local monitors. They run on the app's main run loop (which rumps
drives) and only need Accessibility permission.

Monitors are observe-only (they can't swallow the event). For HOLD on a *key*
combo (e.g. ⌘+D) that would also do something in the focused app, we instead
install a CGEventTap that consumes the key-down. Modifier-only hold (e.g. hold
⌘+Option) needs no tap — modifiers alone don't trigger app actions — so it runs
on the plain monitor via the rising/falling edge of the modifier flags.

Modifiers are matched by the device-dependent bits in `modifierFlags`, so left
and right sides are distinguished: 'shift_l' = physical left shift, while bare
'shift' matches either side.
"""

import time

from AppKit import (
    NSEvent,
    NSEventMaskKeyDown,
    NSEventMaskFlagsChanged,
    NSEventTypeKeyDown,
)
from Quartz import (
    CFMachPortCreateRunLoopSource,
    CFRunLoopAddSource,
    CFRunLoopGetCurrent,
    CFRunLoopRemoveSource,
    CGEventGetFlags,
    CGEventGetIntegerValueField,
    CGEventMaskBit,
    CGEventTapCreate,
    CGEventTapEnable,
    kCFRunLoopCommonModes,
    kCGEventFlagMaskAlternate,
    kCGEventFlagMaskCommand,
    kCGEventFlagMaskControl,
    kCGEventFlagMaskShift,
    kCGEventFlagsChanged,
    kCGEventKeyDown,
    kCGEventTapDisabledByTimeout,
    kCGEventTapDisabledByUserInput,
    kCGEventTapOptionDefault,
    kCGHeadInsertEventTap,
    kCGKeyboardEventKeycode,
    kCGSessionEventTap,
)

from .log import get_logger

log = get_logger()

# Device-dependent modifier bits present in NSEvent.modifierFlags().
_SIDE_FLAG = {
    "cmd_l": 0x08, "cmd_r": 0x10,
    "ctrl_l": 0x01, "ctrl_r": 0x2000,
    "alt_l": 0x20, "alt_r": 0x40,
    "shift_l": 0x02, "shift_r": 0x04,
}
_BASE_FLAG = {
    "cmd": 0x18, "ctrl": 0x2001, "alt": 0x60, "shift": 0x06,
}
_RIGHT_CMD = 0x10

# For the hold event tap (key combos only): modifier token -> CGEvent flag mask,
# and non-modifier key -> macOS virtual keycode.
_CG_MOD = {
    "cmd": kCGEventFlagMaskCommand, "cmd_l": kCGEventFlagMaskCommand, "cmd_r": kCGEventFlagMaskCommand,
    "ctrl": kCGEventFlagMaskControl, "ctrl_l": kCGEventFlagMaskControl, "ctrl_r": kCGEventFlagMaskControl,
    "alt": kCGEventFlagMaskAlternate, "alt_l": kCGEventFlagMaskAlternate, "alt_r": kCGEventFlagMaskAlternate,
    "shift": kCGEventFlagMaskShift, "shift_l": kCGEventFlagMaskShift, "shift_r": kCGEventFlagMaskShift,
}
_KEYCODE = {"d": 2, "space": 49}


class HotkeyMonitor:
    def __init__(self, on_toggle, on_start, on_stop, is_recording=None):
        self._on_toggle = on_toggle
        self._on_start = on_start
        self._on_stop = on_stop
        self._is_recording = is_recording or (lambda: False)
        self._global = None
        self._local = None
        self._tap = None
        self._source = None
        self._reset()

    def _reset(self):
        self._required_mods = []   # list of bitmasks; combo needs at least one bit of each
        self._required_key = None  # non-modifier token (e.g. "space", "d") or None
        self._double_cmd = False
        self._hold = False         # push-to-talk mode
        self._held = False
        self._hold_mask = 0        # CGEvent modifier mask required for the hold combo
        self._hold_keycode = None  # virtual keycode that starts the hold combo
        self._mods_satisfied = False
        self._rcmd_down = False
        self._last_rcmd = 0.0

    def set_combo(self, spec, mode="toggle"):
        self._remove()
        self._reset()
        # Legacy "hold:cmd+d" specs imply hold mode.
        if spec.startswith("hold:"):
            mode = "hold"
            spec = spec[len("hold:"):]
        self._hold = (mode == "hold")

        if spec == "double_cmd":
            self._double_cmd = True
            self._install()
            log.info(f"hotkey set to double_cmd (mode={mode})")
            return

        tokens = [t.strip().lower() for t in spec.split("+") if t.strip()]
        for token in tokens:
            if token in _SIDE_FLAG:
                self._required_mods.append(_SIDE_FLAG[token])
            elif token in _BASE_FLAG:
                self._required_mods.append(_BASE_FLAG[token])
            else:
                self._required_key = token
        if self._hold and self._required_key is not None:
            for token in tokens:
                self._hold_mask |= _CG_MOD.get(token, 0)
            self._hold_keycode = _KEYCODE.get(self._required_key)
        self._install()
        log.info(f"hotkey set to {spec} (mode={mode})")

    def _install(self):
        # Key-combo hold prefers a consuming tap; modifier-only hold and all
        # toggle combos use the observe-only monitors.
        if self._hold and self._required_key is not None and self._install_tap():
            return
        self._install_monitors()

    def _install_monitors(self):
        mask = NSEventMaskFlagsChanged
        if self._required_key is not None:
            mask |= NSEventMaskKeyDown
        self._global = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            mask, self._on_global)
        self._local = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            mask, self._on_local)

    def _install_tap(self):
        if self._hold_keycode is None or self._hold_mask == 0:
            return False
        mask = CGEventMaskBit(kCGEventKeyDown) | CGEventMaskBit(kCGEventFlagsChanged)
        tap = CGEventTapCreate(
            kCGSessionEventTap, kCGHeadInsertEventTap, kCGEventTapOptionDefault,
            mask, self._tap_callback, None)
        if not tap:
            return False
        source = CFMachPortCreateRunLoopSource(None, tap, 0)
        CFRunLoopAddSource(CFRunLoopGetCurrent(), source, kCFRunLoopCommonModes)
        CGEventTapEnable(tap, True)
        self._tap, self._source = tap, source
        log.info("hold event tap installed (consumes the combo)")
        return True

    def _remove(self):
        for monitor in (self._global, self._local):
            if monitor is not None:
                try:
                    NSEvent.removeMonitor_(monitor)
                except Exception:
                    pass
        self._global = self._local = None
        if self._tap is not None:
            try:
                CGEventTapEnable(self._tap, False)
                if self._source is not None:
                    CFRunLoopRemoveSource(
                        CFRunLoopGetCurrent(), self._source, kCFRunLoopCommonModes)
            except Exception:
                pass
            self._tap = self._source = None

    def _on_global(self, event):
        self._process(event)

    def _on_local(self, event):
        self._process(event)
        return event

    def _process(self, event):
        try:
            flags = int(event.modifierFlags())
            if self._double_cmd:
                self._process_double_cmd(flags)
                return

            mods_ok = all(flags & m for m in self._required_mods) if self._required_mods else True

            if self._required_key is None:
                # Modifier-only combo.
                if mods_ok and not self._mods_satisfied:
                    self._mods_satisfied = True
                    self._on_start() if self._hold else self._on_toggle()
                elif not mods_ok and self._mods_satisfied:
                    self._mods_satisfied = False
                    if self._hold:
                        self._on_stop()
            elif event.type() == NSEventTypeKeyDown:
                # Key combo in toggle mode (hold+key uses the tap path instead).
                chars = (event.charactersIgnoringModifiers() or "").lower()
                wanted = " " if self._required_key == "space" else self._required_key
                if mods_ok and chars == wanted:
                    self._on_toggle()
        except Exception:
            log.exception("hotkey processing failed")

    def _tap_callback(self, proxy, etype, event, refcon):
        """Consuming tap for hold-mode key combos: swallow the key-down so the
        focused app never sees it; start on press, stop on modifier release."""
        try:
            if etype == kCGEventKeyDown:
                keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
                flags = CGEventGetFlags(event)
                if keycode == self._hold_keycode and (flags & self._hold_mask) == self._hold_mask:
                    if not self._held:
                        self._held = True
                        self._on_start()
                    return None  # swallow
            elif etype == kCGEventFlagsChanged:
                if self._held:
                    flags = CGEventGetFlags(event)
                    if (flags & self._hold_mask) != self._hold_mask:
                        self._held = False
                        self._on_stop()
            elif etype in (kCGEventTapDisabledByTimeout, kCGEventTapDisabledByUserInput):
                if self._tap is not None:
                    CGEventTapEnable(self._tap, True)  # macOS may disable us; re-arm
        except Exception:
            log.exception("hold event tap callback failed")
        return event

    def _process_double_cmd(self, flags):
        down = bool(flags & _RIGHT_CMD)
        if down and not self._rcmd_down:  # rising edge = a press
            now = time.time()
            if self._is_recording():
                self._on_toggle()                      # single tap stops
            elif now - self._last_rcmd < 0.5:
                self._on_toggle()                      # double tap starts
            self._last_rcmd = now
        self._rcmd_down = down

    def stop(self):
        self._remove()
