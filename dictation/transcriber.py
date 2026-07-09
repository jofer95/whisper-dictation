"""Speech-to-text backend.

Uses faster-whisper (CTranslate2) — a torch-free, cross-platform Whisper
implementation. Runs entirely on the local machine; the only network touch is
the one-off model download from Hugging Face on first use. After that, offline.

Why not MLX? mlx-whisper's current release hard-depends on PyTorch, which would
drag back the ~2-3 GB footprint and the torch CVE surface this fork exists to
remove. CTranslate2 has neither dependency and also runs on Intel Macs, so the
same build ships to the whole company.

The backend is isolated behind a tiny class so it can be swapped without
touching the rest of the app.
"""

import numpy as np

# faster-whisper accepts these size names directly (auto-downloads the matching
# CTranslate2 model). A raw HF repo id or local path also works.
KNOWN_SIZES = (
    "tiny", "base", "small", "medium",
    "large-v2", "large-v3", "distil-large-v3",
)

DEFAULT_MODEL = "small"  # good speed/accuracy balance; bump to large-v3 for max quality


class Transcriber:
    def __init__(self, model: str = DEFAULT_MODEL,
                 device: str = "cpu", compute_type: str = "int8"):
        # Lazy import so `--help` / arg errors don't pay the import cost.
        from faster_whisper import WhisperModel

        self._model = WhisperModel(model, device=device, compute_type=compute_type)

    def warm_up(self) -> None:
        """Force model load/download now so the first real dictation isn't slow."""
        silence = np.zeros(16000, dtype=np.float32)  # 1 s of silence @ 16 kHz
        list(self._model.transcribe(silence)[0])

    def transcribe(self, audio: np.ndarray, language: str | None = None) -> str:
        """Transcribe a mono float32 array sampled at 16 kHz. Returns clean text.

        task="transcribe" guarantees we NEVER translate — mixed Spanish/English
        speech is written as spoken. language=None auto-detects, which best
        preserves code-switching (forcing one language mangles the other).
        """
        segments, _info = self._model.transcribe(
            audio,
            language=language,     # None => auto-detect (best for mixing languages)
            task="transcribe",     # never translate to English
            beam_size=5,
            vad_filter=True,       # drop silence for cleaner results
        )
        return "".join(seg.text for seg in segments).strip()
