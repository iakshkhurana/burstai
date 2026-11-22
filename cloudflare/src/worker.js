/**
 * BurstAI on Cloudflare Workers.
 *
 *  - Serves ./static as the site (assets binding), injecting PUBLIC_URL into the pages.
 *  - Accounts, sessions, saved scans and verification copies of reports live in D1.
 *  - Proxies the heavy endpoints (/api/check, /api/extract, /api/corpus*, /api/gemini*, /api/health)
 *    to the Python engine, adding the shared token, the caller's IP and (if logged in) their user id.
 *
 * Security model
 *  - Nothing the browser sends is trusted: saved reports are allow-listed, and a verification copy
 *    is stored only if the engine's HMAC signature over the result verifies.
 *  - Same-origin only (no CORS), Origin check on state-changing requests, strict CSP, rate limits.
 *  - No essay text is stored here, ever.
 */

const COOKIE = "burstai_session";
const SESSION_TTL = 30 * 86400;
const PBKDF2_ITER = 100000; // Workers cap PBKDF2 at 100k iterations; the stored hash records its own count
const MAX_PASSWORD = 200;
const MAX_JSON = 300000;
const MAX_SCANS = 500;
const PAGES = {
  "/": ["index.html", false], "/check": ["check.html", true], "/report": ["report.html", true], "/login": ["auth.html", true],
  "/signup": ["auth.html", true], "/me": ["me.html", true], "/privacy": ["privacy.html", false], "/terms": ["terms.html", false],
};
const ENGINE_PATHS = ["/api/check", "/api/extract", "/api/corpus", "/api/health", "/api/gemini"];
const CSP = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'";
const SEC_HEADERS = {
  "x-content-type-options": "nosniff",
  "referrer-policy": "strict-origin-when-cross-origin",
  "x-frame-options": "DENY",
  "permissions-policy": "camera=(), microphone=(), geolocation=(), interest-cohort=()",
  "cross-origin-opener-policy": "same-origin",
  "strict-transport-security": "max-age=31536000; includeSubDomains",
};

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    let res;
    try {
      res = await route(request, env, url);
    } catch (e) {
      const status = e?.status || 500;
      if (status >= 500) console.error("worker error", url.pathname, e?.message);
      res = fail(status, status >= 500 ? "Something went wrong on our side. Please try again." : e.message, e?.headers);
    }
    return secure(res, url.pathname);
  },
};

async function route(request, env, url) {
  const path = url.pathname;
  const method = request.method;

  // One canonical host for pages (keeps search engines and cookies on a single origin).
  const canon = env.PUBLIC_URL ? new URL(env.PUBLIC_URL) : null;
  if (canon && canon.host !== url.host && (method === "GET" || method === "HEAD") && !path.startsWith("/api/") && !path.startsWith("/static/")) {
    return Response.redirect(canon.origin + path + url.search, 301);
  }

  if (path.startsWith("/api/")) {
    // CSRF defence in depth: browsers send Origin on cross-site state-changing requests.
    if (method !== "GET" && method !== "HEAD") {
      const origin = request.headers.get("origin");
      if (origin && new URL(origin).host !== url.host) throw err(403, "Cross-site request blocked.");
    }
    await ensureSchema(env);
    if (path === "/api/engine/register") return registerEngine(request, env);
    if (path.startsWith("/api/auth/")) return auth(request, env, path);
    if (path.startsWith("/api/scans")) return scans(request, env, path);
    if (path.startsWith("/api/reports")) return reports(request, env, path);
    if (ENGINE_PATHS.some((p) => path === p || path.startsWith(p + "/"))) return proxy(request, env, url);
    throw err(404, "Not found");
  }

  if (path === "/robots.txt") {
    return text(`User-agent: *\nAllow: /\nDisallow: /check\nDisallow: /report\nDisallow: /me\nDisallow: /login\nDisallow: /signup\nDisallow: /v/\nDisallow: /api/\n\nSitemap: ${base(env, url)}/sitemap.xml\n`);
  }
  if (path === "/sitemap.xml") {
    const b = base(env, url);
    const urls = [["/", "weekly", "1.0"], ["/privacy", "yearly", "0.3"], ["/terms", "yearly", "0.3"]]
      .map(([p, f, pr]) => `  <url><loc>${b}${p}</loc><changefreq>${f}</changefreq><priority>${pr}</priority></url>`).join("\n");
    return new Response(`<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls}\n</urlset>\n`, { headers: { "content-type": "application/xml" } });
  }
  const page = PAGES[path] || (/^\/v\/[A-Za-z0-9-]{4,40}$/.test(path) ? ["report.html", true] : null);
  if (page) return servePage(page[0], env, url, page[1], 200);
  // The HTML references /static/...; the assets directory is the static folder itself.
  if (path.startsWith("/static/") && !path.endsWith(".html")) {
    const res = await env.ASSETS.fetch(new Request(new URL(path.slice(7) + url.search, url.origin), request));
    if (res.status !== 404) return res;
  }
  return servePage("404.html", env, url, true, 404);
}

