# BurstAI

**Free, open-source plagiarism checker, AI-writing estimator and grammar checker that shows its work.**
Every flag is linked to the source it matched or the reason it was raised, so a person makes the
final call instead of a number.

Live: <https://burstai.online> · Licence: MIT · [Privacy](static/privacy.html) · [Responsible use](static/terms.html) · [Security](SECURITY.md)

```
run.bat                                   # Windows: creates .venv, installs deps, starts http://127.0.0.1:8000
# or
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
bash scripts/download_models.sh           # resumable, ~1 GB (distilgpt2, gpt2, MiniLM)
.venv/Scripts/python -m uvicorn app.main:app
```

Try it with the bundled sample: `/check?sample=1`.

---

## Honest status

Read this before relying on it.

| Area | What works | Limits you must know |
|---|---|---|
| **Plagiarism** | Winnowing fingerprints + fuzzy + embedding (paraphrase) matching against pages found by live search, Wikipedia, Europe PMC / Crossref abstracts, and your own private document bank. Shows the matched source text per sentence. | Recall depends on **free, key-less web search**, which is rate-limited and sometimes returns nothing. When that happens the UI and the signed report say so explicitly. It cannot see paywalled content or Turnitin's private archive of student papers. A low score is not proof of originality. |
| **AI-writing likelihood** | Binoculars (distilgpt2 / gpt2) + perplexity + stylometry, optional Gemini second opinion with the user's own key. Sentence-level view, confidence label, reasons. | **English only** (other languages get no score rather than a wrong one). Calibrated on **10 bundled samples**, so treat it as experimental. Unreliable on short texts; can unfairly flag non-native writers; can be evaded. Never use as sole evidence. |
| **Grammar** | LanguageTool with a noise filter for names / tech terms / variant nags; optional context review by Gemini (user's key). | The public LanguageTool endpoint is rate-limited and meant for light use: self-host it for real traffic (`LANGUAGETOOL_URL`). |
| **Reports** | Engine-signed results (HMAC). `/v/<id>` shows only signed values; a teacher can paste a document to confirm it is the exact text that was scanned. | A signed report proves the scores were not edited. It does not prove authorship and is not a certificate of originality. |
| **Accounts** | Email + password, saved scan scores, private document bank, delete-my-account. | No email verification and no password reset yet. |

No detector, commercial or open, is 100% accurate. Anyone who tells you otherwise is selling something.

## What is stored

Nothing of the essay. Scans are processed in memory. Saved scans keep scores, source links and
per-sentence flags without text. Signed reports keep scores, source links and a SHA-256 fingerprint.
Logs and caches never contain essay text (cache keys are hashes). See the in-app privacy notice for
exactly which third parties receive what during a scan.

## How it works

### Plagiarism (what Turnitin-class tools do, minus the private index)

1. Split into sentences (Unicode-aware; any space-separated script).
2. Fingerprint with **winnowing** (Schleimer, Wilkerson & Aiken 2003): any shared run of about 8+ words
   between two documents shares a fingerprint.
3. Retrieve candidates: exact-phrase queries for the most distinctive sentences via `ddgs`
   (key-less metasearch), keyword queries to Wikipedia, Europe PMC and Crossref, plus the caller's
   private document bank.
4. Match sentence by sentence: fingerprints (verbatim), `rapidfuzz` token-set ratio (light edits),
   MiniLM embeddings (paraphrase).
5. Report similarity = matched words / counted words, with quotes, citations and the reference
   list optionally excluded, and with **coverage** (how many pages of each kind were compared).

### AI-writing likelihood

Ensemble of the Binoculars score (Hans et al., ICML 2024: perplexity divided by cross-perplexity of
two related models), raw perplexity, ~25 stylometric features, and optionally Gemini. Calibration
constants live at the top of `app/aidetect/engine.py`; re-measure with
`python -m tests.calibrate` after adding your own labelled samples to `samples/`
(name human ones `*human*`). The "Likely AI-generated" label needs p ≥ 0.70 and non-low confidence.

### Signed reports

`/api/check` returns an attestation: canonical JSON of the scores, sources, coverage, timestamp and
`sha256(normalised text)`, with an HMAC-SHA256 signature. The report id is derived from the
signature. `/api/reports` stores a copy only if the signature verifies, and never overwrites.

## Architecture

```
app/
  main.py               FastAPI: engine endpoints, accounts, signed reports, pages, security headers
  security.py           rate limiter, attestations (HMAC), SSRF guard, CSP / headers
  textproc.py           Unicode sentence splitting with offsets; quote / citation / reference detection; English gate
  fingerprint.py        winnowing
  extract.py            docx / pdf / html / txt -> text
  grammar.py            LanguageTool client + noise filter + optional Gemini review
  llm.py                optional Gemini client (user-supplied key; failure-safe)
  auth.py               accounts, sessions, saved scans (SQLite)
  reports.py            signed verification copies; allow-list sanitiser for saved views
  cache.py              hashed-key cache for search results and fetched public pages
  plagiarism/           search.py · fetch.py (SSRF-guarded) · matcher.py · corpus.py (per-owner) · engine.py
  aidetect/             lm.py (perplexity, Binoculars) · stylometry.py · llm_judge.py · engine.py
static/                 the site: index / check / report / auth / me / privacy / terms / 404, css, js, self-hosted fonts
cloudflare/             Worker: site + accounts + signed reports on D1, proxy to the engine
scripts/                download_models.sh · fetch_fonts.py · start_engine.ps1 · make_release.py
tests/                  test_core.py · test_api.py · calibrate.py · shot.mjs (headless screenshots)
```

## Tests

```
pip install -r requirements-ci.txt
python -m pytest tests/test_core.py tests/test_api.py -q      # no models, no network needed
```

They cover fingerprinting, Unicode handling, the English gate, the grammar noise filter, the SSRF
guard, rate limits, security headers/CSP, signed-report round trips and forgery rejection, report
sanitising, account flows, and per-owner isolation of scans and document banks. CI runs them on
every push (`.github/workflows/ci.yml`).

## Configuration

Copy `.env.example` to `.env`. Everything is optional; see `DEPLOY.md` for the Cloudflare setup.

## Production checklist (things code cannot do for you)

1. Run the engine on an always-on machine with a **named** Cloudflare Tunnel or a VM, not a laptop.
2. Self-host LanguageTool, and use a search API you have an agreement with if traffic grows
   (see `THIRD_PARTY.md`).
3. Build a labelled evaluation set from your own users' domain and re-calibrate the AI thresholds;
   publish the measured false-positive rate.
4. Have the privacy notice and terms reviewed for your jurisdiction, and add a real contact address.
5. Add email verification and password reset (needs a mail provider).
6. Back up the D1 database; enable Dependabot.

## Licence

MIT (see `LICENSE`). Third-party models, fonts and services are listed in `THIRD_PARTY.md`.
