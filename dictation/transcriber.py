"""Speech-to-text backend.

Uses faster-whisper (CTranslate2) — a torch-free, cross-platform Whisper
implementation. Runs entirely on the local machine; the only network touch is
the one-off model download from Hugging Face on first use. After that, offline.

Why not MLX? mlx-whisper's current release hard-depends on PyTorch, which would
drag back the ~2-3 GB footprint and the torch CVE surface this fork exists to
remove. CTranslate2 has neither dependency and also runs on Intel Macs, so the
same build ships to the whole company.

Latency notes (measured on an M3 Pro, `small`, int8):
  * Whisper always encodes a full 30 s window, so the encoder (~0.6 s) is the
    floor for any clip, however short.
  * faster-whisper's built-in auto-detect runs that encoder TWICE (once to
    detect, once to transcribe) — ~1.5 s for a 1 s clip. We detect the language
    ourselves on the exact segment the decoder will see and cache the encoder
    output, so auto-detect costs the same ~0.8 s as a fixed language.
  * Detection can be restricted to the languages the user actually speaks
    (e.g. Spanish + English). Unrestricted detection on short clips sometimes
    picks a neighbour (Portuguese, Galician…) and then mangles the text.
"""

import os

import numpy as np

from .log import get_logger

log = get_logger()

SAMPLE_RATE = 16000

# faster-whisper accepts these size names directly (auto-downloads the matching
# CTranslate2 model). A raw HF repo id or local path also works.
KNOWN_SIZES = (
    "tiny", "base", "small", "medium",
    "large-v2", "large-v3", "large-v3-turbo", "turbo", "distil-large-v3",
)

DEFAULT_MODEL = "small"  # good speed/accuracy balance; large-v3-turbo for max quality

# Below this much detected speech we treat the clip as an accidental tap.
MIN_SPEECH_SECONDS = 0.25

# Whisper "hallucinates" these on silence / background noise (it was trained on
# subtitled video). Substrings are dropped unconditionally; whole-utterance
# phrases only when the model itself thinks there was probably no speech.
_HALLUCINATION_SUBSTRINGS = (
    "amara.org",
    "subtítulos realizados por",
    "subtitulos realizados por",
    "subtítulos por la comunidad",
    "subtitles by the amara",
    "www.mooji.org",
)
_HALLUCINATION_PHRASES = {
    "thank you", "thanks for watching", "thank you for watching", "you",
    "bye", "gracias", "gracias por ver", "gracias por ver el video",
    "suscríbete", "subscribe", "música", "music", "applause", "aplausos",
}


# Personal-dictionary biasing. Measured on `small`: a bare glossary or the
# `hotwords` option barely moves it (a product name stays misspelled), but a short natural sentence
# in the spoken language reliably does ("Acme Cloud", "Jira", "pull request").
_VOCAB_SENTENCE = {"es": ("Hablamos de {}.", " y "), "en": ("We talked about {}.", " and ")}
MAX_VOCAB = 30


def vocab_prompt(words, language):
    words = [w for w in (words or []) if w.strip()][:MAX_VOCAB]
    if not words:
        return None
    template, conj = _VOCAB_SENTENCE.get(language, ("{}.", ", "))
    joined = words[0] if len(words) == 1 else ", ".join(words[:-1]) + conj + words[-1]
    return template.format(joined)


def _norm(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum() or ch.isspace()).strip()


def _cpu_threads() -> int:
    # CTranslate2 defaults to 4 threads; using the performance cores is ~25%
    # faster on Apple Silicon. Leave a couple of cores for the UI / audio.
    n = os.cpu_count() or 4
    return max(4, min(8, n - 2))