// ---------------------------------------------------------------- helpers
const base = (env, url) => (env.PUBLIC_URL || url.origin).replace(/\/$/, "");
const json = (data, status = 200, headers = {}) => new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json; charset=utf-8", ...headers } });
const text = (s) => new Response(s, { headers: { "content-type": "text/plain; charset=utf-8" } });
const fail = (status, detail, headers = {}) => json({ detail }, status, headers);
const err = (status, message, headers) => Object.assign(new Error(message), { status, headers });
const now = () => Date.now() / 1000;

function secure(res, path) {
  const out = new Response(res.body, res);
  for (const [k, v] of Object.entries(SEC_HEADERS)) if (!out.headers.has(k)) out.headers.set(k, v);
  if ((out.headers.get("content-type") || "").startsWith("text/html")) out.headers.set("content-security-policy", CSP);
  if (path.startsWith("/api/")) out.headers.set("cache-control", "no-store");
  return out;
}

async function servePage(name, env, url, noindex, status) {
  let res = await env.ASSETS.fetch(new Request(new URL("/" + name, url.origin)));
  // the assets layer may canonicalise "/x.html" -> "/x"; follow that once
  const loc = res.status >= 300 && res.status < 400 ? res.headers.get("location") : null;
  if (loc) res = await env.ASSETS.fetch(new Request(new URL(loc, url.origin)));
  if (!res.ok) return new Response("Not found", { status: 404, headers: { "content-type": "text/plain" } });
  const html = (await res.text()).replaceAll("__BASE__", base(env, url));
  const headers = { "content-type": "text/html; charset=utf-8", "cache-control": "no-cache" };
  if (noindex) headers["x-robots-tag"] = "noindex, nofollow";
  return new Response(html, { status, headers });
}

async function readJson(request) {
  const raw = await request.text();
  if (raw.length > MAX_JSON) throw err(413, "Request too large.");
  try { const v = JSON.parse(raw); if (v && typeof v === "object") return v; } catch {}
  throw err(400, "Invalid request.");
}

const hex = (buf) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
const fromHex = (h) => new Uint8Array(h.match(/../g).map((x) => parseInt(x, 16)));
async function sha256(s) { return hex(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s))); }
function safeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let d = 0; for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}
async function hmacHex(secret, msg) {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return hex(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(msg)));
}
/** True only if the engine signed exactly this payload. */
async function verifyAttestation(env, payload, sig) {
  if (!env.ENGINE_TOKEN || typeof payload !== "string" || typeof sig !== "string" || payload.length > 20000) return false;
  return safeEqual(await hmacHex(env.ENGINE_TOKEN, payload), sig);
}
const reportId = (sig) => { const s = sig.toUpperCase(); return `BA-${s.slice(0, 5)}-${s.slice(5, 10)}`; };

// ---------------------------------------------------------------- schema (created lazily, idempotent)
let schemaReady = false;
async function ensureSchema(env) {
  if (schemaReady) return;
  await env.DB.batch([
    env.DB.prepare("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL, pw_hash TEXT NOT NULL, created REAL NOT NULL)"),
    env.DB.prepare("CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires REAL NOT NULL)"),
    env.DB.prepare("CREATE TABLE IF NOT EXISTS scans (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, report_id TEXT, title TEXT, words INTEGER, similarity REAL, ai REAL, grammar INTEGER, report TEXT, created REAL NOT NULL)"),
    env.DB.prepare("CREATE INDEX IF NOT EXISTS scans_user ON scans(user_id, created DESC)"),
    env.DB.prepare("CREATE TABLE IF NOT EXISTS certs (id TEXT PRIMARY KEY, payload TEXT NOT NULL, sig TEXT NOT NULL, title TEXT, saved_at REAL NOT NULL)"),
    env.DB.prepare("CREATE TABLE IF NOT EXISTS rl (k TEXT PRIMARY KEY, n INTEGER NOT NULL, reset REAL NOT NULL)"),
    env.DB.prepare("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL, updated REAL NOT NULL)"),
  ]);
  schemaReady = true;
}

