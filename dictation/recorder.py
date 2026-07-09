"""Microphone capture via sounddevice.

sounddevice ships a bundled PortAudio in its macOS wheel, so end users do NOT
need `brew install portaudio`. Audio is captured as mono float32 at 16 kHz —
exactly the format Whisper expects, so no resampling is needed.

We show ALL input devices and default to the system's chosen input (so a
clamshell/closed-lid setup using a USB or Continuity-camera mic just works).
"""

import numpy as np
import sounddevice as sd

from .log import get_logger

log = get_logger()

SAMPLE_RATE = 16000
CHANNELS = 1


def list_input_devices():
    """Return [(index, name), ...] for every input-capable device."""
    return [
        (i, d["name"])
        for i, d in enumerate(sd.query_devices())
        if d["max_input_channels"] > 0
    ]


def system_default_index():
    try:
        return sd.default.device[0]
    except Exception:
        return None


def device_name(index):
    if index is None:
        di = system_default_index()
        base = "system default"
        if di is not None:
            try:
                return f"{base} ({sd.query_devices(di)['name']})"
            except Exception:
                pass
        return base
    try:
        return sd.query_devices(index)["name"]
    except Exception:
        return f"device {index}"


def resolve_device(spec):
    """int index, numeric string, or name substring -> index. None -> system default."""
    if spec is None:
        return None
    if isinstance(spec, int):
        return spec
    spec = str(spec).strip()
    if spec.isdigit():
        return int(spec)
    for i, name in list_input_devices():
        if spec.lower() in name.lower():
            return i
    log.warning(f"no input device matched {spec!r}; using system default")
    return None


class Recorder:
    def __init__(self, device=None):
        self.device = device  # None => system default
        self._frames = []
        self._stream = None

    def _open(self, device):
        def _callback(indata, _frames, _time, status):
            if status:
                log.warning(f"audio callback status: {status}")
            self._frames.append(indata.copy())

        stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            device=device,
            callback=_callback,
        )
        stream.start()
        return stream

    def start(self):
        self._frames = []
        try:
            self._stream = self._open(self.device)
        except Exception as exc:
            # If a specific device fails, fall back to the system default once.
            if self.device is not None:
                log.warning(f"device {self.device!r} failed to open ({exc}); "
                            f"retrying system default")
                self._stream = self._open(None)
            else:
                raise
        log.info(f"recording started on '{device_name(self.device)}'")

    def stop(self):
        """Stop recording and return the captured audio as a 1-D float32 array."""
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        if not self._frames:
            log.warning("recording stopped with no frames captured")
            return np.zeros(0, dtype=np.float32)

        audio = np.concatenate(self._frames, axis=0).flatten()
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        log.info(f"recording stopped: {audio.size} samples, peak={peak:.4f}")
        return audio
