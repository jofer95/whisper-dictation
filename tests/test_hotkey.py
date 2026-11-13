"""State-machine tests for dictation.hotkey (no AppKit / no real keyboard)."""

import unittest

from dictation.hotkey import (BASE_FLAG, FN_FLAG, SIDE_FLAG, DoubleCmdMachine,
                              HotkeyMonitor, Trigger, TriggerMachine)


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class Harness:
    def __init__(self, mode="smart", start_ok=True, cls=TriggerMachine):
        self.clock = Clock()
        self.log = []
        self.timers = []
        self.start_ok = start_ok

        def start():
            self.log.append("start")
            return self.start_ok

        self.m = cls(start, lambda: self.log.append("stop"),
                     lambda: self.log.append("cancel"),
                     lambda d, fn: self.timers.append((self.clock.t + d, fn)),
                     clock=self.clock)
        self.m.mode = mode

    def advance(self, dt):
        self.clock.t += dt
        due = [t for t in self.timers if t[0] <= self.clock.t]
        self.timers = [t for t in self.timers if t[0] > self.clock.t]
        for _t, fn in due:
            fn()

    def tap(self, hold=0.1):
        self.m.press()
        self.advance(hold)
        self.m.release()


class Smart(unittest.TestCase):
    def test_hold_to_talk(self):
        h = Harness()
        h.m.press(); h.advance(2.0); h.m.release()
        self.assertEqual(h.log, ["start", "stop"])
        self.assertFalse(h.m.recording)

    def test_double_tap_goes_hands_free_then_tap_stops(self):
        h = Harness()
        h.tap(); h.advance(0.15); h.tap()
        h.advance(5.0)
        self.assertEqual(h.log, ["start"])
        self.assertTrue(h.m.recording)
        h.tap()
        self.assertEqual(h.log, ["start", "stop"])
        self.assertFalse(h.m.recording)

    def test_single_accidental_tap_is_discarded(self):
        h = Harness()
        h.tap(); h.advance(1.0)
        self.assertEqual(h.log, ["start", "cancel"])
        self.assertFalse(h.m.recording)

    def test_shortcut_chord_cancels(self):
        h = Harness()
        h.m.press(); h.advance(0.2); h.m.other_key()
        h.m.release()
        self.assertEqual(h.log, ["start", "cancel"])

    def test_late_other_key_does_not_cancel(self):
        h = Harness()
        h.m.press(); h.advance(3.0); h.m.other_key(); h.advance(1.0); h.m.release()
        self.assertEqual(h.log, ["start", "stop"])

    def test_escape(self):
        h = Harness()
        h.tap(); h.advance(0.1); h.tap(); h.advance(3)
        self.assertTrue(h.m.escape())
        self.assertEqual(h.log, ["start", "cancel"])
        self.assertFalse(h.m.escape())

    def test_start_refused_stays_idle(self):
        h = Harness(start_ok=False)
        h.m.press(); h.advance(1); h.m.release()
        self.assertEqual(h.log, ["start"])
        self.assertFalse(h.m.recording)

    def test_external_reset(self):
        h = Harness()
        h.tap(); h.advance(0.1); h.tap()
        h.m.reset()
        h.advance(1)
        h.m.press(); h.advance(1); h.m.release()
        self.assertEqual(h.log, ["start", "start", "stop"])


class Toggle(unittest.TestCase):
    def test_tap_on_tap_off(self):
        h = Harness("toggle")
        h.tap(); h.advance(4); h.tap()
        self.assertEqual(h.log, ["start", "stop"])

    def test_quick_retap_cancels(self):
        h = Harness("toggle")
        h.tap(); h.advance(0.1); h.tap()
        self.assertEqual(h.log, ["start", "cancel"])


class Hold(unittest.TestCase):
    def test_short_press_still_stops(self):
        h = Harness("hold")
        h.tap(0.1); h.advance(1)
        self.assertEqual(h.log, ["start", "stop"])


class DoubleCmd(unittest.TestCase):
    def test_double_tap_starts_single_stops(self):
        h = Harness(cls=DoubleCmdMachine)
        h.m.press(); h.advance(0.2); h.m.press()
        self.assertEqual(h.log, ["start"])
        h.advance(3); h.m.press()
        self.assertEqual(h.log, ["start", "stop"])

    def test_cmd_c_is_not_a_double_tap(self):
        h = Harness(cls=DoubleCmdMachine)
        h.m.press(); h.m.other_key(); h.advance(0.2); h.m.press()
        self.assertEqual(h.log, [])


