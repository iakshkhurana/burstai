# burstai

**Free, open-source plagiarism + AI-content + grammar checker that runs on your own machine.**
No account, no word cap, no credits, no upload to a vendor. The goal is simple: make what
Turnitin, Quetext, Copyscape and Scribbr charge for available to every student and teacher for free.

```
run.bat            # Windows: creates .venv, installs deps, starts http://127.0.0.1:8000
# or
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
.venv/Scripts/python -m uvicorn app.main:app
```

First start downloads two small language models (gpt2 ~500 MB, gpt2-medium ~1.5 GB) and a
sentence-embedding model (~90 MB) from Hugging Face. After that everything except web search
and grammar works offline.

---

## How the commercial tools actually work (and what we copied)

### Plagiarism checkers: Turnitin, Copyscape, Quetext, PlagScan, Unicheck, Scribbr

There is **very little machine learning** in classic plagiarism detection. The pipeline is:

1. **Normalise** the text (lower-case, strip punctuation).
2. **Fingerprint** it: hash every k-gram of words and keep a sparse, position-aware subset
   using *winnowing* (Schleimer, Wilkerson & Aiken 2003 - the MOSS algorithm). Any shared run
   of roughly 8+ words between two documents is guaranteed to share a fingerprint.
3. **Look the fingerprints up in a gigantic index**: the open web crawl, publisher databases
   (Elsevier, Springer...), and, for Turnitin, every student paper ever submitted to it.
4. **Report** the matched spans with a *similarity score* = matched words / total words.

Their real moat is step 3, the private database, not the algorithm. Scribbr and Quetext are
literally resellers of Turnitin's index. Newer "paraphrase detection" layers add sentence
embeddings to catch rewrites, which is the one place ML appears.

**burstai does the same four steps**, with the index replaced by live, free retrieval:
distinctive exact-phrase queries to DuckDuckGo and the Wikipedia API, page download and
boilerplate removal, then winnowing + fuzzy matching + MiniLM sentence embeddings for
paraphrase. You can also build your own **local corpus** (previous submissions, course readings)
in SQLite, which is exactly what institutions pay Turnitin for.

### AI-content detectors: Turnitin AI, GPTZero, Originality.ai, Copyleaks

These **are** ML, and they work on one insight: LLM text is *statistically predictable*.

| Method | Used by | Idea |
|---|---|---|
| Perplexity + burstiness | GPTZero (original) | A language model is rarely surprised by LLM text; humans vary sentence-to-sentence |
| Fine-tuned classifier | Turnitin, Originality, Copyleaks | RoBERTa-style model trained on millions of human/AI pairs; opaque, confident, decays as models change |
| DetectGPT / Fast-DetectGPT | research | Perturb the text; AI text sits at a probability *peak*, human text does not |
| **Binoculars** (Hans et al., ICML 2024) | research, SOTA zero-shot | Perplexity divided by *cross*-perplexity between two related models; cancels the "simple topic" confound |
| Watermarking (SynthID) | Google only | Needs the generator's cooperation; nothing else can read it |

**burstai implements an ensemble** of Binoculars (gpt2 observer / gpt2-medium performer),
raw perplexity, sentence-level burstiness, and ~25 explainable stylometric features (LLM-phrase
density such as *delve / tapestry / testament to*, sentence-length uniformity, missing
contractions, transitional openers...). If you add a free Gemini key it also asks Gemini for a
bounded third opinion. Every sentence is coloured so you can see *where* the signal comes from,
and the report lists the reasons in plain English.

### Grammar: Grammarly

