"""Shared text normalization for WER scoring, the coercion lexicon and the classifier.

Whisper writes the same speech in different scripts and spellings: romanized Hinglish often comes
back in Devanagari ("सेफ अकाउंट"), Hindi words drift ("मैद" for "मत"), and English words get
mis-heard ("HTFC" for "HDFC"). Every consumer compares text through `normalize()`, so all of these
land on the same tokens:

  1. tokenize the original, keeping each token's character span
  2. romanize Devanagari (indic-transliteration ITRANS, then casual Hindi romanization)
  3. phonetic key: lowercase and fold spelling noise (ph→f, w→v, aspirates, long vowels, doubles)
  4. apply spelling variants and context-dependent STT confusions from variants.yaml

The original text is always kept; evidence shown to users uses `NormalizedText.span()`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml
from indic_transliteration import sanscript

VARIANTS_PATH = Path(__file__).with_name("variants.yaml")

# Letters/digits in any script, plus the Devanagari block (matras are not \w) minus the dandas.
_TOKEN = re.compile(r"(?:[^\W_]|[ऀ-ॣ०-ॿ])+")
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_CONSONANT = re.compile(r"[क-हक़-य़]")

# ITRANS -> casual Hindi romanization. Order matters (multi-char codes first).
_ITRANS_TO_CASUAL = [
    ("R^i", "ri"), ("RRi", "ri"), ("~N", "n"), ("~n", "n"), (".N", "n"), (".n", "n"),
    (".h", ""), (".Dh", "dh"), (".D", "d"), ("Sh", "sh"), ("M", "n"), ("H", "h"),
    ("A", "aa"), ("I", "ii"), ("U", "uu"), ("T", "t"), ("D", "d"), ("N", "n"), ("L", "l"),
    ("^", ""), (".", ""), ("{}", ""),
]  # fmt: skip

# Phonetic folding applied to every token (Latin or romanized). Order matters.
_PHONETIC = [
    (re.compile(r"ph"), "f"),
    (re.compile(r"w"), "v"),
    (re.compile(r"z"), "j"),
    (re.compile(r"q"), "k"),
    (re.compile(r"x"), "ks"),
    (re.compile(r"c(?=[eiy])"), "s"),
    (re.compile(r"c(?!h)"), "k"),
    (re.compile(r"([kgjtdb])h"), r"\1"),  # aspirates kh gh jh th dh bh (keeps ch, sh)
    (re.compile(r"aa"), "a"),
    (re.compile(r"ee|ii"), "i"),
    (re.compile(r"oo|uu"), "u"),
    (re.compile(r"ou"), "au"),
    (re.compile(r"(?<=[aeiou])ye$"), "e"),  # bataiye / बताइए -> bataie
    (re.compile(r"^(.{2,}[^aeiou])e$"), r"\1"),  # English silent e: crime -> krim
    (re.compile(r"y$"), "i"),
    (re.compile(r"(.)\1+"), r"\1"),
]


@dataclass(frozen=True)
class Token:
    norm: str
    start: int  # character span in the original text
    end: int


@dataclass(frozen=True)
class NormalizedText:
    original: str
    tokens: tuple[Token, ...]

    @property
    def text(self) -> str:
        return " ".join(t.norm for t in self.tokens)

    @property
    def norms(self) -> list[str]:
        return [t.norm for t in self.tokens]

    def span(self, first: int, last: int) -> str:
        """Original text covered by tokens[first:last+1]."""
        return self.original[self.tokens[first].start : self.tokens[last].end]


_NUKTA, _VIRAMA = "\u093c", "\u094d"
# Candra vowels (loanword "o"/"e" as in हॉस्पिटल) have no ITRANS code; use the plain vowel.
_CANDRA = str.maketrans(
    {"\u0949": "\u094b", "\u0911": "\u0913", "\u0945": "\u0947", "\u090d": "\u090f"}
)


def _is_consonant(ch: str) -> bool:
    return "\u0915" <= ch <= "\u0939" or "\u0958" <= ch <= "\u095f"


def _is_matra(ch: str) -> bool:
    return "\u093e" <= ch <= "\u094c" or ch in "\u0962\u0963"


def _is_sign(ch: str) -> bool:  # candrabindu, anusvara, visarga
    return "\u0900" <= ch <= "\u0903"


@dataclass
class _Akshara:
    head: str  # consonant cluster or independent vowel
    tail: str  # matra / virama / signs
    inherent: bool  # carries the inherent "a"
    vowel: bool  # has any vowel sound


def _aksharas(word: str) -> list[_Akshara]:
    out, i, n = [], 0, len(word)
    while i < n:
        j = i + 1
        if _is_consonant(word[i]):
            while j < n and (
                word[j] == _NUKTA
                or (word[j] == _VIRAMA and j + 1 < n and _is_consonant(word[j + 1]))
            ):
                j += 2 if word[j] == _VIRAMA else 1
            head_end, inherent, vowel = j, True, True
            if j < n and word[j] == _VIRAMA:
                j, inherent, vowel = j + 1, False, False
            elif j < n and _is_matra(word[j]):
                j, inherent = j + 1, False
        else:
            head_end, inherent, vowel = j, False, bool(_DEVANAGARI.match(word[i]))
        while j < n and _is_sign(word[j]):
            j += 1
        out.append(_Akshara(word[i:head_end], word[head_end:j], inherent, vowel))
        i = j
    return out


def _delete_schwas(word: str) -> str:
    """Hindi schwa deletion: अपने -> अप्ने (apne), करना -> कर्ना, but जनता keeps its first vowel.

    Right to left, the inherent "a" of a non-initial syllable is dropped when the word ends there,
    or when both neighbouring syllables are voiced (the classic V C _ C V context) and the next
    syllable doesn't start with a consonant cluster.
    """
    aks = _aksharas(word)
    last = len(aks) - 1
    for k in range(last, 0, -1):
        a = aks[k]
        if not a.inherent or a.tail:
            continue
        # Never before a conjunct: गिरफ्तार stays giraftaar, not girftaar.
        if k == last or (aks[k - 1].vowel and aks[k + 1].vowel and _VIRAMA not in aks[k + 1].head):
            a.tail, a.inherent, a.vowel = _VIRAMA, False, False
    return "".join(a.head + a.tail for a in aks)


def romanize_devanagari(word: str) -> str:
    word = _delete_schwas(word.translate(_CANDRA))
    out = sanscript.transliterate(word, sanscript.DEVANAGARI, sanscript.ITRANS)
    for src, dst in _ITRANS_TO_CASUAL:
        out = out.replace(src, dst)
    return out.lower()


def phonetic_key(token: str) -> str:
    out = token.lower()
    for pattern, repl in _PHONETIC:
        out = pattern.sub(repl, out)
    return out


def _base_key(raw: str) -> str:
    raw = unicodedata.normalize("NFC", raw)
    return phonetic_key(romanize_devanagari(raw) if _DEVANAGARI.search(raw) else raw)


def _phrase_keys(phrase: str) -> tuple[str, ...]:
    return tuple(_base_key(m.group()) for m in _TOKEN.finditer(phrase))


@dataclass(frozen=True)
class _Rule:
    source: tuple[str, ...]
    target: tuple[str, ...]
    near: frozenset[str]
    window: int


@lru_cache(maxsize=4)
def _rules(path: Path = VARIANTS_PATH) -> tuple[list[_Rule], int]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rules: list[_Rule] = []
    for c in data.get("confusions", []):
        rules.append(
            _Rule(
                _phrase_keys(c["from"]),
                _phrase_keys(c["to"]),
                frozenset(_base_key(w) for w in c.get("near", [])),
                int(c.get("window", 0)),
            )
        )
    for canonical, forms in (data.get("variants") or {}).items():
        target = _phrase_keys(canonical)
        for form in forms:
            source = _phrase_keys(form)
            if source and source != target:
                rules.append(_Rule(source, target, frozenset(), 0))
    # Longest source first so multi-word forms win over single-word ones.
    rules.sort(key=lambda r: -len(r.source))
    longest = max((len(r.source) for r in rules), default=1)
    return rules, longest


def _context_ok(rule: _Rule, keys: list[str], i: int) -> bool:
    if not rule.near:
        return True
    lo, hi = max(0, i - rule.window), i + len(rule.source) + rule.window
    return any(k in rule.near for k in keys[lo:hi])


def normalize(text: str, variants_path: Path = VARIANTS_PATH) -> NormalizedText:
    matches = list(_TOKEN.finditer(text))
    keys = [_base_key(m.group()) for m in matches]
    rules, longest = _rules(variants_path)

    tokens: list[Token] = []
    i = 0
    while i < len(matches):
        for rule in rules:
            n = len(rule.source)
            if n > longest or tuple(keys[i : i + n]) != rule.source:
                continue
            if not _context_ok(rule, keys, i):
                continue
            start, end = matches[i].start(), matches[i + n - 1].end()
            tokens.extend(Token(k, start, end) for k in rule.target)
            i += n
            break
        else:
            tokens.append(Token(keys[i], matches[i].start(), matches[i].end()))
            i += 1
    return NormalizedText(text, tuple(tokens))


def normalize_key(text: str) -> str:
    """Normalized text as one string (for lexicon patterns and classifier features)."""
    return normalize(text).text