// ---------------------------------------------------------------- rate limiting (fixed window, hashed IP)
async function rateLimit(env, request, bucket, limit, windowSec) {
  const ip = request.headers.get("cf-connecting-ip") || "unknown";
  const k = `${bucket}:${(await sha256(ip + "|" + (env.ENGINE_TOKEN || "burstai"))).slice(0, 24)}`;
  const t = now();
  if (Math.random() < 0.02) await env.DB.prepare("DELETE FROM rl WHERE reset < ?").bind(t).run();
  const row = await env.DB.prepare("SELECT n, reset FROM rl WHERE k = ?").bind(k).first();
  if (!row || row.reset < t) {
    await env.DB.prepare("INSERT OR REPLACE INTO rl (k, n, reset) VALUES (?, 1, ?)").bind(k, t + windowSec).run();
    return;
  }
  if (row.n >= limit) {
    const wait = Math.max(1, Math.ceil(row.reset - t));
    const mins = Math.max(1, Math.round(wait / 60));
    throw err(429, `Too many requests. Please try again in about ${mins} minute${mins === 1 ? "" : "s"} - limits keep BurstAI free for everyone.`, { "retry-after": String(wait) });
  }
  await env.DB.prepare("UPDATE rl SET n = n + 1 WHERE k = ?").bind(k).run();
}

// ---------------------------------------------------------------- engine proxy
let engineCache = { url: null, at: 0 };
async function engineUrl(env) {
  if (engineCache.url && Date.now() - engineCache.at < 60000) return engineCache.url;
  let url = null;
  try { url = (await env.DB.prepare("SELECT v FROM kv WHERE k = 'engine_url'").first())?.v || null; } catch {}
  if (!url && env.ENGINE_URL && !/example\.com/.test(env.ENGINE_URL)) url = env.ENGINE_URL;
  engineCache = { url, at: Date.now() };
  return url;
}

/** The engine announces its current public (tunnel) URL here on start-up; needs the shared token. */
async function registerEngine(request, env) {
  if (request.method !== "POST") throw err(405, "Method not allowed");
  if (!env.ENGINE_TOKEN || !safeEqual(request.headers.get("x-engine-token") || "", env.ENGINE_TOKEN)) throw err(401, "engine token required");
  const body = await readJson(request);
  const u = String(body.url || "").replace(/\/+$/, "");
  if (!/^https:\/\/[a-z0-9.-]{4,200}$/i.test(u)) throw err(400, "url must be an https origin");
  await env.DB.prepare("INSERT OR REPLACE INTO kv (k, v, updated) VALUES ('engine_url', ?, ?)").bind(u, now()).run();
  engineCache = { url: u, at: Date.now() };
  return json({ ok: true, url: u });
}

async function proxy(request, env, url) {
  const path = url.pathname;
  if (request.method === "POST") {
    if (path === "/api/check") { await rateLimit(env, request, "scan-m", 6, 60); await rateLimit(env, request, "scan-h", 30, 3600); }
    else if (path === "/api/extract") await rateLimit(env, request, "extract", 60, 3600);
    else if (path.startsWith("/api/gemini")) await rateLimit(env, request, "gemini", 12, 3600);
  }
  const target = await engineUrl(env);
  if (!target) return fail(503, "The checking engine is not connected right now. Please try again later.");
  const headers = new Headers(request.headers);
  for (const h of ["cookie", "host", "x-engine-token", "x-burstai-user", "x-client-ip", "cf-connecting-ip", "x-forwarded-for"]) headers.delete(h);
  if (env.ENGINE_TOKEN) headers.set("x-engine-token", env.ENGINE_TOKEN);
  headers.set("x-client-ip", request.headers.get("cf-connecting-ip") || "unknown");
  if (path.startsWith("/api/corpus") || path === "/api/check") {
    const user = await currentUser(request, env);
    if (user) headers.set("x-burstai-user", String(user.id));
  }
  let res;
  try {
    res = await fetch(new URL(path + url.search, target), { method: request.method, headers, body: request.method === "GET" || request.method === "HEAD" ? undefined : request.body, redirect: "manual" });
  } catch {
    return fail(502, "The checking engine could not be reached. It may be restarting - please try again in a minute.");
  }
  const isJson = (res.headers.get("content-type") || "").includes("json");
  if (res.status >= 520 || (res.status >= 500 && !isJson)) {
    return fail(res.status === 524 ? 504 : 502, res.status === 524
      ? "This scan took longer than the connection allows. Try a shorter text, or scan it in parts."
      : "The checking engine is offline right now. Please try again in a few minutes.");
  }
  return new Response(res.body, { status: res.status, headers: { "content-type": res.headers.get("content-type") || "application/json", ...(res.headers.get("retry-after") ? { "retry-after": res.headers.get("retry-after") } : {}) } });
}

