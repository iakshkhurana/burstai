"""A tiny local corpus (SQLite) so a teacher / institution can compare submissions against
each other and against their own document bank - the part of Turnitin that is its real moat."""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass

from .. import config
from ..fingerprint import Fingerprint, winnow
from ..textproc import tokenize_words

_DB = config.DATA_DIR / "corpus.sqlite3"


@dataclass
class CorpusDoc:
    id: int
    title: str
    added: float
    n_words: int


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_DB)
    c.execute(
        "CREATE TABLE IF NOT EXISTS docs (id INTEGER PRIMARY KEY, title TEXT, text TEXT, "
        "hashes TEXT, n_words INTEGER, added REAL)"
    )
    return c


def add(title: str, text: str) -> CorpusDoc:
    words = tokenize_words(text)
    fp = winnow(words)
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO docs (title, text, hashes, n_words, added) VALUES (?,?,?,?,?)",
            (title, text, json.dumps(list(fp.hashes)), len(words), time.time()),
        )
        return CorpusDoc(cur.lastrowid, title, time.time(), len(words))


def list_docs() -> list[CorpusDoc]:
    with _conn() as c:
        rows = c.execute("SELECT id, title, added, n_words FROM docs ORDER BY added DESC").fetchall()
    return [CorpusDoc(*r) for r in rows]


def delete(doc_id: int) -> bool:
    with _conn() as c:
        return c.execute("DELETE FROM docs WHERE id=?", (doc_id,)).rowcount > 0


def candidates(doc_fp: Fingerprint, min_overlap: float = 0.02) -> list[tuple[str, str, str, float]]:
    """(url-ish id, title, text, overlap) for corpus docs sharing fingerprints with the submission."""
    want = doc_fp.hash_set
    if not want:
        return []
    out = []
    with _conn() as c:
        for doc_id, title, text, hashes in c.execute("SELECT id, title, text, hashes FROM docs"):
            hs = set(json.loads(hashes))
            ov = len(want & hs) / len(want)
            if ov >= min_overlap:
                out.append((f"corpus://{doc_id}", title, text, ov))
    out.sort(key=lambda t: -t[3])
    return out[:10]
