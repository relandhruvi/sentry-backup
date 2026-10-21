"""In-memory ring buffer of 16 kHz mono int16 audio, addressed by absolute sample index."""

from __future__ import annotations

import numpy as np

SAMPLE_RATE = 16_000
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 320
FRAME_BYTES = FRAME_SAMPLES * 2  # 640


class RingBuffer:
    """Keeps the most recent `capacity` samples. Older audio is overwritten, never written to disk."""

    def __init__(self, seconds: float, sample_rate: int = SAMPLE_RATE) -> None:
        self.sample_rate = sample_rate
        self.capacity = int(seconds * sample_rate)
        self._data = np.zeros(self.capacity, dtype=np.int16)
        self.total = 0  # samples written since the start of the call

    def write(self, samples: np.ndarray) -> None:
        n = len(samples)
        # Sample with absolute index i always lives at i % capacity.
        if n > self.capacity:
            self.total += n - self.capacity
            samples = samples[-self.capacity :]
            n = self.capacity
        start = self.total % self.capacity
        end = start + n
        if end <= self.capacity:
            self._data[start:end] = samples
        else:
            split = self.capacity - start
            self._data[start:] = samples[:split]
            self._data[: end - self.capacity] = samples[split:]
        self.total += n

    @property
    def oldest(self) -> int:
        return max(0, self.total - self.capacity)

    def read(self, start: int, end: int) -> np.ndarray:
        """Samples in [start, end) by absolute index. Raises if the range is no longer retained."""
        if not (self.oldest <= start <= end <= self.total):
            raise IndexError(f"range [{start}, {end}) not in buffer [{self.oldest}, {self.total})")
        idx = np.arange(start, end) % self.capacity
        return self._data[idx].copy()

    @property
    def seconds(self) -> float:
        return self.total / self.sample_rate
