"""Global hotkey via AppKit NSEvent monitors, plus a Quartz event tap for the
push-to-talk ("hold") mode.

pynput's event-tap listener does not fire from inside a py2app bundle, so we use
NSEvent global/local monitors instead. They run on the app's main run loop (which
rumps already drives) and only need Accessibility permission — the reliable way
to observe global hotkeys from a menu-bar app.

Monitors are observe-only: they cannot swallow the event, so a key combo also
reaches the focused app. The hold mode instead installs a CGEventTap, which CAN
consume the matching key-down — so ⌘+D drives dictation without also triggering
the app underneath (e.g. Claude, Finder's "Duplicate"). If the tap can't be
created, it falls back to the plain key-combo monitor (non-consuming,
tap-to-start/tap-to-stop rather than true hold).

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
    kCGEventFlagMaskCommand,
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

# For the hold event tap: modifier token -> CGEvent flag mask, and key -> macOS
# virtual keycode. Only what the sole hold shortcut (⌘+D) needs — add entries
# here when another hold combo is offered.
_CG_MOD = {"cmd": kCGEventFlagMaskCommand}
_KEYCODE = {"d": 2, "space": 49}


class HotkeyMonitor:
    def __init__(self, on_trigger, is_recording=None):
        self._on_trigger = on_trigger
        self._is_recording = is_recording or (lambda: False)
        self._global = None
        self._local = None
        self._tap = None
        self._source = None
        self._reset()

    def _reset(self):
        self._required_mods = []   # list of bitmasks; combo needs at least one bit of each
        self._required_key = None  # non-modifier token (e.g. "space", "k") or None
        self._double_cmd = False
        self._hold = False         # push-to-talk: hold the modifier to keep dictating
        self._held = False
        self._hold_mask = 0        # CGEvent modifier mask required for the hold combo
        self._hold_keycode = None  # virtual keycode that starts the hold combo
        self._mods_satisfied = False
        self._rcmd_down = False
        self._last_rcmd = 0.0

    def set_combo(self, spec):
        self._remove()
        self._reset()
        if spec == "double_cmd":
            self._double_cmd = True
            self._install()
            log.info("hotkey set to double_cmd")
            return
        if spec.startswith("hold:"):   # "hold:cmd+d" = hold ⌘ to keep dictating
            self._hold = True
            spec = spec[len("hold:"):]
        tokens = [t.strip().lower() for t in spec.split("+") if t.strip()]
        for token in tokens:
            if token in _SIDE_FLAG:
                self._required_mods.append(_SIDE_FLAG[token])
            elif token in _BASE_FLAG:
                self._required_mods.append(_BASE_FLAG[token])
            else:
                self._required_key = token
        if self._hold:
            for token in tokens:
                self._hold_mask |= _CG_MOD.get(token, 0)
            self._hold_keycode = _KEYCODE.get(self._required_key)
        self._install()
        log.info(f"hotkey set to {spec}")

    def _install(self):
        # Hold mode prefers a consuming event tap; fall back to the monitor if it
        # can't be created (e.g. Accessibility not yet granted).
        if self._hold and self._install_tap():
            return
        self._install_monitors()

    def _install_monitors(self):
        mask = NSEventMaskFlagsChanged
        if self._required_key is not None:
            mask |= NSEventMaskKeyDown
        # Global monitor: events while OTHER apps are focused (the usual case).
        self._global = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            mask, self._on_global)
        # Local monitor: events while our (menu-bar) app is focused; must return
        # the event so it isn't swallowed.
        self._local = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            mask, self._on_local)

    def _install_tap(self):
        if self._hold_keycode is None:
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
                # Modifier-only combo: fire once on the rising edge.
                if mods_ok and not self._mods_satisfied:
                    self._mods_satisfied = True
                    self._on_trigger()
                elif not mods_ok:
                    self._mods_satisfied = False
            elif event.type() == NSEventTypeKeyDown:
                chars = (event.charactersIgnoringModifiers() or "").lower()
                wanted = " " if self._required_key == "space" else self._required_key
                if mods_ok and chars == wanted:
                    self._on_trigger()
        except Exception:
            log.exception("hotkey processing failed")

    def _tap_callback(self, proxy, etype, event, refcon):
        """CGEventTap handler. Returns None to SWALLOW the matching key-down (so
        the focused app never sees ⌘+D); returns the event otherwise."""
        try:
            if etype == kCGEventKeyDown:
                keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
                flags = CGEventGetFlags(event)
                if keycode == self._hold_keycode and (flags & self._hold_mask) == self._hold_mask:
                    if not self._held:
                        self._held = True
                        self._on_trigger()          # press -> start
                    return None                     # swallow: don't leak to other apps
            elif etype == kCGEventFlagsChanged:
                if self._held:
                    flags = CGEventGetFlags(event)
                    if (flags & self._hold_mask) != self._hold_mask:
                        self._held = False
                        self._on_trigger()          # modifier released -> stop
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
                self._on_trigger()                     # single tap stops
            elif now - self._last_rcmd < 0.5:
                self._on_trigger()                     # double tap starts
            self._last_rcmd = now
        self._rcmd_down = down

    def stop(self):
        self._remove()
