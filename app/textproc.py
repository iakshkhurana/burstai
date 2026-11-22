"""Text preprocessing: sentence segmentation with character offsets, normalisation,
quote / citation / reference-section detection.

Everything here is dependency-free so it can run inside the fingerprinting hot path.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

STOPWORDS = frozenset(
    """a about above after again against all am an and any are as at be because been
    before being below between both but by can could did do does doing down during each
    few for from further had has have having he her here hers herself him himself his how
    i if in into is it its itself just me more most my myself no nor not now of off on once
    only or other our ours ourselves out over own same she should so some such than that
    the their theirs them themselves then there these they this those through to too under
    until up very was we were what when where which while who whom why will with would you
    your yours yourself yourselves""".split()
)

_ABBREV = re.compile(
    r"\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|vs|etc|e\.g|i\.e|Fig|No|Vol|pp|al|Inc|Ltd|Co|U\.S|U\.K)\.$",
    re.IGNORECASE,
)
# Sentence terminators include the Devanagari danda so Hindi and friends segment correctly.
_SENT_END = re.compile(r"([.!?।॥]+[\"'”’)\]]*)(\s+)")
try:  # Unicode-aware words (letters + combining marks + digits) in any script
    import regex as _rx

    _WORD = _rx.compile(r"[\p{L}\p{N}][\p{L}\p{M}\p{N}'’-]*")
except ImportError:  # pragma: no cover - ASCII-ish fallback
    _WORD = re.compile(r"[^\W_][\w'’-]*")
_REF_HEADING = re.compile(
    r"^\s*(references?|bibliography|works\s+cited|sources?|citations?|literature\s+cited)\s*:?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_CITATION = re.compile(
    r"(\(\s*[A-Z][A-Za-z\-]+(?:\s+(?:et\s+al\.?|and|&)\s+[A-Z][A-Za-z\-]+)*,?\s+\d{4}[a-z]?(?:,\s*p+\.?\s*\d+)?\s*\))"
    r"|(\[\d+(?:[,\-–]\s*\d+)*\])"
    r"|(doi:\s*\S+|https?://\S+)",
    re.IGNORECASE,
)
_QUOTE_SPAN = re.compile(r"[\"“”']([^\"“”']{20,})[\"“”']")


@dataclass
class Sentence:
    index: int
    text: str
    start: int
    end: int
    words: list[str] = field(default_factory=list)
    is_quote: bool = False
    is_reference: bool = False
    is_citation: bool = False

    @property
    def word_count(self) -> int:
        return len(self.words)


def tokenize_words(text: str) -> list[str]:
    """Lower-cased alphanumeric tokens (apostrophes/hyphens kept inside words)."""
    return [w.lower().replace("’", "'") for w in _WORD.findall(text)]


def word_tokens(text: str) -> list[str]:
    """Words in their original case (used to build search phrases)."""
    return _WORD.findall(text)


def normalize(text: str) -> str:
    return " ".join(tokenize_words(text))


def split_sentences(text: str) -> list[Sentence]:
    """Split into sentences, preserving exact character offsets into *text*.

    Paragraph breaks always end a sentence; otherwise we split after .!? followed by
    whitespace, unless the preceding token is a known abbreviation or a single initial.
    """
    sentences: list[Sentence] = []
    ref_start = _reference_section_start(text)

    for para in re.finditer(r"[^\n]+", text):
        p_text, p_off = para.group(0), para.start()
        cursor = 0
        for m in _SENT_END.finditer(p_text):
            candidate = p_text[cursor : m.end(1)]
            if _ABBREV.search(candidate) or re.search(r"\b[A-Z]\.$", candidate):
                continue
            if len(tokenize_words(candidate)) < 2:
                continue  # don't split on things like "3." list markers
            _push(sentences, text, p_off + cursor, p_off + m.end(1), ref_start)
            cursor = m.end()
        if cursor < len(p_text):
            _push(sentences, text, p_off + cursor, p_off + len(p_text), ref_start)
    return sentences


def _push(out: list[Sentence], text: str, start: int, end: int, ref_start: int | None) -> None:
    raw = text[start:end]
    stripped = raw.strip()
    if not stripped:
        return
    lead = len(raw) - len(raw.lstrip())
    start += lead
    end = start + len(stripped)
    words = tokenize_words(stripped)
    if not words:
        return
    s = Sentence(index=len(out), text=stripped, start=start, end=end, words=words)
    s.is_reference = ref_start is not None and start >= ref_start
    s.is_quote = _looks_quoted(stripped)
    s.is_citation = bool(_CITATION.search(stripped)) and len(words) <= 12
    out.append(s)


def _looks_quoted(s: str) -> bool:
    if len(s) > 2 and s[0] in "\"“'" and s[-1] in "\"”'.,":
        return True
    quoted = sum(len(m.group(1)) for m in _QUOTE_SPAN.finditer(s))
    return quoted >= 0.6 * len(s)


def _reference_section_start(text: str) -> int | None:
    """Offset of the last 'References'-style heading that sits in the final 40% of the text."""
    best = None
    for m in _REF_HEADING.finditer(text):
        if m.start() >= 0.4 * len(text):
            best = m.start()
    return best


def strip_citations(text: str) -> str:
    return _CITATION.sub(" ", text)


def content_words(words: list[str]) -> list[str]:
    return [w for w in words if w not in STOPWORDS and len(w) > 2]


def english_score(words: list[str]) -> float:
    """Share of tokens that are common English function words. Ordinary English prose scores
    roughly 0.30-0.50; other languages (and romanised ones such as Hinglish) score well under 0.15.
    Used to refuse AI-detection verdicts on text the English-only language models cannot judge."""
    if not words:
        return 0.0
    return sum(1 for w in words if w in STOPWORDS) / len(words)


def is_english(words: list[str], threshold: float = 0.15) -> bool:
    return len(words) < 30 or english_score(words) >= threshold
