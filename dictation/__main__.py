"""Entry point: `python -m dictation`.

Wires the recorder, transcriber and text injector into the menu-bar app and
starts the global hotkey listener.
"""

import argparse
import threading
import time

from . import __version__
from . import prefs
from .log import LOG_PATH, get_logger
from .transcriber import DEFAULT_MODEL, KNOWN_SIZES, Transcriber
from .recorder import Recorder, device_name, normalize_device_spec
from .injector import TextInjector

log = get_logger()

DEFAULTS = {
    "device": None,
    "hotkey": "ctrl+alt",      # Wispr's default on Macs without a usable Fn key
    "mode": "smart",
    "language": "auto:es,en",
    "model": DEFAULT_MODEL,
    "mute": False,             # Wispr default; music is ducked out of the mic by VAD anyway
    "sounds": True,
    "waveform": True,
    "overlay_style": "glass",
    "fillers": True,
    "commands": True,
    "context": True,
    "casual": True,
    "ai_cleanup": False,       # on-device Apple Intelligence pass (macOS 26+), opt-in
    "history": True,
    "max_time": 20 * 60,
}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="dictation",
        description="Private, offline dictation for macOS (faster-whisper). Hold the "
        "shortcut to talk, double-tap it for hands-free, Esc cancels, ⌃⌘V pastes the "
        "last transcript. Runs 100% locally — no cloud, no telemetry.",
    )
    parser.add_argument(
        "-m", "--model", default=None,
        help=f"Model size ({', '.join(KNOWN_SIZES)}) or a raw Hugging Face CT2 "
        f"repo id / local path. Default: last used, else {DEFAULT_MODEL}.",
    )
    parser.add_argument(
        "-k", "--key-combination", default=None,
        help="Shortcut joined by '+', e.g. fn, ctrl+alt, cmd+alt, ctrl+shift_l, "
        "alt+space. Bare modifiers match either side; sided names (shift_l) match one.",
    )
    parser.add_argument("--double-cmd", action="store_true",
                        help="Double-tap Right Command to start / single tap to stop.")
    parser.add_argument(
        "--mode", choices=["smart", "toggle", "hold"], default=None,
        help="smart (hold to talk, double-tap for hands-free — default), "
        "toggle (tap on / tap off) or hold (push-to-talk only).",
    )
    parser.add_argument("--hold", action="store_true", help="Shorthand for --mode hold.")
    parser.add_argument(
        "-l", "--language", default=None,
        help="'es', 'en', … to force one; 'auto' for any; 'auto:es,en' to auto-detect "
        "among a few (default).",
    )
    parser.add_argument("-t", "--max-time", type=float, default=None,
                        help="Auto-stop (and transcribe) after this many seconds. Default: 1200.")
    parser.add_argument(
        "--inject", choices=["paste", "type"], default="paste",
        help="'paste' (default) uses the clipboard + ⌘V (best for accents); "
        "'type' synthesizes keystrokes.",
    )
    parser.add_argument("--device", default=None,
                        help="Input device: a name, name substring or index.")
    parser.add_argument("--mute", action="store_true",
                        help="Mute other audio while recording (also in the menu).")
    parser.add_argument("--no-mute", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-sounds", action="store_true",
                        help="Do NOT play the start/stop audio cues.")
    parser.add_argument("--no-waveform", action="store_true",
                        help="Do NOT show the floating recording pill.")
    parser.add_argument("--overlay-style", default=None,
                        choices=["cream", "glass", "hud", "aurora"],
                        help="Recording-pill look. Default: last used, else glass.")
    parser.add_argument("--ai-cleanup", action="store_true",
                        help="Turn on the on-device AI cleanup step (Apple Intelligence, "
                        "macOS 26+) for this run. Also in the menu under Smart formatting.")
    parser.add_argument(
        "--self-test", action="store_true",
        help="Load the model, run one inference through the engine, then exit 0. "
        "Used to verify a packaged build boots without a mic/hotkey.",
    )
    parser.add_argument(
        "--simulate", metavar="WAV", default=None,
        help="Developer check: once the model is loaded, wait --simulate-delay seconds, "
        "then run WAV (16 kHz mono) through the full pipeline and paste the result into "
        "the focused app, as if you had dictated it. Quits afterwards.",
    )
    parser.add_argument("--simulate-delay", type=float, default=3.0, help=argparse.SUPPRESS)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(argv)