// ---------------------------------------------------------------- sessions + passwords
function cookie(request) {
  const m = (request.headers.get("cookie") || "").match(new RegExp("(?:^|;\\s*)" + COOKIE + "=([^;]+)"));
  return m ? decodeURIComponent(m[1]) : null;
}
const setCookie = (token) => `${COOKIE}=${encodeURIComponent(token)}; Max-Age=${SESSION_TTL}; Path=/; HttpOnly; Secure; SameSite=Lax`;
const clearCookie = () => `${COOKIE}=; Max-Age=0; Path=/; HttpOnly; Secure; SameSite=Lax`;

async function pbkdf2(password, salt, iter) {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(password), "PBKDF2", false, ["deriveBits"]);
  return hex(await crypto.subtle.deriveBits({ name: "PBKDF2", hash: "SHA-256", salt, iterations: iter }, key, 256));
}
async function hashPassword(pw) {
  const salt = crypto.getRandomValues(new Uint8Array(16));
  return `pbkdf2_sha256$${PBKDF2_ITER}$${hex(salt)}$${await pbkdf2(pw, salt, PBKDF2_ITER)}`;
}
async function verifyPassword(pw, stored) {
  try {
    const [, iter, salt, dk] = stored.split("$");
    if (+iter > PBKDF2_ITER) return false; // hash made elsewhere with a count this runtime cannot compute
    return safeEqual(await pbkdf2(pw, fromHex(salt), +iter), dk);
  } catch { return false; }
}
function randomToken() { const b = crypto.getRandomValues(new Uint8Array(32)); return btoa(String.fromCharCode(...b)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, ""); }

async function currentUser(request, env) {
  const tok = cookie(request);
  if (!tok) return null;
  const row = await env.DB.prepare("SELECT u.id, u.email, u.name, u.created FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ? AND s.expires > ?").bind(await sha256(tok), now()).first();
  return row ? { ...row, initial: (row.name || row.email).slice(0, 1).toUpperCase() } : null;
}
async function requireUser(request, env) {
  const u = await currentUser(request, env);
  if (!u) throw err(401, "Please log in");
  return u;
}
async function createSession(env, userId) {
  const tok = randomToken();
  await env.DB.batch([
    env.DB.prepare("DELETE FROM sessions WHERE expires < ?").bind(now()),
    env.DB.prepare("INSERT INTO sessions (token_hash, user_id, expires) VALUES (?, ?, ?)").bind(await sha256(tok), userId, now() + SESSION_TTL),
  ]);
  return tok;
}

// ---------------------------------------------------------------- /api/auth/*
const clean = (s, n) => String(s ?? "").replace(/[\u0000-\u001f<>]/g, " ").slice(0, n).trim();

