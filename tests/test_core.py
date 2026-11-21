"""Fast, offline unit tests for the deterministic parts of burstai (no models, no network)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.fingerprint import fingerprint_text, overlap, shared_positions, winnow  # noqa: E402
from app.plagiarism.search import build_queries  # noqa: E402
from app.textproc import split_sentences, tokenize_words  # noqa: E402
from app.aidetect.stylometry import features, style_score  # noqa: E402

ORIGINAL = (
    "The mitochondrion is a double-membrane-bound organelle found in most eukaryotic organisms. "
    "Mitochondria generate most of the cell's supply of adenosine triphosphate, which is used as a source of chemical energy. "
    "They were first discovered by Albert von Kolliker in 1857 in the voluntary muscles of insects."
)
COPIED = (
    "As we learned in class, mitochondria generate most of the cell's supply of adenosine triphosphate, which is used "
    "as a source of chemical energy. This is why they are called the powerhouse. "
    "They were first discovered by Albert von Kolliker in 1857 in the voluntary muscles of insects, which I found surprising."
)
UNRELATED = (
    "Yesterday the municipal council voted to extend the tram line by four stops towards the harbour district. "
    "Construction is expected to begin next spring and will take roughly eighteen months according to the engineers."
)


def test_sentence_split_offsets_roundtrip():
    text = "Dr. Smith went home. He said \"hello there, friend.\" Then he left!\n\nNew paragraph here"
    sents = split_sentences(text)
    assert [text[s.start:s.end] for s in sents] == [s.text for s in sents]
    assert sents[0].text.startswith("Dr. Smith went home.")  # abbreviation not split
    assert len(sents) == 4


def test_reference_section_and_quote_flags():
    body = "This is the essay body. " * 20
    text = body + "\n\nReferences\nSmith, J. (2020). A paper. Journal of Things, 1(2), 3-4.\n"
    sents = split_sentences(text)
    assert any(s.is_reference for s in sents)
    assert not sents[0].is_reference
    q = split_sentences('"To be or not to be, that is the question," he said.')
    assert q[0].is_quote


def test_winnowing_detects_copy_and_ignores_unrelated():
    a, b, c = fingerprint_text(ORIGINAL), fingerprint_text(COPIED), fingerprint_text(UNRELATED)
    assert overlap(a, b) > 0.4
    assert overlap(a, c) == 0.0
    assert shared_positions(a, b)


def test_winnowing_guarantee_eight_word_run():
    base = tokenize_words(UNRELATED)
    run = tokenize_words("one two three four five six seven eight")
    doc1 = winnow(base[:10] + run + base[10:])
    doc2 = winnow(run)
    assert overlap(doc2, doc1) > 0


def test_build_queries_prefers_distinctive_sentences():
    text = ORIGINAL + "\n\nReferences\nKolliker A. (1857). Some paper."
    qs = build_queries(split_sentences(text), 5)
    assert qs and all(q.startswith('"') and q.endswith('"') for q in qs)
    assert all("1857" not in q or "Some paper" not in q for q in qs)  # reference lines skipped


def test_stylometry_separates_samples():
    root = Path(__file__).resolve().parent.parent / "static" / "samples"
    human = (root / "human.txt").read_text(encoding="utf-8")
    ai = (root / "ai.txt").read_text(encoding="utf-8")
    ph, _ = style_score(features(human, split_sentences(human)))
    pa, _ = style_score(features(ai, split_sentences(ai)))
    assert pa > 0.6 > ph, (pa, ph)