def build_settings(args, saved):
    s = dict(DEFAULTS)
    s.update({k: v for k, v in saved.items() if k in DEFAULTS})

    # ---- migrations from ≤1.12 prefs ----
    if saved.get("language", "missing") is None:
        s["language"] = "auto:es,en"   # old "Auto (mixed languages)": now restricted ES/EN
    hk = s["hotkey"] or DEFAULTS["hotkey"]
    if hk.startswith("hold:"):
        hk, s["mode"] = hk[len("hold:"):], "hold"
    s["hotkey"] = hk
    if s["mode"] == "hybrid":
        s["mode"] = "smart"
    if "mode" not in saved and "hotkey" in saved:
        s["mode"] = "toggle"           # 1.x default; keep muscle memory for upgraders

    # ---- explicit CLI flags win ----
    if args.double_cmd:
        s["hotkey"] = "double_cmd"
    elif args.key_combination:
        s["hotkey"] = args.key_combination
    if args.hold:
        s["mode"] = "hold"
    elif args.mode:
        s["mode"] = args.mode
    if args.language is not None:
        s["language"] = None if args.language == "auto" else args.language
    if args.model:
        s["model"] = args.model
    if args.max_time is not None:
        s["max_time"] = args.max_time
    if args.device is not None:
        s["device"] = args.device
    if args.mute:
        s["mute"] = True
    if args.no_mute:
        s["mute"] = False
    if args.no_sounds:
        s["sounds"] = False
    if args.no_waveform:
        s["waveform"] = False
    if args.overlay_style:
        s["overlay_style"] = args.overlay_style
    if args.ai_cleanup:
        s["ai_cleanup"] = True
    s["device"] = normalize_device_spec(s["device"])
    return s


def load_transcriber(name):
    t = Transcriber(name)
    t.warm_up()
    return t


def main(argv=None):
    args = parse_args(argv)
    log.info(f"starting whisper-dictation v{__version__}; log at {LOG_PATH}")

    settings = build_settings(args, prefs.load())
    log.info(f"settings: {settings}")
    log.info(f"input device: '{device_name(settings['device'])}'")

    if args.self_test:
        import numpy as np
        print(f"[self-test] loading model '{settings['model']}'…")
        t = load_transcriber(settings["model"])
        print("[self-test] running one inference through the bundled engine...")
        t.transcribe(np.zeros(16000, dtype=np.float32), language="en")
        from . import perms
        print("[self-test] permissions:", perms.summary())
        from . import aicleanup
        ai = aicleanup.AICleanup()
        print(f"[self-test] AI cleanup helper: {ai.path or 'missing'}; "
              f"available: {ai.availability()}")
        print("SELF-TEST OK")
        return

    from .app import StatusBarApp

    recorder = Recorder(device=settings["device"])
    injector = TextInjector(mode=args.inject)
    app = StatusBarApp(recorder, injector, settings, load_transcriber)
    app.load_model_async(settings["model"])

    if args.simulate:
        threading.Thread(target=_simulate, args=(app, args.simulate, args.simulate_delay),
                         daemon=True).start()

    # Validate permissions up front: prompt (but don't force-open Settings every
    # launch — the "Grant / re-check permissions…" menu item does that on demand).
    app.check_permissions(prompt=True, open_settings=False)

    log.info("entering run loop")
    print("Running. Look for the 🎙️ icon in your menu bar.")
    app.run()


def _simulate(app, path, delay):
    """Feed a WAV file through the real pipeline (developer verification)."""
    import wave

    import numpy as np
    from PyObjCTools import AppHelper
    import rumps

    while not app._model_ready:
        time.sleep(0.2)
    time.sleep(delay)
    with wave.open(path) as w:
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        audio = audio.astype(np.float32) / 32768.0
    _ax_caret_to_end()
    log.info(f"[simulate] feeding {path} ({audio.size / 16000:.1f}s)")
    t0 = time.time()
    app._transcribe_and_insert(audio, audio.size / 16000)
    log.info(f"[simulate] done in {time.time() - t0:.2f}s; status={app._status!r}")
    time.sleep(1.5)  # let the paste land and the clipboard restore run
    log.info(f"[simulate] focused field now contains: {_ax_focused_value()!r}")
    AppHelper.callAfter(rumps.quit_application)


def _ax_focused(attr):
    from ApplicationServices import AXUIElementCopyAttributeValue, AXUIElementCreateSystemWide
    _e, el = AXUIElementCopyAttributeValue(AXUIElementCreateSystemWide(),
                                           "AXFocusedUIElement", None)
    if el is None:
        return None, None
    _e, value = AXUIElementCopyAttributeValue(el, attr, None)
    return el, value


def _ax_focused_value():
    try:
        _el, value = _ax_focused("AXValue")
        return None if value is None else str(value)[-400:]
    except Exception as exc:
        return f"<unreadable: {exc}>"


def _ax_caret_to_end():
    """Put the caret at the end of the focused field (so --simulate appends)."""
    try:
        from ApplicationServices import (AXUIElementSetAttributeValue, AXValueCreate,
                                         kAXValueCFRangeType)
        el, value = _ax_focused("AXValue")
        if el is not None and value is not None:
            end = len(str(value))
            AXUIElementSetAttributeValue(el, "AXSelectedTextRange",
                                         AXValueCreate(kAXValueCFRangeType, (end, 0)))
    except Exception:
        log.exception("[simulate] could not move caret")


if __name__ == "__main__":
    main()
