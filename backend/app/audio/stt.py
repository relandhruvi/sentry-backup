"""Speech-to-text providers.

FasterWhisperProvider runs on CPU (int8) in a single dedicated worker thread so inference never
blocks the event loop. Language is restricted to the configured set (Hindi/English): a language-ID
model scores every language, we pick the best of the allowed ones, then transcribe in it.

Language ID can use a smaller model than transcription (default: tiny for ID, small for text).
On this hardware ID with `small` costs ~1 s per utterance; with `tiny` it costs ~0.1 s.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal, Protocol

import numpy as np
from pydantic import BaseModel

from app.config import Settings
from app.events import ComponentStatus

log = logging.getLogger(__name__)

Lang = Literal["en", "hi", "mixed"]

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_LATIN = re.compile(r"[A-Za-z]")


class STTUnavailable(Exception):
    """Raised when the provider can't transcribe (not loaded, failed to load)."""


class Transcript(BaseModel):
    text: str
    lang: Lang
    detected_language: str  # language the text was decoded in
    lang_confidence: float  # language-ID probability of the top allowed language
    lang_decision: Literal["detected", "fallback"]
    lang_probs: dict[str, float]
    avg_logprob: float | None
    no_speech_prob: float | None
    inference_ms: float
    model: str


class STTProvider(Protocol):
    name: str

    def status(self) -> tuple[ComponentStatus, str]: ...

    async def load(self) -> None: ...

    async def wait_ready(self, budget_s: float) -> bool:
        """True once loading has finished (successfully or not); False if still loading."""
        ...

    async def transcribe(self, pcm: np.ndarray) -> Transcript:
        """Transcribe 16 kHz mono int16 audio. Raises STTUnavailable or TimeoutError."""
        ...


def script_lang(text: str, detected: str) -> Lang:
    """Label from the output script plus the detected language.

    'mixed' = at least two words in each of Devanagari and Latin script, or Latin-script text that
    Whisper detected as Hindi (romanized Hinglish).
    """
    words = text.split()
    deva = sum(1 for w in words if _DEVANAGARI.search(w))
    latin = sum(1 for w in words if _LATIN.search(w))
    if deva >= 2 and latin >= 2:
        return "mixed"
    if deva > latin:
        return "hi"
    if latin > deva:
        return "mixed" if detected == "hi" else "en"
    return "hi" if detected == "hi" else "en"


