"""Language-model signals for AI-text detection.

Two zero-shot statistics computed with a pair of small open causal LMs that share a
tokenizer (default: distilgpt2 as *observer*, gpt2 as *performer*):

* **Perplexity** - how "surprised" the model is by each token. LLM output is
  low-perplexity by construction (it was sampled from a similar distribution).
  This is the classic GPTZero signal, but it alone misclassifies memorised famous
  text and non-native / formulaic writing.
* **Binoculars score** (Hans et al., ICML 2024) - perplexity divided by the
  *cross*-perplexity between the two models. The cross-perplexity measures how
  surprising the text is *expected* to be given the prompt-like context, which
  normalises away the "simple topic / common phrasing" confound. Lower score => more likely AI.

Both models are English-only: callers must not interpret scores for other languages.
Everything is lazily loaded so the API starts instantly.
"""
from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass, field

import numpy as np

from .. import config

log = logging.getLogger(__name__)


@dataclass
class TokenStats:
    offsets: list[tuple[int, int]]          # char span of each *scored* token
    logprob: np.ndarray                     # observer log p(token)       (nats)
    xent: np.ndarray                        # cross-entropy observer||performer per token
    perf_logprob: np.ndarray                # performer log p(token)
    binoculars: float | None = None
    log_ppl: float = 0.0                    # observer mean NLL
    chunks: int = 1                         # sections actually scored
    chunks_total: int = 1                   # sections in the document
    model_info: dict = field(default_factory=dict)

    @property
    def ppl(self) -> float:
        return math.exp(self.log_ppl)


class LMScorer:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._score_lock = threading.Lock()   # one scoring pass at a time keeps a small machine responsive
        self._loaded = False
        self.tok = None
        self.observer = None
        self.performer = None
        self.device = "cpu"
        self.error: str | None = None

    # ------------------------------------------------------------------ loading
    def load(self) -> bool:
        if self._loaded:
            return self.error is None
        with self._lock:
            if self._loaded:
                return self.error is None
            if config.OFFLINE_LM:
                self.error = "language models disabled (BURSTAI_OFFLINE_LM=1)"
                self._loaded = True
                return False
            try:
                import torch
                from transformers import AutoModelForCausalLM, AutoTokenizer

                self.device = _pick_device()
                dtype = torch.float16 if self.device == "cuda" else torch.float32
                self.tok = AutoTokenizer.from_pretrained(config.OBSERVER_MODEL)
                self.observer = AutoModelForCausalLM.from_pretrained(config.OBSERVER_MODEL, dtype=dtype).to(self.device).eval()
                if config.USE_BINOCULARS:
                    self.performer = (
                        AutoModelForCausalLM.from_pretrained(config.PERFORMER_MODEL, dtype=dtype).to(self.device).eval()
                    )
                log.info("LM scorer ready on %s", self.device)
            except Exception as e:  # noqa: BLE001
                self.error = f"{type(e).__name__}: {str(e)[:200]}"
                log.exception("failed to load language models")
            finally:
                self._loaded = True
        return self.error is None

    def info(self) -> dict:
        name = lambda p: str(p).replace("\\", "/").rstrip("/").split("/")[-1]  # noqa: E731 - never expose local paths
        return {
            "loaded": self._loaded and self.error is None,
            "error": self.error,
            "device": self.device,
            "observer": name(config.OBSERVER_MODEL),
            "performer": name(config.PERFORMER_MODEL) if config.USE_BINOCULARS else None,
        }

    # ------------------------------------------------------------------ scoring
    def score(self, text: str) -> TokenStats | None:
        if not self.load():
            return None
        import torch

        enc = self.tok(text, return_offsets_mapping=True, add_special_tokens=False)
        ids: list[int] = enc["input_ids"]
        offs: list[tuple[int, int]] = [tuple(o) for o in enc["offset_mapping"]]
        if len(ids) < 2:
            return None

        max_len = config.MAX_LM_TOKENS - 1  # room for the BOS token we prepend
        bos = self.tok.bos_token_id if self.tok.bos_token_id is not None else self.tok.eos_token_id

        starts = list(range(0, len(ids), max_len))
        total = len(starts)
        if total > config.MAX_LM_CHUNKS:
            # Long document: score evenly spaced sections so the request finishes in bounded time.
            step = (total - 1) / (config.MAX_LM_CHUNKS - 1)
            starts = [starts[round(i * step)] for i in range(config.MAX_LM_CHUNKS)]

        lp_all, xe_all, plp_all, off_all = [], [], [], []
        with self._score_lock:
            for start in starts:
                piece = ids[start : start + max_len]
                # Give later chunks some context so the first tokens aren't unfairly surprising.
                ctx = ids[max(0, start - 64) : start] if start else []
                inp = torch.tensor([[bos, *ctx, *piece]], device=self.device)
                with torch.inference_mode():
                    o_logits = self.observer(inp).logits[0, :-1].float()
                    targets = inp[0, 1:]
                    o_logp = torch.log_softmax(o_logits, dim=-1)
                    lp = o_logp.gather(1, targets[:, None])[:, 0]
                    if self.performer is not None:
                        p_logits = self.performer(inp).logits[0, :-1].float()
                        p_logp = torch.log_softmax(p_logits, dim=-1)
                        plp = p_logp.gather(1, targets[:, None])[:, 0]
                        # X-entropy: expectation under observer of performer's NLL (Binoculars eq. 3)
                        xe = -(torch.softmax(o_logits, dim=-1) * p_logp).sum(-1)
                    else:
                        plp = lp
                        xe = torch.full_like(lp, float("nan"))
                n_ctx = len(ctx)
                lp_all.append(lp[n_ctx:].cpu().numpy())
                xe_all.append(xe[n_ctx:].cpu().numpy())
                plp_all.append(plp[n_ctx:].cpu().numpy())
                off_all.extend(offs[start : start + len(piece)])

        lp_arr = np.concatenate(lp_all)
        xe_arr = np.concatenate(xe_all)
        plp_arr = np.concatenate(plp_all)
        stats = TokenStats(offsets=off_all, logprob=lp_arr, xent=xe_arr, perf_logprob=plp_arr, chunks=len(starts), chunks_total=total)
        stats.log_ppl = float(-lp_arr.mean())
        if self.performer is not None:
            # paper: log-PPL of performer / X-PPL(observer, performer)
            stats.binoculars = float((-plp_arr.mean()) / max(1e-6, float(np.nanmean(xe_arr))))
        stats.model_info = self.info()
        return stats


def _pick_device() -> str:
    if config.DEVICE != "auto":
        return config.DEVICE
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
    except Exception:  # noqa: BLE001
        pass
    return "cpu"


def span_stats(stats: TokenStats, start: int, end: int) -> dict | None:
    """Aggregate token statistics for a character span (e.g. one sentence)."""
    idx = [i for i, (a, b) in enumerate(stats.offsets) if a < end and b > start and i < len(stats.logprob)]
    if len(idx) < 3:
        return None
    lp = stats.logprob[idx]
    plp = stats.perf_logprob[idx]
    xe = stats.xent[idx]
    out = {"tokens": len(idx), "log_ppl": float(-lp.mean())}
    if not np.isnan(xe).all():
        out["binoculars"] = float((-plp.mean()) / max(1e-6, float(np.nanmean(xe))))
    return out


SCORER = LMScorer()
