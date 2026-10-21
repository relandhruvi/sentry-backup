"""Word/character error rate of transcripts against the synthetic script text.

Utterances are matched to script turns by time overlap (the renderer records each turn's start and
end in the manifest), so the score doesn't depend on the VAD cutting exactly at turn boundaries.

Two scores per turn:
  raw         lowercase + punctuation stripped, scripts compared as written
  normalized  both sides through app.text.normalize (romanize Devanagari, phonetic key, variants),
              the same normalization the lexicon and classifier use
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field

from rapidfuzz.distance import Levenshtein

from app.text.normalize import normalize as shared_normalize

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_LATIN = re.compile(r"[A-Za-z]")


def raw_tokens(text: str) -> list[str]:
    text = unicodedata.normalize("NFC", text).lower()
    # Drop punctuation (incl. Devanagari danda) but keep letters, marks (matras) and digits.
    cleaned = "".join(
        ch if unicodedata.category(ch)[0] in "LMN" or ch.isspace() else " " for ch in text
    )
    return cleaned.split()


def norm_tokens(text: str) -> list[str]:
    return shared_normalize(text).norms


def dominant_script(words: list[str]) -> str:
    deva = sum(1 for w in words if _DEVANAGARI.search(w))
    latin = sum(1 for w in words if _LATIN.search(w))
    if not deva and not latin:
        return "none"
    return "devanagari" if deva >= latin else "latin"


def _edits(ref: list[str], hyp: list[str]) -> tuple[int, int]:
    return Levenshtein.distance(ref, hyp), Levenshtein.distance("".join(ref), "".join(hyp))


@dataclass
class TurnScore:
    lang: str
    ref_text: str
    hyp_text: str

    def __post_init__(self) -> None:
        self.ref_raw, self.hyp_raw = raw_tokens(self.ref_text), raw_tokens(self.hyp_text)
        self.ref_norm, self.hyp_norm = norm_tokens(self.ref_text), norm_tokens(self.hyp_text)
        self.raw_edits = _edits(self.ref_raw, self.hyp_raw)
        self.norm_edits = _edits(self.ref_norm, self.hyp_norm)

    @property
    def script_mismatch(self) -> bool:
        return bool(self.hyp_raw) and dominant_script(self.ref_raw) != dominant_script(self.hyp_raw)


@dataclass
class Report:
    turns: list[TurnScore] = field(default_factory=list)
    unmatched_utterances: int = 0

    def by_lang(self) -> dict[str, dict[str, float]]:
        groups: dict[str, list[TurnScore]] = defaultdict(list)
        for t in self.turns:
            groups[t.lang].append(t)
        groups["all"] = list(self.turns)
        out = {}
        for lang, ts in groups.items():
            raw_words = sum(len(t.ref_raw) for t in ts)
            raw_chars = sum(len("".join(t.ref_raw)) for t in ts)
            norm_words = sum(len(t.ref_norm) for t in ts)
            norm_chars = sum(len("".join(t.ref_norm)) for t in ts)
            out[lang] = {
                "turns": len(ts),
                "ref_words": raw_words,
                "raw_wer": sum(t.raw_edits[0] for t in ts) / max(raw_words, 1),
                "raw_cer": sum(t.raw_edits[1] for t in ts) / max(raw_chars, 1),
                "norm_wer": sum(t.norm_edits[0] for t in ts) / max(norm_words, 1),
                "norm_cer": sum(t.norm_edits[1] for t in ts) / max(norm_chars, 1),
                "script_mismatch": sum(t.script_mismatch for t in ts) / max(len(ts), 1),
                "missed_turns": sum(1 for t in ts if not t.hyp_raw) / max(len(ts), 1),
            }
        return out

    def extend(self, other: Report) -> None:
        self.turns.extend(other.turns)
        self.unmatched_utterances += other.unmatched_utterances


def score(manifest: dict, utterances: list[tuple[float, float, str]]) -> Report:
    """utterances: (t_start_s, t_end_s, text) in the same time base as the manifest."""
    turns = manifest["turns"]
    assigned: dict[int, list[tuple[float, str]]] = defaultdict(list)
    unmatched = 0
    for start, end, text in utterances:
        overlaps = [(min(end, t["end_s"]) - max(start, t["start_s"]), t["index"]) for t in turns]
        best, idx = max(overlaps)
        if best <= 0:
            unmatched += 1
            continue
        assigned[idx].append((start, text))

    report = Report(unmatched_utterances=unmatched)
    for t in turns:
        hyp_text = " ".join(text for _, text in sorted(assigned.get(t["index"], [])))
        report.turns.append(TurnScore(lang=t["lang"], ref_text=t["text"], hyp_text=hyp_text))
    return report


def format_table(rows: dict[str, dict[str, float]]) -> str:
    header = (
        f"{'lang':10s} {'turns':>5s} {'words':>6s} │ {'raw WER':>8s} {'raw CER':>8s} │ "
        f"{'norm WER':>8s} {'norm CER':>8s} │ {'script≠':>7s} {'missed':>6s}"
    )
    lines = [header]
    for lang in sorted(rows, key=lambda k: (k == "all", k)):
        r = rows[lang]
        lines.append(
            f"{lang:10s} {r['turns']:5d} {r['ref_words']:6d} │ {r['raw_wer']:8.1%} {r['raw_cer']:8.1%} │ "
            f"{r['norm_wer']:8.1%} {r['norm_cer']:8.1%} │ {r['script_mismatch']:7.0%} {r['missed_turns']:6.0%}"
        )
    return "\n".join(lines)
