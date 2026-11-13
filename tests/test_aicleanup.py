"""AI cleanup client: every failure must fall back to the rule-based text.

Run with:  .venv/bin/python -m unittest discover -s tests -v

The Swift helper is faked with an injectable runner, so these tests run on any
Mac (and in CI) without macOS 26 or Apple Intelligence. One optional test at
the bottom exercises the real helper when it's built and the model is usable.
"""

import os
import subprocess
import time
import unittest

from dictation import aicleanup as ai

SUPPORTED = (True, "ok")
TEXT = "So let's meet at 2, actually 3 on Thursday."


def proc(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


class FakeRunner:
    """Records calls; returns/raises whatever the test configures."""

    def __init__(self, result=None, exc=None, delay=0.0):
        self.result, self.exc, self.delay = result, exc, delay
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        if self.delay:
            time.sleep(self.delay)
        if self.exc:
            raise self.exc
        return self.result


def client(runner, supported=SUPPORTED, path="/fake/aicleanup"):
    return ai.AICleanup(path=path, runner=runner, supported=supported)


class Fallback(unittest.TestCase):
    def assertFallback(self, res, reason_part):
        self.assertFalse(res.used)
        self.assertEqual(res.text, TEXT)
        self.assertIn(reason_part, res.reason)

    def test_success_uses_model_text(self):
        runner = FakeRunner(proc("So let's meet at 3 on Thursday."))
        res = client(runner).clean(TEXT, language="en")
        self.assertTrue(res.used)
        self.assertEqual(res.text, "So let's meet at 3 on Thursday.")
        cmd, kw = runner.calls[0]
        self.assertEqual(cmd, ["/fake/aicleanup", "--style", "default", "--lang", "en"])
        self.assertEqual(kw["input"], TEXT)
        self.assertLessEqual(kw["timeout"], ai.TIMEOUT_MAX)

    def test_timeout(self):
        exc = subprocess.TimeoutExpired(cmd="aicleanup", timeout=1.5)
        self.assertFallback(client(FakeRunner(exc=exc)).clean(TEXT), "timed out")

    def test_helper_crashes_to_launch(self):
        self.assertFallback(client(FakeRunner(exc=OSError("Bad CPU type"))).clean(TEXT),
                            "failed to run")

    def test_model_unavailable_exit_code(self):
        runner = FakeRunner(proc(returncode=2, stderr="Apple Intelligence is turned off\n"))
        self.assertFallback(client(runner).clean(TEXT), "Apple Intelligence is turned off")

    def test_generation_error_exit_code(self):
        runner = FakeRunner(proc(returncode=3, stderr="generation failed: guardrail"))
        self.assertFallback(client(runner).clean(TEXT), "helper exit 3")

    def test_killed_by_signal(self):
        self.assertFallback(client(FakeRunner(proc(returncode=-9))).clean(TEXT), "exit -9")

    def test_empty_output(self):
        self.assertFallback(client(FakeRunner(proc("   \n"))).clean(TEXT), "empty output")

    def test_missing_helper_never_runs(self):
        runner = FakeRunner(proc("x"))
        c = ai.AICleanup(path=None, runner=runner, supported=SUPPORTED)
        c._path = None
        orig = ai.helper_path
        ai.helper_path = lambda: None
        try:
            self.assertFallback(c.clean(TEXT), "helper not installed")
        finally:
            ai.helper_path = orig
        self.assertEqual(runner.calls, [])

    def test_old_macos_never_runs(self):
        runner = FakeRunner(proc("x"))
        res = client(runner, supported=(False, "needs macOS 26 or later")).clean(TEXT)
        self.assertFallback(res, "macOS 26")
        self.assertEqual(runner.calls, [])

    def test_empty_input_is_noop(self):
        runner = FakeRunner(proc("x"))
        res = client(runner).clean("  ")
        self.assertFalse(res.used)
        self.assertEqual(runner.calls, [])

    def test_unknown_language_code_not_passed(self):
        runner = FakeRunner(proc(TEXT))
        client(runner).clean(TEXT, language="auto:es,en")
        self.assertNotIn("--lang", runner.calls[0][0])

    def test_casual_style_forwarded(self):
        runner = FakeRunner(proc(TEXT))
        client(runner).clean(TEXT, style="casual")
        self.assertEqual(runner.calls[0][0][1:3], ["--style", "casual"])


class Guard(unittest.TestCase):
    """The model's answer must look like a cleaned-up copy of the input."""

    def check(self, original, candidate):
        res = client(FakeRunner(proc(candidate))).clean(original)
        return res

    def test_rejects_translation(self):
        es = ("Oye podemos hacer el deploy mañana porque el build de staging "
              "is still failing")
        res = self.check(es, "Hey, can we do the deploy tomorrow because the "
                             "staging build is still failing")
        self.assertFalse(res.used)
        self.assertEqual(res.text, es)
        self.assertIn("translated", res.reason)

    def test_rejects_answering_a_question(self):
        res = self.check("What is the capital of France?",
                         "The capital of France is Paris, a city known for the Eiffel Tower.")
        self.assertFalse(res.used)

    def test_rejects_refusal(self):
        res = self.check("Tell him the deploy is blocked until Monday.",
                         "I'm sorry, but I can't help with that request.")
        self.assertFalse(res.used)

    def test_rejects_summary(self):
        long = ("I looked at the crash on the job details screen this morning and it looks "
                "like the view model is null when we come back from the background, so we "
                "should add a saved state handle and a test for it.")
        res = self.check(long, "Crash fix needed.")
        self.assertFalse(res.used)
        self.assertIn("too short", res.reason)

    def test_rejects_rambling(self):
        res = self.check("Sounds good.", "Sounds good. " * 10)
        self.assertFalse(res.used)
        self.assertIn("too long", res.reason)

    def test_accepts_mixed_language_cleanup(self):
        res = self.check("oye podemos hacer el deploy mañana porque el build de staging "
                         "is still failing",
                         "Oye, ¿podemos hacer el deploy mañana? Porque el build de "
                         "staging is still failing.")
        self.assertTrue(res.used, res.reason)

    def test_accepts_list_formatting(self):
        res = self.check("We need three things first update the docs second fix the tests "
                         "and third ship it",
                         "We need three things:\n1. Update the docs.\n2. Fix the tests.\n"
                         "3. Ship it.")
        self.assertTrue(res.used, res.reason)

    def test_restores_leading_capital(self):
        res = self.check("So let's meet at 3 on Thursday.", "so let's meet at 3 on Thursday.")
        self.assertTrue(res.used)
        self.assertEqual(res.text, "So let's meet at 3 on Thursday.")


class Timeouts(unittest.TestCase):
    def test_short_text_gets_base_timeout(self):
        self.assertEqual(ai.timeout_for("hello there"), ai.TIMEOUT_BASE)

    def test_long_text_is_capped(self):
        self.assertEqual(ai.timeout_for("x" * 5000), ai.TIMEOUT_MAX)
        self.assertGreater(ai.timeout_for("x" * 400), ai.TIMEOUT_BASE)


class Warm(unittest.TestCase):
    def test_warm_is_throttled_and_never_raises(self):
        runner = FakeRunner(exc=OSError("boom"))
        c = client(runner)
        c.warm()
        c.warm()                      # within WARM_EVERY: no second spawn
        time.sleep(0.1)
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(runner.calls[0][0][1], "--warm")

    def test_availability_reports_reason(self):
        c = client(FakeRunner(proc(returncode=2, stderr="model not ready\n")))
        self.assertEqual(c.availability(), (False, "model not ready"))
        c = client(FakeRunner(exc=subprocess.TimeoutExpired("x", 3)))
        self.assertEqual(c.availability()[0], False)


class AppWiring(unittest.TestCase):
    """The menu toggle and the app's AI step, without starting the menu-bar app."""

    def setUp(self):
        from types import SimpleNamespace
        from unittest import mock
        from dictation import app as appmod
        from dictation.context import Context
        self.appmod, self.Context, self.mock = appmod, Context, mock
        self.saved = []
        self.app = SimpleNamespace(s={"ai_cleanup": False, "casual": True}, _status="",
                                   _save=lambda: self.saved.append(True))
        self.item = SimpleNamespace(_key="ai_cleanup", state=0)

    def toggle(self, available):
        app, mock = self.app, self.mock
        app._ai = mock.Mock()
        app._ai.availability.return_value = available
        app._check_ai = lambda item: self.appmod.StatusBarApp._check_ai(app, item)
        # Run the background check inline and the main-thread callback immediately.
        with mock.patch("threading.Thread") as thread, \
                mock.patch("PyObjCTools.AppHelper.callAfter", side_effect=lambda f: f()), \
                mock.patch.object(self.appmod.rumps, "alert") as alert:
            thread.side_effect = lambda target, args, **kw: mock.Mock(
                start=lambda: target(*args))
            self.appmod.StatusBarApp._on_toggle_setting(app, self.item)
        return alert

    def test_toggle_on_when_available(self):
        alert = self.toggle((True, "ok"))
        self.assertTrue(self.app.s["ai_cleanup"])
        self.assertEqual(self.item.state, 1)
        self.assertTrue(self.saved)
        alert.assert_not_called()
        self.app._ai.warm.assert_called_once()

    def test_toggle_reverts_when_unavailable(self):
        alert = self.toggle((False, "Apple Intelligence is turned off"))
        self.assertFalse(self.app.s["ai_cleanup"])
        self.assertEqual(self.item.state, 0)
        self.assertIn("unavailable", self.app._status)
        alert.assert_called_once()

    def test_ai_step_falls_back_on_crash(self):
        app = self.app
        app._ai = self.mock.Mock()
        app._ai.clean.side_effect = RuntimeError("boom")
        out = self.appmod.StatusBarApp._ai_clean(app, "Hola.", "es", self.Context())
        self.assertEqual(out, "Hola.")

    def test_ai_step_uses_casual_style_in_chat_apps(self):
        app = self.app
        app._ai = self.mock.Mock()
        app._ai.clean.return_value = ai.Result("Hola", True, "ok", 0.4)
        ctx = self.Context(bundle_id="com.tinyspeck.slackmacgap")
        out = self.appmod.StatusBarApp._ai_clean(app, "Hola.", "es", ctx)
        self.assertEqual(out, "Hola")
        self.assertEqual(app._ai.clean.call_args.kwargs["style"], "casual")

    def test_off_by_default(self):
        from dictation.__main__ import DEFAULTS
        self.assertIs(DEFAULTS["ai_cleanup"], False)
        self.assertIs(self.appmod.TOGGLES["ai_cleanup"][1], False)


@unittest.skipUnless(os.environ.get("AICLEANUP_LIVE") == "1",
                     "set AICLEANUP_LIVE=1 to call the real on-device model")
class Live(unittest.TestCase):
    def test_real_helper_end_to_end(self):
        c = ai.AICleanup()
        ok, reason = c.availability()
        if not ok:
            self.skipTest(reason)
        c.clean("warm up", language="en", timeout=10)
        res = c.clean("so um let's meet at 2 actually 3 on Thursday", language="en")
        print(f"\n[live] {res}")
        self.assertTrue(res.used, res.reason)
        self.assertIn("3", res.text)
        self.assertNotIn("2", res.text)


if __name__ == "__main__":
    unittest.main()
