"""Entry point: `python -m dictation`.

Wires the recorder, transcriber and text injector into the menu-bar app and
starts the global hotkey listener.
"""

import argparse
import platform

from . import __version__
from . import prefs
from .log import LOG_PATH, get_logger
from .transcriber import DEFAULT_MODEL, KNOWN_SIZES, Transcriber
from .recorder import Recorder, resolve_device, device_name
from .injector import TextInjector
from .app import StatusBarApp

log = get_logger()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="dictation",
        description="Private, offline dictation for macOS (faster-whisper). "
        "Toggle with cmd+alt by default; transcribed text is inserted into the "
        "focused field. Runs 100% locally — no cloud, no telemetry.",
    )
    parser.add_argument(
        "-m", "--model", default=DEFAULT_MODEL,
        help=f"Model size ({', '.join(KNOWN_SIZES)}) or a raw Hugging Face CT2 "
        f"repo id / local path. Default: {DEFAULT_MODEL}.",
    )
    parser.add_argument(
        "-k", "--key-combination", default=None,
        help="Toggle combo joined by '+', e.g. cmd+alt or ctrl+shift_l. Bare "
        "modifiers match either side; sided names (shift_l) match one. "
        "Default: last used, else cmd+alt on macOS.",
    )
    parser.add_argument(
        "--double-cmd", action="store_true",
        help="Use double-tap Right Command to start / single tap to stop "
        "(overrides --key-combination).",
    )
    parser.add_argument(
        "--hold", action="store_true",
        help="Push-to-talk: hold the shortcut to dictate, release to stop "
        "(default is toggle: tap on / tap off). Also selectable from the menu.",
    )
    parser.add_argument(
        "-l", "--language", default=None,
        help="Force a language (e.g. 'es' or 'en'). Comma-separate to offer a "
        "switcher menu (first is default). Omit to auto-detect per utterance.",
    )
    parser.add_argument(
        "-t", "--max-time", type=float, default=60,
        help="Auto-stop after this many seconds. Default: 60.",
    )
    parser.add_argument(
        "--inject", choices=["paste", "type"], default="paste",
        help="How to insert text. 'paste' (default) uses the clipboard + Cmd+V "
        "(best for accents); 'type' synthesizes keystrokes.",
    )
    parser.add_argument(
        "--device", default=None,
        help="Input device: index or a name substring (e.g. 'MacBook'). "
        "Default: smart (built-in mic, ignoring virtual devices). "
        "Also selectable live from the menu.",
    )
    parser.add_argument(
        "--no-mute", action="store_true",
        help="Do NOT mute system audio while recording. By default output is "
        "muted during capture so music doesn't leak into the mic.",
    )
    parser.add_argument(
        "--no-sounds", action="store_true",
        help="Do NOT play the start/stop audio cues (on by default).",
    )
    parser.add_argument(
        "--no-waveform", action="store_true",
        help="Do NOT show the floating waveform overlay while recording "
        "(on by default).",
    )
    parser.add_argument(
        "--overlay-style", default=None,
        choices=["cream", "glass", "hud", "aurora"],
        help="Recording-pill look: cream, glass (Liquid Glass), hud, or aurora. "
        "Default: last used, else glass. Also selectable from the menu.",
    )
    parser.add_argument(
        "--self-test", action="store_true",
        help="Load the model, run one inference through the engine, then exit "
        "0. Used to verify a packaged build boots without a mic/hotkey.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    args = parser.parse_args(argv)
    if args.language is not None:
        langs = [x.strip() for x in args.language.split(",") if x.strip()]
        args.languages = langs
        args.language = langs[0] if langs else None
    else:
        args.languages = None
    return args


def main(argv=None):
    args = parse_args(argv)

    log.info(f"starting whisper-dictation-hcp v{__version__}; log at {LOG_PATH}")
    print(f"Loading model '{args.model}' (first run downloads it, then it's offline)...")
    transcriber = Transcriber(args.model)
    transcriber.warm_up()
    print("Model ready.")

    # Merge saved preferences with CLI args (explicit CLI args win).
    saved = prefs.load()
    default_hotkey = "cmd+alt" if platform.system() == "Darwin" else "ctrl+alt"
    if args.double_cmd:
        hotkey = "double_cmd"
    else:
        hotkey = args.key_combination or saved.get("hotkey") or default_hotkey
    mode = "hold" if args.hold else saved.get("mode", "toggle")
    # Migrate the legacy "hold:cmd+d" spec to (spec, mode) so menus stay in sync.
    if hotkey.startswith("hold:"):
        hotkey = hotkey[len("hold:"):]
        mode = "hold"
    device_spec = args.device if args.device is not None else saved.get("device")
    language = args.language if args.language is not None else saved.get("language")
    mute = False if args.no_mute else bool(saved.get("mute", True))
    sounds = False if args.no_sounds else bool(saved.get("sounds", True))
    waveform = False if args.no_waveform else bool(saved.get("waveform", True))
    overlay_style = args.overlay_style or saved.get("overlay_style") or "glass"
    log.info(f"prefs applied: hotkey={hotkey} mode={mode} device={device_spec!r} "
             f"language={language!r} mute={mute} sounds={sounds} waveform={waveform} "
             f"overlay={overlay_style}")

    device = resolve_device(device_spec)
    log.info(f"input device: '{device_name(device)}' (index={device})")
    recorder = Recorder(device=device)
    injector = TextInjector(mode=args.inject)

    app = StatusBarApp(
        recorder, transcriber, injector,
        languages=args.languages, language=language,
        max_time=args.max_time, hotkey=hotkey, hotkey_mode=mode,
        mute_while_recording=mute, play_sounds=sounds, show_waveform=waveform,
        overlay_style=overlay_style,
    )

    if args.self_test:
        import numpy as np
        print("[self-test] running one inference through the bundled engine...")
        transcriber.transcribe(np.zeros(16000, dtype=np.float32), language="en")
        print("[self-test] permissions:", app.check_permissions(prompt=False))
        print("SELF-TEST OK")
        return

    # Validate permissions up front: prompt (but don't force-open Settings every
    # launch — the "Grant / re-check permissions…" menu item does that on demand).
    log.info("checking permissions...")
    app.check_permissions(prompt=True, open_settings=False)

    log.info("entering run loop")
    print("Running. Look for the 🎙️ icon in your menu bar.")
    app.run()


if __name__ == "__main__":
    main()
