# Deploying BurstAI

BurstAI has two halves:

| Half | What it does | Where it runs |
|---|---|---|
| **Site + accounts** (`static/`, auth, saved scans, signed reports) | Everything the browser talks to except the heavy checks | Cloudflare Workers + D1 (free tier) **or** the Python server |
| **Engine** (`app/`: search, fingerprinting, language models, grammar) | `/api/check`, `/api/extract`, `/api/corpus`, `/api/gemini` | Any always-on machine with Python 3.12+ and about 2 GB RAM |

Cloudflare Workers cannot run PyTorch, so the engine always lives on a real machine. The Worker
proxies the engine endpoints to it and adds a shared secret; the engine refuses anything without it.

---

## Option A: one Python server does everything

```
cp .env.example .env             # leave BURSTAI_ENGINE_TOKEN empty in this mode
bash scripts/download_models.sh   # resumable model download (~1 GB)
run.bat                           # or: .venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Put it behind an HTTPS reverse proxy (Caddy, nginx, a tunnel) and set `BURSTAI_PUBLIC_URL`.
Accounts, sessions, saved scans and signed reports live in `data/`. Back that folder up.

## Option B: Cloudflare Workers for the site, Python engine elsewhere

### 1. Deploy the Worker (once)

Cloudflare dashboard → Workers & Pages → Create → Import a repository:

| Field | Value |
|---|---|
| Root directory | `cloudflare` |
| Build command | *(empty)* |
| Deploy command | `npx wrangler deploy` |
| Non-production branch deploy command | `npx wrangler versions upload` |

Then:

1. Storage & Databases → D1 → create a database named `burstai`; put its id in `cloudflare/wrangler.toml`.
   Tables are created automatically on first use (`schema.sql` is kept for reference).
2. Worker → Settings → Variables and Secrets → add a **Secret** named `ENGINE_TOKEN` with a long random
   value. `keep_vars = true` in `wrangler.toml` makes dashboard values survive git deploys.
3. Set `PUBLIC_URL` in `wrangler.toml` to your final address and attach the custom domain under
   Settings → Domains & Routes. Other hostnames then redirect to it.

### 2. Run the engine and connect it

On the engine machine, in `.env`:

```
BURSTAI_ENGINE_TOKEN=<the same value as the Worker secret>
BURSTAI_SITE_URL=https://your-site.example
```

Then:

```
bash scripts/download_models.sh
powershell -ExecutionPolicy Bypass -File scripts\start_engine.ps1
```

The script starts the engine on `127.0.0.1:8000`, opens a Cloudflare quick tunnel, and **registers
the tunnel's address with the site** (`POST /api/engine/register`, authenticated with the token).
The address can change on every restart without redeploying anything. Re-run the script after a
reboot, or schedule it at log-on.

For real production use, replace the quick tunnel with a **named tunnel** (Zero Trust → Networks →
Tunnels) or run the engine on a small VM, set `ENGINE_URL` in `wrangler.toml` to its fixed address,
and keep the token.

### What the Worker does

* serves `static/` with a strict CSP and security headers; redirects other hostnames to `PUBLIC_URL`;
* accounts, sessions, saved scans (allow-listed, no essay text) and signed report copies in D1;
* verifies the engine's HMAC signature before storing a report (`ENGINE_TOKEN` is the key);
* rate-limits scans, uploads, logins, sign-ups and report creation per hashed IP;
* forwards engine calls with the token, the caller's IP (for the engine's own limits) and, when
  logged in, the user id (for the private document bank).

---

## Environment variables (engine)

| Variable | Default | Purpose |
|---|---|---|
| `BURSTAI_ENGINE_TOKEN` | empty | engine-only mode: required header `X-Engine-Token`; also the HMAC key for signed results. Rotating it invalidates existing signed reports. |
| `BURSTAI_SITE_URL` | empty | where `start_engine.ps1` registers the tunnel URL |
| `BURSTAI_PUBLIC_URL` | derived | canonical / OG / sitemap links when the Python server serves the site |
| `BURSTAI_OBSERVER_MODEL` / `BURSTAI_PERFORMER_MODEL` | distilgpt2 / gpt2 | Binoculars pair (`./models/<name>` is used when present). Re-calibrate after changing. |
| `BURSTAI_USE_BINOCULARS` | 1 | 0 = perplexity only (lighter) |
| `BURSTAI_OFFLINE_LM` | 0 | 1 = never load language models (CI / tiny machines) |
| `BURSTAI_MAX_INPUT_CHARS` | 200000 | per-scan size cap |
| `BURSTAI_TASK_TIMEOUT` | 80 | seconds per part of a scan; Cloudflare cuts requests at about 100 s |
| `BURSTAI_RATE_SCANS_PER_HOUR` / `_PER_MIN` | 30 / 6 | fair-use limits per visitor |
| `LANGUAGETOOL_URL` | public API | point at a self-hosted LanguageTool for real traffic |
| `GEMINI_API_KEY` | empty | optional server-wide key; normally each user adds their own in the browser |
| `BURSTAI_ADMIN_EMAIL` / `BURSTAI_ADMIN_PASSWORD` | empty | create a first account on start (stand-alone mode) |

## Releases

`python scripts/make_release.py "note"` writes `dist/burstai-<version>-<date>.zip` (source, site,
Worker, docs; no venv, models, data or secrets) and prepends a `CHANGELOG.md` entry.
