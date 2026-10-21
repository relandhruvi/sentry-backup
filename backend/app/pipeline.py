"""Per-call audio pipeline.

frames -> ring buffer -> VAD -> STT worker -> utterance event -> text analysis (coercion, entities,
risk) -> alert dispatcher (first HIGH/CRITICAL)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Literal

import numpy as np
from pydantic import BaseModel

from app.alerts.dispatcher import CallAlerts
from app.analysis import CallAnalyzer
from app.audio.buffer import FRAME_BYTES, RingBuffer
from app.audio.stt import STTProvider, STTUnavailable
from app.audio.vad import Segment, VadSegmenter
from app.components import ComponentRegistry
from app.config import Settings
from app.events import ComponentStatus, EventBus, EventType

log = logging.getLogger(__name__)

SttStatus = Literal["ok", "unavailable", "timeout", "dropped", "error"]


class UtterancePayload(BaseModel):
    utterance_id: str
    t_start_s: float
    t_end_s: float
    end_reason: str
    speech_ms: int
    stt_status: SttStatus
    text: str | None = None
    lang: str | None = None
    detected_language: str | None = None
    lang_confidence: float | None = None
    lang_decision: str | None = None
    lang_probs: dict[str, float] = {}
    avg_logprob: float | None = None
    no_speech_prob: float | None = None
    # Time from end of utterance to transcript ready (queue wait + inference).
    stt_latency_ms: float | None = None
    inference_ms: float | None = None
    stt_model: str | None = None
    detail: str = ""


class CallPipeline:
    def __init__(
        self,
        session_id: str,
        bus: EventBus,
        settings: Settings,
        stt: STTProvider,
        segmenter: VadSegmenter,
        components: ComponentRegistry,
        analyzer: CallAnalyzer | None = None,
        alerts: CallAlerts | None = None,
    ) -> None:
        self.session_id = session_id
        self._analyzer = analyzer
        self._alerts = alerts
        self._bus = bus
        self._settings = settings
        self._stt = stt
        self._segmenter = segmenter
        self._components = components
        self.buffer = RingBuffer(settings.audio_ring_seconds)
        self._queue: asyncio.Queue[tuple[Segment, float]] = asyncio.Queue()
        self._utterance_count = 0
        self._worker = asyncio.create_task(self._run(), name=f"stt-worker-{session_id[:8]}")

    # ---- ingest -------------------------------------------------------------------------------

    def feed(self, data: bytes) -> None:
        """Accept one or more whole 20 ms frames."""
        if not data or len(data) % FRAME_BYTES:
            raise ValueError(
                f"audio must be whole {FRAME_BYTES}-byte frames, got {len(data)} bytes"
            )
        self.buffer.write(np.frombuffer(data, dtype=np.int16))
        for offset in range(0, len(data), FRAME_BYTES):
            segment = self._segmenter.push(data[offset : offset + FRAME_BYTES])
            if segment is not None:
                self._enqueue(segment)

    def flush(self) -> None:
        segment = self._segmenter.flush()
        if segment is not None:
            self._enqueue(segment)

    def _enqueue(self, segment: Segment) -> None:
        if self._queue.qsize() >= self._settings.stt_max_backlog:
            dropped, _ = self._queue.get_nowait()
            self._queue.task_done()
            self._components.set(
                "stt", ComponentStatus.DEGRADED, "transcription backlog: dropping utterances"
            )
            asyncio.get_running_loop().create_task(
                self._publish(self._payload(dropped, "dropped", detail="STT backlog full"))
            )
        self._queue.put_nowait((segment, time.perf_counter()))

    # ---- transcription worker -----------------------------------------------------------------

    async def _run(self) -> None:
        while True:
            segment, closed_at = await self._queue.get()
            try:
                await self._transcribe(segment, closed_at)
            except Exception:
                log.exception("utterance processing failed")
            finally:
                self._queue.task_done()

    async def _transcribe(self, segment: Segment, closed_at: float) -> None:
        try:
            result = await self._stt.transcribe(segment.pcm)
        except STTUnavailable as exc:
            await self._publish(self._payload(segment, "unavailable", detail=str(exc)))
            return
        except TimeoutError:
            self._components.set(
                "stt",
                ComponentStatus.DEGRADED,
                f"transcription exceeded {self._settings.stt_timeout_s}s timeout",
            )
            await self._publish(self._payload(segment, "timeout"))
            return
        except Exception as exc:
            log.exception("STT failed")
            await self._publish(self._payload(segment, "error", detail=repr(exc)))
            return

        if self._components.get("stt").status == ComponentStatus.DEGRADED and self._queue.empty():
            self._components.set("stt", *self._stt.status())

        no_speech = (
            result.no_speech_prob is not None
            and result.avg_logprob is not None
            and result.no_speech_prob > self._settings.stt_no_speech_threshold
            and result.avg_logprob < self._settings.stt_logprob_threshold
        )
        if not result.text or no_speech:
            log.debug("dropping non-speech segment %.2f-%.2fs", segment.start_s, segment.end_s)
            return

        payload = self._payload(segment, "ok")
        payload.text = result.text
        payload.lang = result.lang
        payload.detected_language = result.detected_language
        payload.lang_confidence = result.lang_confidence
        payload.lang_decision = result.lang_decision
        payload.lang_probs = result.lang_probs
        payload.avg_logprob = result.avg_logprob
        payload.no_speech_prob = result.no_speech_prob
        payload.inference_ms = result.inference_ms
        payload.stt_latency_ms = round((time.perf_counter() - closed_at) * 1000, 1)
        payload.stt_model = result.model
        log.info(
            "%s %s %.1f-%.1fs lang=%s (%s, p=%.2f) stt=%.0fms",
            self.session_id[:8],
            payload.utterance_id,
            segment.start_s,
            segment.end_s,
            result.detected_language,
            result.lang_decision,
            result.lang_confidence,
            payload.stt_latency_ms,
        )
        await self._publish(payload)
        await self._analyze(payload)

    async def _analyze(self, utterance: UtterancePayload) -> None:
        if self._analyzer is None or not utterance.text:
            return
        signals = {name: self._components.get(name).status.value for name in ("voice", "verify")}
        try:
            coercion, entities, risk = await self._analyzer.analyze(
                utterance.utterance_id, utterance.t_start_s, utterance.text, signals
            )
        except Exception:
            log.exception("text analysis failed for %s", utterance.utterance_id)
            self._components.set("lexicon", ComponentStatus.DEGRADED, "analysis error, see logs")
            return
        await self._bus.publish(EventType.COERCION, coercion)
        if entities is not None:
            await self._bus.publish(EventType.ENTITIES, entities)
        await self._bus.publish(EventType.RISK, risk)
        if self._alerts is not None:
            try:
                await self._alerts.on_risk(risk, self._analyzer.claims, signals["verify"])
            except Exception:
                log.exception("alert dispatch failed for %s", utterance.utterance_id)

    def _payload(self, segment: Segment, status: SttStatus, detail: str = "") -> UtterancePayload:
        self._utterance_count += 1
        return UtterancePayload(
            utterance_id=f"u{self._utterance_count}",
            t_start_s=round(segment.start_s, 3),
            t_end_s=round(segment.end_s, 3),
            end_reason=segment.end_reason,
            speech_ms=segment.speech_ms,
            stt_status=status,
            detail=detail,
        )

    async def _publish(self, payload: UtterancePayload) -> None:
        await self._bus.publish(EventType.UTTERANCE, payload)

    # ---- lifecycle ----------------------------------------------------------------------------

    async def drain(self, budget_s: float) -> bool:
        """Flush the VAD and wait for queued utterances to be transcribed. False on timeout."""
        self.flush()
        try:
            await asyncio.wait_for(self._queue.join(), timeout=budget_s)
            return True
        except TimeoutError:
            return False

    async def close(self) -> None:
        self._worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._worker
