"""Central configuration. Everything is overridable through environment variables / .env."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = Path(os.getenv("BURSTAI_DATA_DIR", ROOT / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ---- AI detection models -------------------------------------------------
# Observer / performer pair for the Binoculars score. They MUST share a tokenizer.
# A model name is resolved to ./models/<name> if that folder exists (see scripts/download_models.sh),
# otherwise it is fetched from the Hugging Face hub on first use.
MODELS_DIR = ROOT / "models"


def _local_or_hub(name: str) -> str:
    local = MODELS_DIR / name.split("/")[-1]
    return str(local) if (local / "config.json").exists() else name


OBSERVER_MODEL = _local_or_hub(os.getenv("BURSTAI_OBSERVER_MODEL", "distilgpt2"))
PERFORMER_MODEL = _local_or_hub(os.getenv("BURSTAI_PERFORMER_MODEL", "gpt2"))
# Set to "0" to skip the performer model (perplexity-only mode, lighter on RAM).
USE_BINOCULARS = os.getenv("BURSTAI_USE_BINOCULARS", "1") != "0"
DEVICE = os.getenv("BURSTAI_DEVICE", "auto")  # auto | cpu | cuda
MAX_LM_TOKENS = int(os.getenv("BURSTAI_MAX_LM_TOKENS", "1024"))
# Long documents: score at most this many evenly spaced 1024-token sections (keeps requests bounded).
MAX_LM_CHUNKS = int(os.getenv("BURSTAI_MAX_LM_CHUNKS", "6"))
# Set to 1 to never load the language models (CI, tiny machines). AI detection then says so.
OFFLINE_LM = os.getenv("BURSTAI_OFFLINE_LM", "0") == "1"

# Sentence embedding model for paraphrase detection.
EMBED_MODEL = _local_or_hub(os.getenv("BURSTAI_EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2"))

# ---- Web search ----------------------------------------------------------
SEARCH_MAX_QUERIES = int(os.getenv("BURSTAI_SEARCH_MAX_QUERIES", "10"))
SEARCH_RESULTS_PER_QUERY = int(os.getenv("BURSTAI_SEARCH_RESULTS_PER_QUERY", "4"))
SEARCH_MAX_CANDIDATES = int(os.getenv("BURSTAI_SEARCH_MAX_CANDIDATES", "24"))
SEARCH_PHASE_TIMEOUT = float(os.getenv("BURSTAI_SEARCH_PHASE_TIMEOUT", "35"))
FETCH_TIMEOUT = float(os.getenv("BURSTAI_FETCH_TIMEOUT", "12"))
FETCH_CONCURRENCY = int(os.getenv("BURSTAI_FETCH_CONCURRENCY", "8"))
FETCH_MAX_BYTES = int(os.getenv("BURSTAI_FETCH_MAX_BYTES", str(3 * 1024 * 1024)))

# ---- Optional LLM judge (Gemini free tier) ------------------------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# ---- Grammar (LanguageTool public API: free, no key, rate limited) ------
LANGUAGETOOL_URL = os.getenv("LANGUAGETOOL_URL", "https://api.languagetool.org/v2/check")
LANGUAGETOOL_MAX_CHARS = 20000
# Which regional variant to assume when the language is auto-detected (en-GB suits India/UK; use en-US for American spelling).
LANGUAGETOOL_VARIANTS = os.getenv("LANGUAGETOOL_VARIANTS", "en-GB,de-DE,pt-BR,ca-ES")

# ---- Server --------------------------------------------------------------
# Public URL of your deployment (no trailing slash), used for canonical / Open Graph / sitemap links.
# Leave empty to derive it from each request.
PUBLIC_URL = os.getenv("BURSTAI_PUBLIC_URL", "").rstrip("/")
# When set, the heavy engine endpoints require header X-Engine-Token (used by the Cloudflare Worker proxy).
ENGINE_TOKEN = os.getenv("BURSTAI_ENGINE_TOKEN", "").strip()
HOST = os.getenv("BURSTAI_HOST", "127.0.0.1")
PORT = int(os.getenv("BURSTAI_PORT", "8000"))
MAX_INPUT_CHARS = int(os.getenv("BURSTAI_MAX_INPUT_CHARS", "200000"))
MAX_UPLOAD_BYTES = int(os.getenv("BURSTAI_MAX_UPLOAD_BYTES", str(15 * 1024 * 1024)))
# Each part of a scan (plagiarism / AI / grammar) must finish within this many seconds; a proxy such as
# Cloudflare cuts requests off at ~100 s, so we return partial results with a clear note instead.
TASK_TIMEOUT = float(os.getenv("BURSTAI_TASK_TIMEOUT", "80"))
# Fair-use limits per client IP (scans per hour / per minute).
RATE_SCANS_PER_HOUR = int(os.getenv("BURSTAI_RATE_SCANS_PER_HOUR", "30"))
RATE_SCANS_PER_MIN = int(os.getenv("BURSTAI_RATE_SCANS_PER_MIN", "6"))
# Where the public site lives (the Worker). scripts/start_engine.ps1 registers the tunnel URL there.
SITE_URL = os.getenv("BURSTAI_SITE_URL", "").rstrip("/")
