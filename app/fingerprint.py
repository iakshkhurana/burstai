"""Document fingerprinting using the Winnowing algorithm (Schleimer, Wilkerson & Aiken 2003).

This is the same family of technique used by MOSS and, in spirit, by Turnitin:
  1. normalise the text to a word stream,
  2. hash every k-gram of words,
  3. slide a window of w hashes and keep the minimum of each window.
The surviving hashes are a compact, position-aware fingerprint. Two documents share
fingerprints wherever they share any run of at least k + w - 1 words, which is
robust to insertions and reorderings of unrelated material.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass

from .textproc import tokenize_words

K_DEFAULT = 5   # words per k-gram
W_DEFAULT = 4   # window size; guarantees detection of any shared run >= k + w - 1 = 8 words


def _h(words: list[str]) -> int:
    return int.from_bytes(hashlib.blake2b(" ".join(words).encode(), digest_size=8).digest(), "big")


@dataclass
class Fingerprint:
    hashes: dict[int, list[int]]   # hash -> word positions where it occurs
    n_words: int

    @property
    def hash_set(self) -> set[int]:
        return set(self.hashes)


def kgram_hashes(words: list[str], k: int = K_DEFAULT) -> list[int]:
    if not words:
        return []
    if len(words) < k:
        return [_h(words)]
    return [_h(words[i : i + k]) for i in range(len(words) - k + 1)]


def winnow(words: list[str], k: int = K_DEFAULT, w: int = W_DEFAULT) -> Fingerprint:
    hashes = kgram_hashes(words, k)
    picked: dict[int, list[int]] = defaultdict(list)
    if not hashes:
        return Fingerprint(picked, len(words))
    if len(hashes) <= w:
        m = min(range(len(hashes)), key=hashes.__getitem__)
        picked[hashes[m]].append(m)
        return Fingerprint(picked, len(words))
    last = -1
    for i in range(len(hashes) - w + 1):
        window = hashes[i : i + w]
        # rightmost minimum, as in the paper
        m = i + max(range(w), key=lambda j: (-window[j], j))
        if m != last:
            picked[hashes[m]].append(m)
            last = m
    return Fingerprint(picked, len(words))


def fingerprint_text(text: str, k: int = K_DEFAULT, w: int = W_DEFAULT) -> Fingerprint:
    return winnow(tokenize_words(text), k, w)


def overlap(a: Fingerprint, b: Fingerprint) -> float:
    """Fraction of *a*'s fingerprints that also appear in *b* (0..1)."""
    if not a.hashes:
        return 0.0
    shared = a.hash_set & b.hash_set
    return len(shared) / len(a.hashes)


def shared_positions(a: Fingerprint, b: Fingerprint) -> list[int]:
    """Word positions in *a* covered by fingerprints that also occur in *b*."""
    pos: list[int] = []
    for h in a.hash_set & b.hash_set:
        pos.extend(a.hashes[h])
    return sorted(set(pos))


def ngrams(words: list[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(words[i : i + n]) for i in range(max(0, len(words) - n + 1))}