Grammarly is a rule engine plus proprietary ML. burstai calls the free public
[LanguageTool](https://languagetool.org) API (open-source rule engine, 30+ languages, no key),
or your own self-hosted LanguageTool for unlimited use.

---

## Honest accuracy statement

> **No detector is 100% accurate. Not Turnitin, not GPTZero, not this one.**

Turnitin itself claims ~1% false positives per document at the 20% threshold and independent
studies have found materially worse, especially for non-native English writers and for text
passed through a "humanizer". Any tool advertising 100% is lying to you.

What burstai does differently is *honesty and transparency*: it shows the raw numbers, explains
every signal, reports a confidence level, refuses to be confident on texts under ~120 words, and
never hides a sentence-level view behind a single scary percentage.

Run `.venv/Scripts/python -m tests.calibrate` to see the per-sample statistics on the bundled
`samples/` and re-tune the constants at the top of `app/aidetect/engine.py` for your own data.
Add your own human/AI examples to `samples/` (name human ones `*human*`) to improve calibration.

## Robustness against "humanizers"

Tools like *Turnitout-Humanizer* evade detectors by (a) swapping synonyms to break n-gram
matches, (b) reshaping clauses, (c) stripping zero-width characters and metadata. burstai is
designed with those in mind:

* paraphrase layer uses **sentence embeddings**, which survive synonym swaps;
* fuzzy token-set matching tolerates reordering and insertions;
* the AI ensemble leans on **Binoculars**, which is harder to game than perplexity alone;
* stylometry flags the *result* of synonym-swapping (unnatural word choice raises perplexity
  unevenly, visible as patchy sentence scores).

It is still an arms race. Expect determined adversaries to win sometimes.

---

## The app

Three pages, implemented from the BurstAI Claude Design canvas:

| Route | What it does |
|---|---|
| `/` | Landing page: paste or drop a .docx/.pdf/.txt, pick Plagiarism / AI detector / Grammar, start a scan. Shows your local scan history. |
| `/check` | Checker workspace: the essay with per-sentence highlights, three layers (Plagiarism / AI writing / Grammar), source list that isolates matches, exclude quotes / bibliography toggles, grammar fixes you can accept or dismiss (the text updates live), local corpus management. |
| `/report` | Originality report: score cards, plain-English summary, sentence-by-sentence table, sources with an APA/MLA/Harvard/IEEE citation generator, printable PDF, and a certificate with a verification link (`/v/<id>`) served by your own instance. |

| `/login`, `/signup` | Free accounts (SQLite locally, D1 on Cloudflare): PBKDF2-hashed passwords, HttpOnly session cookies. Optional: everything works logged out. |
| `/me` | "My writing": streak, originality trend and every saved scan (synced across devices when logged in, kept on-device otherwise). |

All pages are responsive: the phone layouts follow the BurstAI mobile canvas (bottom navigation,
document + results bottom sheet on the checker, fanned score deck and flagged-sentence cards on the report).

Try it instantly with the bundled sample essay: `/check?sample=1`.

**Deploying** (including Cloudflare Workers + D1 for the site and accounts, with the Python engine on
any machine behind a free Cloudflare Tunnel): see [DEPLOY.md](DEPLOY.md).

## Architecture

```
app/
  main.py               FastAPI: /api/check /api/extract /api/corpus /api/health /api/auth/* /api/scans /api/reports, serves static/
  auth.py               accounts, sessions, saved scans (SQLite)
  reports.py            public verification copies of reports (/v/<id>)
  cache.py              search-result + page cache (SQLite, TTL)
  textproc.py           sentence splitting with offsets, quote / citation / reference detection
  fingerprint.py        winnowing fingerprints
  extract.py            docx / pdf / html / txt -> text
  grammar.py            LanguageTool client
  plagiarism/
    search.py           distinctive phrase queries -> DuckDuckGo + Wikipedia candidates
    fetch.py            async download + trafilatura boilerplate removal
    matcher.py          winnowing + rapidfuzz + MiniLM paraphrase matching
    corpus.py           local SQLite document bank
    engine.py           orchestration, scoring, per-sentence report
  aidetect/
    lm.py               GPT-2 pair: per-token log-probs, perplexity, Binoculars
    stylometry.py       explainable features + heuristic score
    llm_judge.py        optional Gemini judge
    engine.py           ensemble + sentence colouring + confidence
static/                 the site (no build step): index / check / report / auth / me + css/js
cloudflare/             Worker (static assets + auth on D1 + engine proxy), wrangler.toml, schema.sql
scripts/                download_models.sh (resumable), make_release.py (zip + CHANGELOG)
samples/                calibration texts
tests/                  pytest unit tests, calibrate.py, shot.mjs (headless screenshot driver)
```

## Configuration

Copy `.env.example` to `.env`. Notable knobs:

* `GEMINI_API_KEY` - optional judge (free tier at aistudio.google.com).
* `BURSTAI_OBSERVER_MODEL` / `BURSTAI_PERFORMER_MODEL` - swap in a stronger pair
  (e.g. Qwen2.5-1.5B / -Instruct) if you have the VRAM; re-run calibration afterwards.
* `BURSTAI_USE_BINOCULARS=0` - perplexity-only mode on weak machines.
* `LANGUAGETOOL_URL` - point at a self-hosted LanguageTool for unlimited grammar checks.

## API

```
POST /api/check     {"text": "...", "options": {"plagiarism": true, "ai": true, "grammar": true,
                     "exclude_quotes": true, "exclude_references": true, "deep_search": false}}
POST /api/extract   multipart file -> {"text": ...}
GET  /api/corpus    | POST /api/corpus (multipart file) | DELETE /api/corpus/{id}
GET  /api/health
```

## Roadmap

* Semantic Scholar / arXiv / CrossRef retrieval for academic sources
* Cross-language plagiarism (translate-then-match) via multilingual MiniLM
* Trainable classifier head on top of the signals once a labelled dataset is collected
* Browser extension and LMS (Moodle / Google Classroom) plug-in
* Docker image bundling LanguageTool

## License

MIT. Use it, fork it, host it for your school.