class TriggerParsing(unittest.TestCase):
    def test_extra_modifier_is_not_the_chord(self):
        t = Trigger("cmd+alt")
        self.assertTrue(t.mods_ok(SIDE_FLAG["cmd_l"] | SIDE_FLAG["alt_r"]))
        self.assertFalse(t.mods_ok(SIDE_FLAG["cmd_l"] | SIDE_FLAG["alt_l"] | SIDE_FLAG["shift_l"]))

    def test_sided(self):
        t = Trigger("ctrl+shift_l")
        self.assertTrue(t.mods_ok(SIDE_FLAG["ctrl_r"] | SIDE_FLAG["shift_l"]))
        self.assertFalse(t.mods_ok(SIDE_FLAG["ctrl_r"] | SIDE_FLAG["shift_r"]))

    def test_kinds(self):
        self.assertEqual(Trigger("fn").kind, "fn")
        self.assertEqual(Trigger("alt+space").kind, "key")
        self.assertEqual(Trigger("double_cmd").kind, "double_cmd")
        with self.assertRaises(ValueError):
            Trigger("cmd+banana")


class MonitorRouting(unittest.TestCase):
    """Drive HotkeyMonitor.handle() with synthetic events (no tap installed)."""

    def make(self, spec, mode="smart"):
        h = Harness(mode)
        mon = HotkeyMonitor.__new__(HotkeyMonitor)
        mon._on_paste_last = lambda: h.log.append("paste_last")
        mon._is_recording = lambda: False
        mon.trigger = Trigger(spec)
        mon.machine = h.m
        mon._down = False
        return h, mon

    def test_key_combo_is_swallowed_and_repeats_ignored(self):
        h, mon = self.make("alt+space", "toggle")
        alt = SIDE_FLAG["alt_l"]
        self.assertTrue(mon.handle("down", 49, alt))
        self.assertTrue(mon.handle("down", 49, alt, autorepeat=True))
        self.assertTrue(mon.handle("up", 49, alt))
        self.assertEqual(h.log, ["start"])
        self.assertFalse(mon.handle("down", 0, 0))       # plain 'a' passes through

    def test_esc_cancels_only_while_recording(self):
        h, mon = self.make("alt+space", "toggle")
        self.assertFalse(mon.handle("down", 53, 0))
        mon.handle("down", 49, SIDE_FLAG["alt_l"]); mon.handle("up", 49, SIDE_FLAG["alt_l"])
        self.assertTrue(mon.handle("down", 53, 0))
        self.assertEqual(h.log, ["start", "cancel"])

    def test_paste_last(self):
        h, mon = self.make("cmd+alt")
        flags = SIDE_FLAG["ctrl_l"] | SIDE_FLAG["cmd_l"]
        self.assertTrue(mon.handle("down", 9, flags))
        self.assertFalse(mon.handle("down", 9, SIDE_FLAG["cmd_l"]))   # plain ⌘V untouched
        self.assertEqual(h.log, ["paste_last"])

    def test_modifier_chord_hold(self):
        h, mon = self.make("ctrl+shift_l")
        mon.handle("flags", 59, SIDE_FLAG["ctrl_l"])
        mon.handle("flags", 56, SIDE_FLAG["ctrl_l"] | SIDE_FLAG["shift_l"])
        h.advance(1.5)
        mon.handle("flags", 56, SIDE_FLAG["ctrl_l"])
        self.assertEqual(h.log, ["start", "stop"])

    def test_modifier_chord_used_as_shortcut_cancels(self):
        h, mon = self.make("cmd+alt")
        mon.handle("flags", 55, SIDE_FLAG["cmd_l"] | SIDE_FLAG["alt_l"])
        mon.handle("down", 53, SIDE_FLAG["cmd_l"] | SIDE_FLAG["alt_l"])   # ⌘⌥Esc
        self.assertEqual(h.log, ["start", "cancel"])

    def test_fn_hold_and_fn_delete(self):
        h, mon = self.make("fn")
        mon.handle("flags", 63, FN_FLAG); h.advance(1); mon.handle("flags", 63, 0)
        self.assertEqual(h.log, ["start", "stop"])
        mon.handle("flags", 63, FN_FLAG); mon.handle("down", 51, FN_FLAG)  # Fn+Delete
        mon.handle("flags", 63, 0)
        self.assertEqual(h.log, ["start", "stop", "start", "cancel"])

    def test_ctrl_cmd_v_needs_both(self):
        _h, mon = self.make("fn")
        self.assertFalse(mon.handle("down", 9, BASE_FLAG["ctrl"] & 0x01))


if __name__ == "__main__":
    unittest.main()
