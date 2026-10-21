import numpy as np
import pytest

from app.audio.buffer import FRAME_BYTES, RingBuffer
from app.audio.stt import script_lang
from app.audio.vad import VadConfig, VadSegmenter

SPEECH = b"\x01" * FRAME_BYTES
SILENCE = b"\x00" * FRAME_BYTES

CFG = VadConfig(
    start_speech_ms=60, end_silence_ms=600, max_utterance_ms=8000, preroll_ms=200, min_speech_ms=300
)


def is_speech(frame: bytes) -> bool:
    return frame[0] == 1


def run(segmenter: VadSegmenter, frames: list[bytes]):
    out = [seg for f in frames if (seg := segmenter.push(f)) is not None]
    return out


def test_ring_buffer_reads_by_absolute_index_and_wraps():
    rb = RingBuffer(seconds=1, sample_rate=10)  # capacity 10
    rb.write(np.arange(7, dtype=np.int16))
    rb.write(np.arange(7, 14, dtype=np.int16))
    assert rb.total == 14 and rb.oldest == 4
    assert rb.read(4, 14).tolist() == list(range(4, 14))
    with pytest.raises(IndexError):
        rb.read(0, 5)
    rb.write(np.arange(14, 40, dtype=np.int16))  # larger than capacity
    assert rb.read(30, 40).tolist() == list(range(30, 40))


def test_utterance_ends_after_silence_with_preroll_and_trimmed_tail():
    seg = VadSegmenter(CFG, is_speech)
    frames = [SILENCE] * 20 + [SPEECH] * 50 + [SILENCE] * 30
    (u,) = run(seg, frames)
    assert u.end_reason == "silence"
    assert u.speech_ms == 1000
    # 200 ms preroll before the 3 trigger frames -> starts 10 frames before speech onset
    assert u.start_sample == (20 - 10) * 320
    # 100 ms of trailing silence kept
    assert u.end_sample == (20 + 50 + 5) * 320
    assert len(u.pcm) == u.end_sample - u.start_sample


LONG = VadConfig(
    start_speech_ms=60,
    end_silence_ms=600,
    max_utterance_ms=6000,
    preroll_ms=0,
    min_speech_ms=300,
    soft_max_ms=4000,
    soft_pause_ms=60,
)
QUIET_SPEECH = b"\x01" + b"\x00" * (FRAME_BYTES - 1)  # flagged speech, almost no energy


def test_long_speech_splits_at_first_word_gap_after_soft_limit():
    seg = VadSegmenter(LONG, is_speech)
    gap_at = 250  # 5 s of speech, then an 80 ms gap between words
    utts = run(seg, [SPEECH] * gap_at + [SILENCE] * 4 + [SPEECH] * 100 + [SILENCE] * 40)
    assert [u.end_reason for u in utts] == ["pause", "silence"]
    assert utts[0].end_sample == utts[1].start_sample
    # cut inside the gap (after 3 silent frames), never mid-word
    assert utts[0].end_sample == (gap_at + 3) * 320


def test_no_gap_cuts_at_quietest_frame_before_hard_limit():
    seg = VadSegmenter(LONG, is_speech)
    quiet_at = 250  # between the 4 s soft and 6 s hard limits
    frames = [SPEECH] * quiet_at + [QUIET_SPEECH] + [SPEECH] * 149 + [SILENCE] * 40
    utts = run(seg, frames)
    assert [u.end_reason for u in utts] == ["max_length", "silence"]
    assert utts[0].end_sample == quiet_at * 320  # cut right before the quiet frame
    assert utts[0].end_sample == utts[1].start_sample  # nothing lost
    assert (utts[1].end_sample - utts[0].start_sample) >= 400 * 320


def test_hard_limit_never_exceeded():
    seg = VadSegmenter(LONG, is_speech)
    utts = run(seg, [SPEECH] * 1000 + [SILENCE] * 40)
    assert all((u.end_sample - u.start_sample) / 16000 <= 6.0 for u in utts)
    assert all(a.end_sample == b.start_sample for a, b in zip(utts, utts[1:], strict=False))
    assert utts[0].start_sample == 0 and utts[-1].end_sample >= 1000 * 320  # no speech lost


def test_short_blips_are_discarded_and_flush_emits_open_utterance():
    seg = VadSegmenter(CFG, is_speech)
    assert run(seg, [SPEECH] * 5 + [SILENCE] * 40) == []  # 100 ms of speech < min 300 ms
    run(seg, [SPEECH] * 30)
    u = seg.flush()
    assert u is not None and u.end_reason == "flush"


def test_wrong_frame_size_rejected():
    with pytest.raises(ValueError):
        VadSegmenter(CFG, is_speech).push(b"\x00" * 100)


@pytest.mark.parametrize(
    ("text", "detected", "expected"),
    [
        ("Hello, how are you?", "en", "en"),
        ("जी, बोलिए, क्या बात है?", "hi", "hi"),
        ("आपका account block हो जाएगा, abhi transfer karo", "hi", "mixed"),
        ("aapka account block ho jayega", "hi", "mixed"),
    ],
)
def test_script_lang(text, detected, expected):
    assert script_lang(text, detected) == expected
