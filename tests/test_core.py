"""Fast, offline unit tests for the deterministic parts of burstai (no models, no network)."""
from __future__ import annotations

from pathlib import Path

from app.aidetect.stylometry import features, style_score
from app.fingerprint import fingerprint_text, overlap, shared_positions, winnow
from app.grammar import _noise
from app.plagiarism.search import build_queries
from app.reports import sanitize_view
from app.security import RateLimiter, is_public_url, text_hash
from app.textproc import english_score, is_english, split_sentences, tokenize_words

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
SAMPLES = Path(__file__).resolve().parent.parent / "static" / "samples"


def test_sentence_split_offsets_roundtrip():
    text = "Dr. Smith went home. He said \"hello there, friend.\" Then he left!\n\nNew paragraph here"
    sents = split_sentences(text)
    assert [text[s.start:s.end] for s in sents] == [s.text for s in sents]
    assert sents[0].text.startswith("Dr. Smith went home.")  # abbreviation not split
    assert len(sents) == 4


def test_unicode_tokenisation_and_danda_sentences():
    hindi = "यह पहला वाक्य है। यह दूसरा वाक्य है। और यह तीसरा है।"
    assert len(split_sentences(hindi)) == 3
    assert tokenize_words("हिंदी भाषा naïve café 北京") == ["हिंदी", "भाषा", "naïve", "café", "北京"]


def test_english_gate():
    eng = tokenize_words(ORIGINAL + " " + UNRELATED)
    assert is_english(eng) and english_score(eng) > 0.25
    hinglish = tokenize_words("yaar kal ka test bilkul bakwaas gaya maine teen din se sirf unit do padha tha aur paper mein kuch nahi aaya " * 3)
    assert not is_english(hinglish)
    assert is_english(tokenize_words("too short to judge"))  # short texts are not rejected by the gate


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
    assert all("Some paper" not in q for q in qs)  # reference lines skipped


def test_stylometry_separates_samples():
    human = (SAMPLES / "human.txt").read_text(encoding="utf-8")
    ai = (SAMPLES / "ai.txt").read_text(encoding="utf-8")
    ph, _ = style_score(features(human, split_sentences(human)))
    pa, _ = style_score(features(ai, split_sentences(ai)))
    assert pa > 0.6 > ph, (pa, ph)


def test_grammar_noise_filter():
    def m(rule, msg, *reps):
        return {"rule": {"id": rule}, "message": msg, "replacements": [{"value": r} for r in reps]}

    spell = "MORFOLOGIK_RULE_EN_GB"
    assert _noise(m(spell, "Possible spelling mistake found.", "Gas-powered"), "spelling", "GenAI-powered")
    assert _noise(m(spell, "Possible spelling mistake found.", "pg vector"), "spelling", "pgvector")
    assert _noise(m(spell, "'analyze' is American English.", "analyse"), "spelling", "analyze")
    assert _noise(m("WHITESPACE_RULE", "too many spaces", " "), "punctuation", "  ")
    assert not _noise(m(spell, "Possible spelling mistake found.", "receive"), "spelling", "recieve")
    assert not _noise(m(spell, "Did you mean there?", "there"), "spelling", "their")
    assert not _noise(m("UPPERCASE_SENTENCE_START", "x", "The"), "grammar", "the")


def test_text_hash_ignores_whitespace_but_not_words():
    assert text_hash("Hello   world\n\nfoo") == text_hash(" Hello world foo ")
    assert text_hash("Hello world foo") != text_hash("Hello world bar")


def test_ssrf_guard_blocks_private_and_odd_urls():
    for bad in ("http://127.0.0.1:8000/", "http://localhost/", "http://10.0.0.5/x", "http://192.168.1.1/", "http://169.254.169.254/latest/meta-data",
                "file:///etc/passwd", "ftp://example.com/", "http://[::1]/", "http://printer.local/", "not a url"):
        assert not is_public_url(bad), bad


def test_rate_limiter_window():
    rl = RateLimiter()
    assert [rl.check("k", 2, 60) for _ in range(2)] == [0, 0]
    assert rl.check("k", 2, 60) > 0
    assert rl.check("other", 2, 60) == 0


def test_sanitize_view_drops_unknown_and_dangerous_fields():
    out = sanitize_view({"title": "<img src=x>", "similarity": "150", "aiPct": "abc", "sources": [{"url": "data:text/html,x"}],
                         "rows": [{"text": "essay sentence", "ai": 5, "kind": "weird"}], "grammar": {"open": -3}})
    assert "<" not in out["title"] and out["similarity"] == 100 and out["aiPct"] is None and out["sources"] == []
    assert out["rows"] == [{"src": None, "ai": 1, "excluded": None, "kind": None}] and out["grammar"]["open"] == 0
