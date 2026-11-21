# Deploying BurstAI

BurstAI has two halves:

| Half | What it does | Where it can run |
|---|---|---|
| **Site + accounts** (`static/`, auth, saved scans, report verification) | Everything the browser talks to except the heavy checks | Cloudflare Workers (free) **or** the Python server |
| **Engine** (`app/`: search, fingerprinting, language models, grammar) | `/api/check`, `/api/extract`, `/api/corpus` | Any always-on machine with Python 3.12+ and ~2 GB RAM |

Cloudflare Workers cannot run PyTorch, so the engine always lives on a real machine. The Worker
proxies `/api/check` and friends to it. Your own laptop behind a free Cloudflare Tunnel works.

---

## Option A - simplest: one Python server does everything

```
cp .env.example .env            # optional: set BURSTAI_PUBLIC_URL=https://your.domain
bash scripts/download_models.sh  # resumable model download (~900 MB)
run.bat                          # or: .venv/Scripts/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Put it behind any HTTPS reverse proxy (Caddy / nginx / Cloudflare Tunnel). Accounts, sessions,
saved scans and reports are stored in `data/*.sqlite3`. Back up the `data/` folder.

## Option B - Cloudflare Workers for the site, Python engine elsewhere

### 1. Run the engine somewhere reachable over HTTPS

On the engine machine:

```
cp .env.example .env
# lock the engine to the Worker:
echo BURSTAI_ENGINE_TOKEN=some-long-random-string >> .env
.venv/Scripts/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Expose it:

* **Cloudflare Tunnel (free, no port-forwarding)**: `cloudflared tunnel --url http://localhost:8000`
  prints a `https://....trycloudflare.com` URL (or create a named tunnel on your own domain).
* Or a small VM (Oracle free tier, Fly.io, Render, any VPS) with Caddy in front.

### 2. Deploy the Worker

```
cd cloudflare
npm i -g wrangler && wrangler login
wrangler d1 create burstai                      # copy database_id into wrangler.toml
wrangler d1 execute burstai --remote --file=schema.sql
# edit wrangler.toml: PUBLIC_URL and ENGINE_URL
wrangler secret put ENGINE_TOKEN                # same value as BURSTAI_ENGINE_TOKEN on the engine
wrangler deploy
```

That's it. The Worker serves `static/` at the edge, handles signup/login/sessions/saved scans
and report verification on D1, and forwards the heavy checks to your engine with the shared token.

### 3. Custom domain (optional)

In the Cloudflare dashboard: Workers & Pages → burstai → Settings → Domains & Routes → add your
domain. Update `PUBLIC_URL` in `wrangler.toml` and redeploy so canonical/OG/sitemap links match.

---

## Environment variables (engine)

| Variable | Default | Purpose |
|---|---|---|
| `BURSTAI_PUBLIC_URL` | derived from request | canonical / OG / sitemap links when the Python server serves the site |
| `BURSTAI_ENGINE_TOKEN` | empty (open) | when set, `/api/check`, `/api/extract`, `/api/corpus*` require header `X-Engine-Token` |
| `BURSTAI_OBSERVER_MODEL` / `BURSTAI_PERFORMER_MODEL` | distilgpt2 / gpt2 | Binoculars model pair (local `./models/<name>` is used when present) |
| `BURSTAI_EMBED_MODEL` | all-MiniLM-L6-v2 | paraphrase embeddings |
| `GEMINI_API_KEY` | empty | optional third-opinion judge; you add your own key, nothing else is ever required |
| `LANGUAGETOOL_URL` | public API | point at a self-hosted LanguageTool for unlimited grammar checks |
| `BURSTAI_ADMIN_EMAIL` / `BURSTAI_ADMIN_PASSWORD` | empty | create a first account on start |

## Releases

`python scripts/make_release.py` writes `dist/burstai-<version>-<date>.zip` containing the source,
static site, Worker and docs (no venv, models, data or secrets) plus a fresh `CHANGELOG.md` entry.