class FasterWhisperProvider:
    def __init__(
        self,
        model_size: str,
        compute_type: str,
        cpu_threads: int,
        beam_size: int,
        languages: list[str],
        timeout_s: float,
        download_root: Path,
        detect_model_size: str | None = None,
        repetition_penalty: float = 1.0,
        no_repeat_ngram_size: int = 0,
        lang_min_confidence: float = 0.0,
        lang_fallback: str | None = None,
        initial_prompt: str = "",
    ) -> None:
        self.model_size = model_size
        self.detect_model_size = detect_model_size or model_size
        self.name = f"faster-whisper/{model_size}"
        self._compute_type = compute_type
        self._cpu_threads = cpu_threads
        self._beam_size = beam_size
        self._languages = languages
        self._timeout_s = timeout_s
        self._download_root = download_root
        self._repetition_penalty = repetition_penalty
        self._no_repeat_ngram_size = no_repeat_ngram_size
        self._lang_min_confidence = lang_min_confidence
        self._lang_fallback = lang_fallback or languages[0]
        self._initial_prompt = initial_prompt or None
        self._ready = asyncio.Event()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stt")
        self._model = None
        self._detector = None
        self._status = (ComponentStatus.UNAVAILABLE, "not loaded")

    @classmethod
    def from_settings(cls, s: Settings, model_size: str | None = None) -> FasterWhisperProvider:
        return cls(
            model_size=model_size or s.stt_model,
            compute_type=s.stt_compute_type,
            cpu_threads=s.stt_cpu_threads,
            beam_size=s.stt_beam_size,
            languages=s.stt_languages,
            timeout_s=s.stt_timeout_s,
            download_root=s.resolved_stt_download_root,
            detect_model_size=s.stt_detect_model or None,
            repetition_penalty=s.stt_repetition_penalty,
            no_repeat_ngram_size=s.stt_no_repeat_ngram_size,
            lang_min_confidence=s.stt_lang_min_confidence,
            lang_fallback=s.stt_lang_fallback,
            initial_prompt=s.stt_initial_prompt,
        )

    def status(self) -> tuple[ComponentStatus, str]:
        return self._status

    def _describe(self) -> str:
        return (
            f"{self.name} {self._compute_type} cpu x{self._cpu_threads}, "
            f"lang-id={self.detect_model_size}, langs={','.join(self._languages)}"
        )

    async def load(self) -> None:
        self._status = (ComponentStatus.LOADING, f"loading {self._describe()}")
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self._executor, self._load_sync)
            # Warm-up pass so the first real utterance doesn't pay one-time initialisation costs.
            await loop.run_in_executor(
                self._executor, self._transcribe_sync, np.zeros(16_000, dtype=np.float32)
            )
        except Exception as exc:
            log.exception("STT model failed to load")
            self._model = self._detector = None
            self._status = (ComponentStatus.UNAVAILABLE, f"load failed: {exc}")
        else:
            self._status = (ComponentStatus.OK, self._describe())
        finally:
            self._ready.set()

    async def wait_ready(self, budget_s: float) -> bool:
        """Wait until loading has finished (successfully or not). False if still loading."""
        try:
            await asyncio.wait_for(self._ready.wait(), budget_s)
            return True
        except TimeoutError:
            return False

    def _load_sync(self) -> None:
        from faster_whisper import WhisperModel

        self._download_root.mkdir(parents=True, exist_ok=True)

        def load(size: str) -> WhisperModel:
            return WhisperModel(
                size,
                device="cpu",
                compute_type=self._compute_type,
                cpu_threads=self._cpu_threads,
                download_root=str(self._download_root),
            )

        self._model = load(self.model_size)
        self._detector = (
            self._model
            if self.detect_model_size == self.model_size
            else load(self.detect_model_size)
        )
        # Warm-up: the first inference allocates buffers and is several times slower; do it now
        # (one second of silence through both models) so the first real utterance isn't.
        self._transcribe_sync(np.zeros(16000, dtype=np.float32))

    async def transcribe(self, pcm: np.ndarray) -> Transcript:
        if self._model is None:
            raise STTUnavailable(self._status[1])
        audio = pcm.astype(np.float32) / 32768.0
        loop = asyncio.get_running_loop()
        # On timeout the worker thread still finishes this job; later jobs queue behind it.
        return await asyncio.wait_for(
            loop.run_in_executor(self._executor, self._transcribe_sync, audio),
            timeout=self._timeout_s,
        )

    def transcribe_blocking(self, pcm: np.ndarray) -> Transcript:
        """Synchronous variant for offline tools (benchmarks). Not for use on the event loop."""
        if self._model is None:
            self._load_sync()
        return self._transcribe_sync(pcm.astype(np.float32) / 32768.0)

    def _transcribe_sync(self, audio: np.ndarray) -> Transcript:
        t0 = time.perf_counter()
        _, _, all_probs = self._detector.detect_language(audio)
        allowed = {lang: p for lang, p in all_probs if lang in self._languages}
        top = max(allowed, key=allowed.get) if allowed else self._lang_fallback
        confidence = allowed.get(top, 0.0)
        if confidence >= self._lang_min_confidence:
            language, decision = top, "detected"
        else:
            language, decision = self._lang_fallback, "fallback"

        segments, _ = self._model.transcribe(
            audio,
            language=language,
            task="transcribe",  # never translate
            initial_prompt=self._initial_prompt,
            beam_size=self._beam_size,
            temperature=0.0,
            repetition_penalty=self._repetition_penalty,
            no_repeat_ngram_size=self._no_repeat_ngram_size,
            condition_on_previous_text=False,
            without_timestamps=True,
            vad_filter=False,
        )
        segments = list(segments)
        text = " ".join(s.text.strip() for s in segments).strip()
        avg_logprob = float(np.mean([s.avg_logprob for s in segments])) if segments else None
        no_speech = float(np.mean([s.no_speech_prob for s in segments])) if segments else None
        return Transcript(
            text=text,
            lang=script_lang(text, language),
            detected_language=language,
            lang_confidence=round(confidence, 4),
            lang_decision=decision,
            lang_probs={lang: round(p, 4) for lang, p in allowed.items()},
            avg_logprob=avg_logprob,
            no_speech_prob=no_speech,
            inference_ms=round((time.perf_counter() - t0) * 1000, 1),
            model=self.name,
        )

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
