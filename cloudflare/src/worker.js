/**
 * BurstAI on Cloudflare Workers.
 *
 *  - Serves ./static as the site (assets binding), injecting PUBLIC_URL into the pages.
 *  - Implements accounts, sessions, saved scans and saved reports on D1 (same endpoints as app/main.py).
 *  - Proxies the heavy endpoints (/api/check, /api/extract, /api/corpus*, /api/health) to the Python engine.
 *
 * No third-party service and no API key is involved beyond your own engine URL.
 */

const COOKIE = "burstai_session";
const SESSION_TTL = 30 * 86400;
const PBKDF2_ITER = 210000;
const NOINDEX = new Set(["/check", "/report", "/login", "/signup", "/me"]);
const PAGES = { "/": "index.html", "/check": "check.html", "/report": "report.html", "/login": "auth.html", "/signup": "auth.html", "/me": "me.html" };
const ENGINE_PATHS = ["/api/check", "/api/extract", "/api/corpus", "/api/health"];

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;
    try {
      if (path.startsWith("/api/auth/")) return auth(request, env, path);
      if (path.startsWith("/api/scans")) return scans(request, env, path);
      if (path.startsWith("/api/reports")) return reports(request, env, path);
      if (ENGINE_PATHS.some((p) => path === p || path.startsWith(p + "/"))) return proxy(request, env, url);
      if (path === "/robots.txt") return text(`User-agent: *\nAllow: /\nDisallow: /check\nDisallow: /report\nDisallow: /v/\nDisallow: /api/\n\nSitemap: ${base(env, url)}/sitemap.xml\n`);
      if (path === "/sitemap.xml") return new Response(`<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n  <url><loc>${base(env, url)}/</loc><changefreq>weekly</changefreq><priority>1.0</priority></url>\n</urlset>\n`, { headers: { "content-type": "application/xml" } });
      const page = PAGES[path] || (path.startsWith("/v/") ? "report.html" : null);
      if (page) return servePage(page, env, url, NOINDEX.has(path) || path.startsWith("/v/"));
      return env.ASSETS.fetch(request);
    } catch (e) {
      return json({ detail: String(e?.message || e) }, 500);
    }
  },
};

// ---------------------------------------------------------------- helpers
const base = (env, url) => (env.PUBLIC_URL || url.origin).replace(/\/$/, "");
const json = (data, status = 200, headers = {}) => new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json; charset=utf-8", ...headers } });
const text = (s) => new Response(s, { headers: { "content-type": "text/plain; charset=utf-8" } });
const fail = (status, detail) => json({ detail }, status);

async function servePage(name, env, url, noindex) {
  const res = await env.ASSETS.fetch(new Request(new URL("/" + name, url.origin)));
  let html = await res.text();
  html = html.replaceAll("__BASE__", base(env, url));
  const headers = { "content-type": "text/html; charset=utf-8", "cache-control": "no-cache" };
  if (noindex) headers["x-robots-tag"] = "noindex, nofollow";
  return new Response(html, { headers });
}

async function proxy(request, env, url) {
  if (!env.ENGINE_URL) return fail(503, "Engine not configured: set ENGINE_URL in wrangler.toml");
  const target = new URL(url.pathname + url.search, env.ENGINE_URL);
  const headers = new Headers(request.headers);
  headers.delete("cookie");
  headers.delete("host");
  if (env.ENGINE_TOKEN) headers.set("x-engine-token", env.ENGINE_TOKEN);
  const init = { method: request.method, headers, body: request.method === "GET" || request.method === "HEAD" ? undefined : request.body, redirect: "manual" };
  try {
    return await fetch(target, init);
  } catch (e) {
    return fail(502, "Engine unreachable: " + (e?.message || e));
  }
}

function cookie(request) {
  const m = (request.headers.get("cookie") || "").match(new RegExp("(?:^|;\\s*)" + COOKIE + "=([^;]+)"));
  return m ? decodeURIComponent(m[1]) : null;
}
const setCookie = (token) => `${COOKIE}=${encodeURIComponent(token)}; Max-Age=${SESSION_TTL}; Path=/; HttpOnly; Secure; SameSite=Lax`;
const clearCookie = () => `${COOKIE}=; Max-Age=0; Path=/; HttpOnly; Secure; SameSite=Lax`;

const hex = (buf) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
const fromHex = (h) => new Uint8Array(h.match(/../g).map((x) => parseInt(x, 16)));
async function sha256(s) { return hex(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s))); }

async function pbkdf2(password, salt, iter) {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(password), "PBKDF2", false, ["deriveBits"]);
  return hex(await crypto.subtle.deriveBits({ name: "PBKDF2", hash: "SHA-256", salt, iterations: iter }, key, 256));
}
async function hashPassword(pw) {
  const salt = crypto.getRandomValues(new Uint8Array(16));
  return `pbkdf2_sha256$${PBKDF2_ITER}$${hex(salt)}$${await pbkdf2(pw, salt, PBKDF2_ITER)}`;
}
async function verifyPassword(pw, stored) {
  try { const [, iter, salt, dk] = stored.split("$"); return (await pbkdf2(pw, fromHex(salt), +iter)) === dk; } catch { return false; }
}
function randomToken() { const b = crypto.getRandomValues(new Uint8Array(32)); return btoa(String.fromCharCode(...b)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, ""); }

