"""Microphone capture via sounddevice.

sounddevice ships a bundled PortAudio in its macOS wheel, so end users do NOT
need `brew install portaudio`. Audio is captured as mono float32 at 16 kHz —
exactly the format Whisper expects, so no resampling is needed.

Devices are tracked by NAME, not index: PortAudio snapshots the device list
when it initialises, so plugging in AirPods / a USB mic (or the system default
changing) after launch was invisible and indices silently shifted. We now
re-scan (≈4 ms) right before every recording and whenever the menu refreshes.
"""

import threading

import numpy as np
import sounddevice as sd

from .log import get_logger

log = get_logger()

SAMPLE_RATE = 16000
CHANNELS = 1

_pa_lock = threading.Lock()   # PortAudio re-init must not race an open stream


def rescan():
    """Re-enumerate audio devices (cheap). Only call while no stream is open."""
    with _pa_lock:
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            log.exception("could not rescan audio devices")


def list_input_devices():
    """Return [(index, name), ...] for every input-capable device."""
    try:
        return [
            (i, d["name"])
            for i, d in enumerate(sd.query_devices())
            if d["max_input_channels"] > 0
        ]
    except Exception:
        log.exception("could not list input devices")
        return []


def system_default_index():
    try:
        idx = sd.default.device[0]
        return None if idx is None or idx < 0 else idx
    except Exception:
        return None


def default_input_name():
    di = system_default_index()
    if di is None:
        return None
    try:
        return sd.query_devices(di)["name"]
    except Exception:
        return None


def device_name(name):
    """Human label for a saved device (None = system default)."""
    if name is None:
        cur = default_input_name()
        return f"system default ({cur})" if cur else "system default"
    return str(name)


def resolve_device(spec):
    """Saved name / index / name substring -> current PortAudio index, or None
    (= system default) if it isn't connected right now."""
    if spec is None:
        return None
    if isinstance(spec, int) or (isinstance(spec, str) and spec.strip().isdigit()):
        idx = int(spec)
        return idx if any(i == idx for i, _ in list_input_devices()) else None
    spec = str(spec).strip().lower()
    devices = list_input_devices()
    for i, name in devices:           # exact name first…
        if name.lower() == spec:
            return i
    for i, name in devices:           # …then substring (CLI convenience)
        if spec in name.lower():
            return i
    return None


def normalize_device_spec(spec):
    """CLI/prefs value -> the device NAME we persist (None = system default)."""
    if spec is None:
        return None
    idx = resolve_device(spec)
    if idx is None:
        # Not connected now: keep the saved name so it's used when it returns.
        return None if str(spec).strip().isdigit() else str(spec)
    for i, name in list_input_devices():
        if i == idx:
            return name
    return None


class Recorder:
    def __init__(self, device=None):
        self.device = device  # device NAME, or None => system default
        self._frames = []
        self._stream = None
        self._level = 0.0     # latest block peak; read by the waveform overlay
        self.max_level = 0.0  # loudest block this session (silent-mic warning)
        self.active_device = None

    def level(self):
        """Current mic level (0..1 peak of the most recent block). 0 when idle."""
        return self._level

    @property
    def is_open(self):
        return self._stream is not None

    def _open(self, index):
        def _callback(indata, _frames, _time, status):
            if status:
                log.warning(f"audio callback status: {status}")
            self._frames.append(indata.copy())
            # Single float writes, read on the main thread — no lock needed in CPython.
            peak = float(np.abs(indata).max()) if indata.size else 0.0
            self._level = peak
            if peak > self.max_level:
                self.max_level = peak

        stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            device=index,
            callback=_callback,
        )
        stream.start()
        return stream

    def start(self):
        self._frames = []
        self.max_level = 0.0
        if self._stream is None:
            rescan()   # pick up mics plugged in / default changed since last time
        index = resolve_device(self.device)
        if self.device is not None and index is None:
            log.warning(f"saved mic {self.device!r} not connected; using system default")
        try:
            self._stream = self._open(index)
        except Exception as exc:
            if index is None:
                raise
            log.warning(f"device {self.device!r} failed to open ({exc}); "
                        f"retrying system default")
            index = None
            self._stream = self._open(None)
        self.active_device = (device_name(None) if index is None
                              else sd.query_devices(index)["name"])
        log.info(f"recording started on '{self.active_device}'")

    def stop(self):
        """Stop recording and return the captured audio as a 1-D float32 array."""
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None
        self._level = 0.0

        frames, self._frames = self._frames, []
        if not frames:
            log.warning("recording stopped with no frames captured")
            return np.zeros(0, dtype=np.float32)

        audio = np.concatenate(frames, axis=0).flatten()
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        log.info(f"recording stopped: {audio.size / SAMPLE_RATE:.1f}s, peak={peak:.4f}")
        return audio