async function auth(request, env, path) {
  if (path === "/api/auth/me") return json({ user: await currentUser(request, env) });
  if (request.method !== "POST") throw err(405, "Method not allowed");
  if (path === "/api/auth/logout") {
    const tok = cookie(request);
    if (tok) await env.DB.prepare("DELETE FROM sessions WHERE token_hash = ?").bind(await sha256(tok)).run();
    return json({ ok: true }, 200, { "set-cookie": clearCookie() });
  }
  const body = await readJson(request);
  const password = String(body.password || "");

  if (path === "/api/auth/delete") {
    const user = await requireUser(request, env);
    await rateLimit(env, request, "login", 10, 600);
    const row = await env.DB.prepare("SELECT pw_hash FROM users WHERE id = ?").bind(user.id).first();
    if (!row || password.length > MAX_PASSWORD || !(await verifyPassword(password, row.pw_hash))) throw err(401, "Wrong password");
    await env.DB.batch([
      env.DB.prepare("DELETE FROM scans WHERE user_id = ?").bind(user.id),
      env.DB.prepare("DELETE FROM sessions WHERE user_id = ?").bind(user.id),
      env.DB.prepare("DELETE FROM users WHERE id = ?").bind(user.id),
    ]);
    // best effort: also erase their private document bank on the engine
    try {
      const target = await engineUrl(env);
      if (target) await fetch(new URL("/api/corpus", target), { method: "DELETE", headers: { "x-engine-token": env.ENGINE_TOKEN || "", "x-burstai-user": String(user.id) } });
    } catch {}
    return json({ deleted: true }, 200, { "set-cookie": clearCookie() });
  }

  const email = String(body.email || "").trim().toLowerCase().slice(0, 254);
  if (path === "/api/auth/signup") {
    await rateLimit(env, request, "signup", 6, 3600);
    let name = clean(body.name, 80);
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) throw err(400, "Enter a valid email address");
    if (password.length < 8) throw err(400, "Password must be at least 8 characters");
    if (password.length > MAX_PASSWORD) throw err(400, `Password must be at most ${MAX_PASSWORD} characters`);
    if (!name) name = email.split("@")[0];
    if (await env.DB.prepare("SELECT 1 FROM users WHERE email = ?").bind(email).first()) throw err(409, "An account with this email already exists - log in instead");
    const created = now();
    const r = await env.DB.prepare("INSERT INTO users (email, name, pw_hash, created) VALUES (?, ?, ?, ?)").bind(email, name, await hashPassword(password), created).run();
    const id = r.meta.last_row_id;
    return json({ user: { id, email, name, initial: name.slice(0, 1).toUpperCase(), created } }, 200, { "set-cookie": setCookie(await createSession(env, id)) });
  }
  if (path === "/api/auth/login") {
    await rateLimit(env, request, "login", 10, 600);
    const row = await env.DB.prepare("SELECT id, email, name, pw_hash, created FROM users WHERE email = ?").bind(email).first();
    if (!row || password.length > MAX_PASSWORD || !(await verifyPassword(password, row.pw_hash))) throw err(401, "Wrong email or password");
    return json({ user: { id: row.id, email: row.email, name: row.name, initial: (row.name || row.email).slice(0, 1).toUpperCase(), created: row.created } }, 200, { "set-cookie": setCookie(await createSession(env, row.id)) });
  }
  throw err(404, "Not found");
}

// ---------------------------------------------------------------- saved scans (allow-listed, no essay text)
const KINDS = new Set(["exact", "near", "paraphrase"]);
const num = (v, lo, hi) => { if (v === null || v === undefined || v === "") return null; const f = Number(v); return Number.isFinite(f) ? Math.min(hi, Math.max(lo, f)) : null; };
const int = (v, hi = 1e7) => Math.trunc(num(v, 0, hi) ?? 0);

async function sanitizeView(env, r) {
  if (!r || typeof r !== "object") throw err(400, "bad report");
  const sources = [];
  for (const s of (Array.isArray(r.sources) ? r.sources : []).slice(0, 25)) {
    if (!s || typeof s !== "object") continue;
    const u = String(s.url || "").slice(0, 500);
    if (!/^(https?:\/\/|corpus:\/\/)/.test(u)) continue;
    sources.push({ n: int(s.n, 999), url: u, host: clean(s.host, 120), title: clean(s.title, 200), kind: clean(s.kind, 40), pct: num(s.pct, 0, 100) ?? 0, words: int(s.words), mainKind: KINDS.has(s.mainKind) ? s.mainKind : "exact" });
  }
  const rows = [];
  for (const row of (Array.isArray(r.rows) ? r.rows : []).slice(0, 3000)) {
    if (!row || typeof row !== "object") continue;
    rows.push({ src: int(row.src, 999) || null, ai: num(row.ai, 0, 1), excluded: clean(row.excluded, 20) || null, kind: KINDS.has(row.kind) ? row.kind : null });
  }
  let grammar = null;
  if (r.grammar && typeof r.grammar === "object") {
    const kinds = {};
    for (const [k, v] of Object.entries(r.grammar.kinds || {}).slice(0, 12)) kinds[clean(k, 20) || "other"] = int(v, 1e5);
    grammar = { open: int(r.grammar.open, 1e5), kinds };
  }
  let attestation = null;
  if (r.attestation && (await verifyAttestation(env, r.attestation.payload, r.attestation.sig))) attestation = { payload: r.attestation.payload, sig: r.attestation.sig };
  const id = String(r.id || "");
  return {
    id: /^BA-[0-9A-F]{5}-[0-9A-F]{5}$/.test(id) ? id : "",
    date: num(r.date, 0, 1e14) ?? Date.now(),
    title: clean(r.title, 160) || "Untitled",
    words: int(r.words), similarity: num(r.similarity, 0, 100), aiPct: num(r.aiPct, 0, 100),
    aiLevel: clean(r.aiLevel, 20), aiVerdict: clean(r.aiVerdict, 60), aiConfidence: clean(r.aiConfidence, 20),
    aiFlagged: int(r.aiFlagged), aiTotal: int(r.aiTotal),
    sources, rows, grammar,
    hasPlag: !!r.hasPlag, hasAi: !!r.hasAi, hasGrammar: !!r.hasGrammar, edited: !!r.edited, attestation,
  };
}

