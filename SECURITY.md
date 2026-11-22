# Security

## Reporting a vulnerability

Please do **not** open a public issue for a security problem. Use GitHub's private
"Report a vulnerability" form on this repository (Security tab), or contact the maintainer
privately through the profile linked there. Include steps to reproduce; do not include other
people's data. You will get an acknowledgement, and a fix or mitigation plan, as soon as possible.

## What the design assumes

* **Nothing the browser sends is trusted.** Saved scans are allow-listed field by field; a public
  verification copy (`/v/<id>`) is stored only if the engine's HMAC signature over the result
  verifies, and its id is derived from that signature, so reports cannot be forged or overwritten.
* **No essay text is stored** by the Worker or the engine (only by the user's own browser, and in
  the user's private document bank when they explicitly upload a document there).
* **Same-origin only.** No CORS headers are sent; state-changing requests with a foreign `Origin`
  are rejected; the session cookie is `HttpOnly; Secure; SameSite=Lax`.
* **Strict CSP** (`script-src 'self'`, no inline scripts, self-hosted fonts), `X-Frame-Options: DENY`,
  `nosniff`, HSTS on the Worker.
* **SSRF guard.** The page fetcher only requests http(s) URLs that resolve to public addresses and
  re-checks every redirect hop.
* **Rate limits** on scans, uploads, logins, sign-ups and report creation (hashed IP, short-lived).
* **Passwords** are salted PBKDF2-SHA256 hashes (100k iterations on the Worker, which is the
  platform maximum; 210k on the Python server). Session tokens are stored hashed.
* **The engine is not public.** In engine-only mode it answers only requests carrying the shared
  token from the Worker and never serves the UI.

## Operator checklist

* Keep `BURSTAI_ENGINE_TOKEN` / the Worker secret `ENGINE_TOKEN` long, random and out of git. Rotate
  it if it is ever pasted somewhere it should not be (existing signed reports stop verifying).
* Bind the engine to `127.0.0.1` and reach it through a tunnel; do not expose port 8000 directly.
* Back up the Worker's D1 database and, for stand-alone installs, the `data/` directory.
* Known gaps: no email verification and no password reset; no CAPTCHA on sign-up (rate limits only);
  no automated dependency scanning yet (enable Dependabot on the repository).