class Transcriber:
    def __init__(self, model: str = DEFAULT_MODEL,
                 device: str = "cpu", compute_type: str = "int8"):
        # Lazy import so `--help` / arg errors don't pay the import cost.
        from faster_whisper import WhisperModel

        self.model_name = model
        self._model = WhisperModel(model, device=device, compute_type=compute_type,
                                   cpu_threads=_cpu_threads())
        self._install_encode_cache()
        self.last_language = None

    # ---- encoder cache: lets our own language detection share its pass ----
    def _install_encode_cache(self):
        original = self._model.encode
        cache = {"key": None, "out": None}

        def cached_encode(features):
            key = features.tobytes() if isinstance(features, np.ndarray) else None
            if key is not None and key == cache["key"]:
                return cache["out"]
            out = original(features)
            cache["key"], cache["out"] = key, out
            return out

        def clear():
            cache["key"] = cache["out"] = None

        self._model.encode = cached_encode
        self._clear_encode_cache = clear

    def warm_up(self) -> None:
        """Force model load/download now so the first real dictation isn't slow."""
        # Real-ish signal (not pure zeros) so the decoder path is exercised too.
        t = np.linspace(0, 1, SAMPLE_RATE, dtype=np.float32)
        tone = (0.05 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
        list(self._model.transcribe(tone, language="en", beam_size=1,
                                    without_timestamps=True)[0])

    # ---- pipeline pieces ----
    def _speech_only(self, audio: np.ndarray) -> np.ndarray:
        """Silero VAD: keep only voiced chunks. Returns an empty array if none."""
        from faster_whisper.vad import VadOptions, collect_chunks, get_speech_timestamps

        opts = VadOptions(min_silence_duration_ms=500, speech_pad_ms=300)
        chunks = get_speech_timestamps(audio, opts)
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        pieces, _meta = collect_chunks(audio, chunks)
        return np.concatenate(pieces, axis=0).astype(np.float32)

    def _detect_language(self, speech: np.ndarray, allowed) -> str:
        """Detect the language on exactly the first window the decoder will see
        (so the encoder output is reused), restricted to `allowed` if given."""
        from faster_whisper.audio import pad_or_trim

        fe = self._model.feature_extractor
        features = fe(speech)
        content_frames = features.shape[-1] - 1
        segment = pad_or_trim(features[:, :min(fe.nb_max_frames, content_frames)])
        enc = self._model.encode(segment)
        results = self._model.model.detect_language(enc)[0]
        probs = {tok[2:-2]: p for tok, p in results}
        if allowed:
            best = max(allowed, key=lambda lang: probs.get(lang, 0.0))
        else:
            best = max(probs, key=probs.get)
        log.info("language probs: " + ", ".join(
            f"{k}={probs.get(k, 0):.2f}" for k in (allowed or [best])))
        return best

    def transcribe(self, audio: np.ndarray, language: str | None = None,
                   allowed_languages=None, vocabulary=None) -> str:
        """Transcribe a mono float32 array sampled at 16 kHz. Returns clean text.

        * `language`: force one language ('es', 'en', …). None => auto-detect.
        * `allowed_languages`: when auto-detecting, only pick among these.
        * `vocabulary`: names / jargon from the personal dictionary, biasing the
          decoder toward their spelling ("Acme Cloud" instead of "Akmi Cloud").

        task="transcribe" guarantees we NEVER translate — mixed Spanish/English
        speech is written as spoken.
        """
        self._clear_encode_cache()
        speech = self._speech_only(audio)
        voiced = speech.size / SAMPLE_RATE
        if voiced < MIN_SPEECH_SECONDS:
            log.info(f"no speech detected (voiced={voiced:.2f}s)")
            return ""

        lang = language or self._detect_language(speech, allowed_languages)
        self.last_language = lang

        segments, _info = self._model.transcribe(
            speech,
            language=lang,
            task="transcribe",            # never translate
            beam_size=5,
            vad_filter=False,              # already applied above
            condition_on_previous_text=False,  # stops repetition loops on long clips
            without_timestamps=True,
            initial_prompt=vocab_prompt(vocabulary, lang),
        )
        kept = []
        for seg in segments:
            text = seg.text.strip()
            low = text.lower()
            if any(s in low for s in _HALLUCINATION_SUBSTRINGS):
                log.info(f"dropped hallucinated segment: {text!r}")
                continue
            if seg.no_speech_prob > 0.6 and seg.avg_logprob < -0.8:
                log.info(f"dropped low-confidence segment: {text!r}")
                continue
            kept.append((seg, text))

        if len(kept) == 1:
            seg, text = kept[0]
            if _norm(text) in _HALLUCINATION_PHRASES and seg.no_speech_prob > 0.3:
                log.info(f"dropped probable hallucination: {text!r}")
                return ""
        return " ".join(t for _s, t in kept if t).strip()
