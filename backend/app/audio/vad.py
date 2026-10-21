"""Utterance segmentation from 20 ms frames.

An utterance starts after `start_speech_ms` of consecutive speech (plus `preroll_ms` of lead-in so
first syllables aren't clipped). It ends after `end_silence_ms` of silence. Long speech is split
without cutting words where possible:
  - after `soft_max_ms`, at the first pause of `soft_pause_ms` (a gap between words);
  - at `max_utterance_ms` (hard limit), at the quietest frame after `soft_max_ms`; the audio after
    that point starts the next utterance.
Utterances with less than `min_speech_ms` of speech are discarded as noise.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import numpy as np

from app.audio.buffer import FRAME_BYTES, FRAME_MS, FRAME_SAMPLES, SAMPLE_RATE
from app.config import Settings

EndReason = Literal["silence", "pause", "max_length", "flush"]

# Silence kept at the end of an utterance; the rest of the end-of-speech silence is trimmed.
TAIL_KEEP_MS = 100


@dataclass(frozen=True)
class VadConfig:
    start_speech_ms: int
    end_silence_ms: int
    max_utterance_ms: int
    preroll_ms: int
    min_speech_ms: int
    soft_max_ms: int = 8000
    soft_pause_ms: int = 60

    @classmethod
    def from_settings(cls, s: Settings) -> VadConfig:
        return cls(
            start_speech_ms=s.vad_start_speech_ms,
            end_silence_ms=s.vad_end_silence_ms,
            max_utterance_ms=s.vad_max_utterance_ms,
            preroll_ms=s.vad_preroll_ms,
            min_speech_ms=s.vad_min_speech_ms,
            soft_max_ms=s.vad_soft_max_ms,
            soft_pause_ms=s.vad_soft_pause_ms,
        )


@dataclass(frozen=True)
class Segment:
    start_sample: int
    end_sample: int
    pcm: np.ndarray  # int16
    speech_ms: int
    end_reason: EndReason

    @property
    def start_s(self) -> float:
        return self.start_sample / SAMPLE_RATE

    @property
    def end_s(self) -> float:
        return self.end_sample / SAMPLE_RATE


class WebRtcVad:
    """Frame classifier backed by webrtcvad."""

    name = "webrtcvad"

    def __init__(self, aggressiveness: int) -> None:
        import webrtcvad

        self._vad = webrtcvad.Vad(aggressiveness)
        self.aggressiveness = aggressiveness

    def __call__(self, frame: bytes) -> bool:
        return self._vad.is_speech(frame, SAMPLE_RATE)


def _frames(ms: int) -> int:
    return max(1, -(-ms // FRAME_MS))  # ceil


class VadSegmenter:
    def __init__(self, config: VadConfig, is_speech: Callable[[bytes], bool]) -> None:
        self.cfg = config
        self._is_speech = is_speech
        self._start_frames = _frames(config.start_speech_ms)
        self._end_frames = _frames(config.end_silence_ms)
        self._max_frames = _frames(config.max_utterance_ms)
        self._soft_frames = min(self._max_frames, _frames(config.soft_max_ms))
        self._pause_frames = _frames(config.soft_pause_ms)
        self._min_speech_frames = config.min_speech_ms // FRAME_MS
        self._tail_keep = TAIL_KEEP_MS // FRAME_MS
        self._preroll: deque[bytes] = deque(
            maxlen=self._start_frames + config.preroll_ms // FRAME_MS
        )
        self._frame_index = 0  # frames seen so far
        self._consecutive = 0
        self._in_speech = False
        self._frames_buf: list[bytes] = []
        self._flags: list[bool] = []  # speech flag per buffered frame
        self._start_frame = 0
        self._speech_frames = 0
        self._trailing_silence = 0

    def push(self, frame: bytes) -> Segment | None:
        if len(frame) != FRAME_BYTES:
            raise ValueError(f"expected {FRAME_BYTES}-byte frames, got {len(frame)}")
        speech = self._is_speech(frame)
        self._frame_index += 1

        if not self._in_speech:
            self._preroll.append(frame)
            self._consecutive = self._consecutive + 1 if speech else 0
            if self._consecutive >= self._start_frames:
                self._in_speech = True
                self._frames_buf = list(self._preroll)
                self._flags = [True] * len(self._frames_buf)
                self._start_frame = self._frame_index - len(self._frames_buf)
                self._speech_frames = self._consecutive
                self._trailing_silence = 0
                self._preroll.clear()
                self._consecutive = 0
            return None

        self._frames_buf.append(frame)
        self._flags.append(speech)
        if speech:
            self._speech_frames += 1
            self._trailing_silence = 0
        else:
            self._trailing_silence += 1

        if self._trailing_silence >= self._end_frames:
            trim = max(0, self._trailing_silence - self._tail_keep)
            return self._finish("silence", trim=trim, keep_speaking=False)
        n = len(self._frames_buf)
        if (
            n >= self._soft_frames
            and self._trailing_silence > 0
            and (self._trailing_silence >= self._pause_frames)
        ):
            return self._split(n, "pause")
        if n >= self._max_frames:
            return self._split(self._quietest_cut(), "max_length")
        return None

    def _quietest_cut(self) -> int:
        """Index of the lowest-energy frame after the soft limit (the cut goes before it)."""
        window = self._frames_buf[self._soft_frames :]
        energy = [
            float(np.mean(np.frombuffer(f, dtype=np.int16).astype(np.float32) ** 2)) for f in window
        ]
        return self._soft_frames + int(np.argmin(energy)) if energy else len(self._frames_buf)

    def _split(self, cut: int, reason: EndReason) -> Segment | None:
        """Emit frames[:cut]; frames[cut:] start the next utterance (speech continues)."""
        rest, rest_flags = self._frames_buf[cut:], self._flags[cut:]
        self._frames_buf, self._flags = self._frames_buf[:cut], self._flags[:cut]
        self._speech_frames = sum(self._flags)
        segment = self._finish(reason, trim=0, keep_speaking=True)
        self._frames_buf, self._flags = rest, rest_flags
        self._start_frame = self._frame_index - len(rest)
        self._speech_frames = sum(rest_flags)
        trailing = 0
        for flag in reversed(rest_flags):
            if flag:
                break
            trailing += 1
        self._trailing_silence = trailing
        return segment

    def flush(self) -> Segment | None:
        if not self._in_speech:
            return None
        return self._finish("flush", trim=self._trailing_silence, keep_speaking=False)

    def _finish(self, reason: EndReason, trim: int, keep_speaking: bool) -> Segment | None:
        frames = self._frames_buf[: len(self._frames_buf) - trim] if trim else self._frames_buf
        start = self._start_frame
        speech_frames = self._speech_frames

        if keep_speaking:
            # Forced cut mid-speech: the next utterance continues from the next frame.
            self._frames_buf, self._flags = [], []
            self._start_frame = self._frame_index
            self._speech_frames = 0
            self._trailing_silence = 0
        else:
            self._in_speech = False
            self._frames_buf, self._flags = [], []
            self._consecutive = 0

        if speech_frames < self._min_speech_frames or not frames:
            return None
        pcm = np.frombuffer(b"".join(frames), dtype=np.int16).copy()
        return Segment(
            start_sample=start * FRAME_SAMPLES,
            end_sample=(start + len(frames)) * FRAME_SAMPLES,
            pcm=pcm,
            speech_ms=speech_frames * FRAME_MS,
            end_reason=reason,
        )