async function currentUser(request, env) {
  const tok = cookie(request);
  if (!tok) return null;
  const row = await env.DB.prepare("SELECT u.id, u.email, u.name, u.created FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ? AND s.expires > ?").bind(await sha256(tok), Date.now() / 1000).first();
  return row ? { ...row, initial: (row.name || row.email).slice(0, 1).toUpperCase() } : null;
}
async function requireUser(request, env) {
  const u = await currentUser(request, env);
  if (!u) throw Object.assign(new Error("Please log in"), { status: 401 });
  return u;
}
async function createSession(env, userId) {
  const tok = randomToken();
  await env.DB.batch([
    env.DB.prepare("DELETE FROM sessions WHERE expires < ?").bind(Date.now() / 1000),
    env.DB.prepare("INSERT INTO sessions (token_hash, user_id, expires) VALUES (?, ?, ?)").bind(await sha256(tok), userId, Date.now() / 1000 + SESSION_TTL),
  ]);
  return tok;
}

// ---------------------------------------------------------------- /api/auth/*
async function auth(request, env, path) {
  if (path === "/api/auth/me") { const u = await currentUser(request, env); return json({ user: u }); }
  if (request.method !== "POST") return fail(405, "Method not allowed");
  if (path === "/api/auth/logout") {
    const tok = cookie(request);
    if (tok) await env.DB.prepare("DELETE FROM sessions WHERE token_hash = ?").bind(await sha256(tok)).run();
    return json({ ok: true }, 200, { "set-cookie": clearCookie() });
  }
  const body = await request.json().catch(() => ({}));
  const email = String(body.email || "").trim().toLowerCase();
  const password = String(body.password || "");
  if (path === "/api/auth/signup") {
    let name = String(body.name || "").trim().slice(0, 80);
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) return fail(400, "Enter a valid email address");
    if (password.length < 8) return fail(400, "Password must be at least 8 characters");
    if (!name) name = email.split("@")[0];
    if (await env.DB.prepare("SELECT 1 FROM users WHERE email = ?").bind(email).first()) return fail(409, "An account with this email already exists - log in instead");
    const created = Date.now() / 1000;
    const r = await env.DB.prepare("INSERT INTO users (email, name, pw_hash, created) VALUES (?, ?, ?, ?)").bind(email, name, await hashPassword(password), created).run();
    const id = r.meta.last_row_id;
    return json({ user: { id, email, name, initial: name.slice(0, 1).toUpperCase(), created } }, 200, { "set-cookie": setCookie(await createSession(env, id)) });
  }
  if (path === "/api/auth/login") {
    const row = await env.DB.prepare("SELECT id, email, name, pw_hash, created FROM users WHERE email = ?").bind(email).first();
    if (!row || !(await verifyPassword(password, row.pw_hash))) return fail(401, "Wrong email or password");
    return json({ user: { id: row.id, email: row.email, name: row.name, initial: row.name.slice(0, 1).toUpperCase(), created: row.created } }, 200, { "set-cookie": setCookie(await createSession(env, row.id)) });
  }
  return fail(404, "Not found");
}

// ---------------------------------------------------------------- /api/scans
async function scans(request, env, path) {
  let user;
  try { user = await requireUser(request, env); } catch (e) { return fail(e.status || 401, e.message); }
  const m = path.match(/^\/api\/scans\/(\d+)$/);
  if (!m && request.method === "GET") {
    const { results } = await env.DB.prepare("SELECT id, report_id, title, words, similarity, ai, grammar, created FROM scans WHERE user_id = ? ORDER BY created DESC LIMIT 50").bind(user.id).all();
    return json(results);
  }
  if (!m && request.method === "POST") {
    const report = await request.json().catch(() => ({}));
    delete report.text;
    const rid = String(report.id || "");
    await env.DB.prepare("DELETE FROM scans WHERE user_id = ? AND report_id = ? AND report_id <> ''").bind(user.id, rid).run();
    const r = await env.DB.prepare("INSERT INTO scans (user_id, report_id, title, words, similarity, ai, grammar, report, created) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)")
      .bind(user.id, rid, String(report.title || "Untitled").slice(0, 160), +report.words || 0, +report.similarity || 0, report.aiPct == null ? null : +report.aiPct, report.grammar ? +report.grammar.open || 0 : null, JSON.stringify(report), Date.now() / 1000).run();
    return json({ id: r.meta.last_row_id });
  }
  if (m && request.method === "GET") {
    const row = await env.DB.prepare("SELECT report FROM scans WHERE user_id = ? AND id = ?").bind(user.id, +m[1]).first();
    return row ? new Response(row.report, { headers: { "content-type": "application/json" } }) : fail(404, "Not found");
  }
  if (m && request.method === "DELETE") {
    const r = await env.DB.prepare("DELETE FROM scans WHERE user_id = ? AND id = ?").bind(user.id, +m[1]).run();
    return r.meta.changes ? json({ deleted: +m[1] }) : fail(404, "Not found");
  }
  return fail(405, "Method not allowed");
}

// ---------------------------------------------------------------- /api/reports (public verification copies)
async function reports(request, env, path) {
  const m = path.match(/^\/api\/reports\/([A-Za-z0-9-]{4,32})$/);
  if (!m && request.method === "POST") {
    const report = await request.json().catch(() => ({}));
    const id = String(report.id || "");
    if (!/^[A-Za-z0-9-]{4,32}$/.test(id)) return fail(400, "bad report id");
    delete report.text;
    report.saved_at = Date.now() / 1000;
    await env.DB.prepare("INSERT OR REPLACE INTO reports (id, report, saved_at) VALUES (?, ?, ?)").bind(id, JSON.stringify(report), report.saved_at).run();
    return json({ id });
  }
  if (m && request.method === "GET") {
    const row = await env.DB.prepare("SELECT report FROM reports WHERE id = ?").bind(m[1]).first();
    return row ? new Response(row.report, { headers: { "content-type": "application/json" } }) : fail(404, "Report not found");
  }
  return fail(404, "Not found");
}