async function scans(request, env, path) {
  const user = await requireUser(request, env);
  const m = path.match(/^\/api\/scans\/(\d+)$/);
  const method = request.method;
  if (!m && method === "GET") {
    const { results } = await env.DB.prepare("SELECT id, report_id, title, words, similarity, ai, grammar, created FROM scans WHERE user_id = ? ORDER BY created DESC LIMIT 50").bind(user.id).all();
    return json(results);
  }
  if (!m && method === "POST") {
    await rateLimit(env, request, "scans", 120, 3600);
    const report = await sanitizeView(env, await readJson(request));
    const count = (await env.DB.prepare("SELECT COUNT(*) AS c FROM scans WHERE user_id = ?").bind(user.id).first())?.c || 0;
    if (count >= MAX_SCANS) await env.DB.prepare("DELETE FROM scans WHERE id IN (SELECT id FROM scans WHERE user_id = ? ORDER BY created ASC LIMIT 20)").bind(user.id).run();
    if (report.id) await env.DB.prepare("DELETE FROM scans WHERE user_id = ? AND report_id = ?").bind(user.id, report.id).run();
    const r = await env.DB.prepare("INSERT INTO scans (user_id, report_id, title, words, similarity, ai, grammar, report, created) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)")
      .bind(user.id, report.id, report.title, report.words, report.similarity ?? 0, report.aiPct, report.grammar ? report.grammar.open : null, JSON.stringify(report), now()).run();
    return json({ id: r.meta.last_row_id });
  }
  if (m && method === "GET") {
    const row = await env.DB.prepare("SELECT report FROM scans WHERE user_id = ? AND id = ?").bind(user.id, +m[1]).first();
    if (!row) throw err(404, "Not found");
    return new Response(row.report, { headers: { "content-type": "application/json; charset=utf-8" } });
  }
  if (m && method === "DELETE") {
    const r = await env.DB.prepare("DELETE FROM scans WHERE user_id = ? AND id = ?").bind(user.id, +m[1]).run();
    if (!r.meta.changes) throw err(404, "Not found");
    return json({ deleted: +m[1] });
  }
  throw err(405, "Method not allowed");
}

// ---------------------------------------------------------------- /api/reports (engine-signed verification copies)
async function reports(request, env, path) {
  const m = path.match(/^\/api\/reports\/(BA-[0-9A-F]{5}-[0-9A-F]{5})$/);
  if (path === "/api/reports" && request.method === "POST") {
    await rateLimit(env, request, "reports", 30, 3600);
    const body = await readJson(request);
    if (!(await verifyAttestation(env, body.payload, body.sig))) throw err(400, "This result was not signed by the BurstAI engine, so it cannot be certified. Run the scan again.");
    const id = reportId(body.sig);
    // INSERT OR IGNORE: a certificate can never be overwritten
    await env.DB.prepare("INSERT OR IGNORE INTO certs (id, payload, sig, title, saved_at) VALUES (?, ?, ?, ?, ?)").bind(id, body.payload, body.sig, clean(body.title, 160) || "Untitled", now()).run();
    return json({ id });
  }
  if (m && request.method === "GET") {
    const row = await env.DB.prepare("SELECT id, payload, sig, title, saved_at FROM certs WHERE id = ?").bind(m[1]).first();
    if (!row || !(await verifyAttestation(env, row.payload, row.sig))) throw err(404, "Report not found");
    return json(row);
  }
  throw err(404, "Report not found");
}
