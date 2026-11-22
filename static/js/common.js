/* Shared helpers for all burstai pages. */
window.__errs = [];
window.addEventListener("error", (e) => window.__errs.push(e.message));
window.addEventListener("unhandledrejection", (e) => window.__errs.push(String(e.reason)));
document.addEventListener("securitypolicyviolation", (e) => window.__errs.push(`CSP ${e.violatedDirective} ${e.blockedURI}`));
window.B = (() => {
  const SRC = [
    { c: "#FF5C7A", t: "#FFD3DC" }, { c: "#FFB020", t: "#FFE6B3" }, { c: "#22C38E", t: "#C6F1E0" },
    { c: "#7FB3FF", t: "#DDE9FF" }, { c: "#B79CFF", t: "#E6DCFF" }, { c: "#FFD23F", t: "#FFF2B8" },
    { c: "#7CF2D0", t: "#D3FBEF" }, { c: "#FFB4C6", t: "#FFE3EA" },
  ];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  // same definition of a "word" as the engine: letters/digits of any script, inner marks, apostrophes, hyphens
  const words = (s) => (String(s || "").match(/[\p{L}\p{N}][\p{L}\p{M}\p{N}'’-]*/gu) || []).length;
  const host = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return u.startsWith("corpus://") ? "your corpus" : u; } };
  const pct = (n, d = 0) => Number(n || 0).toFixed(d) + "%";
  const fmtDate = (d) => new Date(d).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });

  async function api(path, opts) {
    const r = await fetch(path, opts);
    if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch {} throw new Error(m); }
    return r.json();
  }
  const store = {
    set(k, v) { try { sessionStorage.setItem("burstai." + k, JSON.stringify(v)); } catch {} },
    get(k) { try { const v = sessionStorage.getItem("burstai." + k); return v ? JSON.parse(v) : null; } catch { return null; } },
    del(k) { try { sessionStorage.removeItem("burstai." + k); } catch {} },
    local(k, v) {
      try { if (v === undefined) { const x = localStorage.getItem("burstai." + k); return x ? JSON.parse(x) : null; } localStorage.setItem("burstai." + k, JSON.stringify(v)); } catch { return null; }
    },
  };

  /** Title + body split: if the first line looks like a heading, use it. */
  function titleOf(text) {
    const lines = text.split(/\n/).map((l) => l.trim()).filter(Boolean);
    const first = lines[0] || "Untitled";
    const isHeading = words(first) <= 14 && !/[.!?]$/.test(first) && lines.length > 1;
    if (isHeading) return { title: first, headingEnd: text.indexOf(first) + first.length };
    const w = first.split(/\s+/).slice(0, 7).join(" ");
    return { title: w + (words(first) > 7 ? "…" : ""), headingEnd: -1 };
  }

  /** Report ids are derived from the engine's signature, so they cannot be chosen or forged. */
  function reportId(sig) { const s = String(sig || "").toUpperCase(); return /^[0-9A-F]{10,}$/.test(s) ? `BA-${s.slice(0, 5)}-${s.slice(5, 10)}` : ""; }

  /** SHA-256 of the normalised text - identical to app/security.py:text_hash (NFC, collapse whitespace, trim). */
  async function textHash(text) {
    const norm = String(text).normalize("NFC").split(/\s+/).filter(Boolean).join(" ");
    const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(norm));
    return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
  }

  let toastT;
  function toast(msg) {
    const t = document.getElementById("toast"); if (!t) return;
    t.textContent = msg; t.classList.add("show"); clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove("show"), 2600);
  }

  const srcKind = (url, origin) => origin === "corpus" || /^corpus:/.test(url) ? "Your corpus" : origin === "wikipedia" || /wikipedia\.org/.test(url) ? "Wikipedia" : origin === "academic" || /doi\.org|arxiv\.org|europepmc|semanticscholar|openalex|ncbi\.nlm|springer|sciencedirect|jstor|researchgate|\.edu\//.test(url) ? "Academic paper" : "Website";
  const matchDesc = { exact: "near-identical wording", near: "light rewrite of the source", paraphrase: "paraphrase matched by meaning" };

  /**
   * Turn a raw /api/check result into everything the UI needs, applying client-side exclusions and grammar edits.
   * state: { excludeQuotes, excludeBib, dismissed:{id:true}, fixed:{id:true} }
   */
  function summarize(data, state) {
    const S = data.sentences || [];
    const P = data.plagiarism && !data.plagiarism.error ? data.plagiarism : null;
    const A = data.ai && !data.ai.error ? data.ai : null;
    const G = data.grammar && data.grammar.available ? data.grammar : null;
    const { title, headingEnd } = titleOf(data.text);

    const plagBy = {}; (P?.sentences || []).forEach((s) => (plagBy[s.index] = s));
    const aiBy = {}; (A?.sentences || []).forEach((s) => (aiBy[s.index] = s));

    // sources ranked by matched words under current exclusions
    const srcAgg = {};
    const rows = S.map((s) => {
      const text = data.text.slice(s.start, s.end);
      const isHeading = s.end <= headingEnd;
      const excluded = isHeading ? "heading" : (state.excludeQuotes && s.quote) ? "quote" : (state.excludeBib && (s.reference || s.citation)) ? (s.reference ? "bibliography" : "citation") : null;
      const m = plagBy[s.index]?.match || null;
      const w = words(text);
      if (m && !excluded) {
        const a = srcAgg[m.url] || (srcAgg[m.url] = { url: m.url, title: m.title, words: 0, kinds: {}, sentences: [] });
        a.words += w; a.kinds[m.kind] = (a.kinds[m.kind] || 0) + 1; a.sentences.push(s.index);
      }
      const ap = aiBy[s.index]?.p;
      return { index: s.index, start: s.start, end: s.end, text, words: w, excluded, match: m, ai: ap == null ? null : ap, ppl: aiBy[s.index]?.ppl ?? null, isHeading };
    });
    const counted = rows.filter((r) => !r.excluded);
    const countedWords = counted.reduce((a, r) => a + r.words, 0) || 1;
    const origins = {}; (P?.sources || []).forEach((s) => (origins[s.url] = s.origin));
    const sources = Object.values(srcAgg).sort((a, b) => b.words - a.words).map((s, i) => ({
      ...s, n: i + 1, host: host(s.url), kind: srcKind(s.url, origins[s.url]), color: SRC[i % SRC.length].c, tint: SRC[i % SRC.length].t,
      pct: (100 * s.words) / countedWords,
      mainKind: Object.entries(s.kinds).sort((a, b) => b[1] - a[1])[0]?.[0] || "exact",
    }));
    const srcN = {}; sources.forEach((s) => (srcN[s.url] = s));
    rows.forEach((r) => { r.src = r.match ? srcN[r.match.url] || null : null; });
    const matchedWords = counted.reduce((a, r) => a + (r.match ? r.words : 0), 0);
    const kindWords = { exact: 0, near: 0, paraphrase: 0 };
    counted.forEach((r) => { if (r.match) kindWords[r.match.kind] += r.words; });

    const bodyRows = rows.filter((r) => !r.isHeading);
    const aiRows = bodyRows.filter((r) => r.ai != null);
    const aiFlagged = aiRows.filter((r) => r.ai >= 0.6);
    const aiPct = A ? A.ai_probability : null;
    const aiLevel = aiPct == null ? "–" : aiPct >= 70 ? "High" : aiPct >= 35 ? "Moderate" : "Low";

    const issues = (G?.issues || []).map((it, i) => ({ ...it, id: "g" + i })).filter((it) => !state.dismissed[it.id]);
    const open = issues.filter((it) => !state.fixed[it.id]);

    return {
      title, headingEnd, text: data.text, totalWords: rows.reduce((a, r) => a + r.words, 0), countedWords,
      P, A, G, rows, bodyRows, sources,
      similarity: P ? (100 * matchedWords) / countedWords : null, kindWords, matchedWords,
      aiPct, aiLevel, aiFlagged, aiRows, aiVerdict: A?.verdict || "", aiConfidence: A?.confidence || "",
      issues, openIssues: open,
      attestation: data.attestation || null,
      warnings: [...(P?.warnings || []), ...(A?.warnings || []), ...(data.plagiarism?.error ? ["Plagiarism: " + data.plagiarism.error] : []), ...(data.ai?.error ? ["AI detection: " + data.ai.error] : []), ...(data.grammar && !data.grammar.available && data.grammar.error ? ["Grammar check is unavailable right now (the free grammar service may be busy). Try again in a minute."] : [])],
    };
  }

  /** Explain why a sentence looks AI-written, from the signals we have. */
  function aiWhy(row, A) {
    const out = [];
    if (row.ppl != null && row.ppl < 12) out.push("Very predictable wording");
    else if (row.ppl != null && row.ppl < 25) out.push("Predictable wording");
    const phrases = (A?.stylometry?.llm_phrases || []).filter((p) => row.text.toLowerCase().includes(p));
    if (phrases.some((p) => /^(moreover|furthermore|additionally|ultimately|overall|in conclusion|in summary)/.test(p))) out.push("stock transition");
    else if (phrases.length) out.push("LLM-favourite phrasing (" + phrases.slice(0, 2).join(", ") + ")");
    if (!/\d/.test(row.text) && !/[A-Z][a-z]+ [A-Z][a-z]+/.test(row.text) && row.words > 12) out.push("no concrete detail");
    if (!out.length) out.push("even rhythm");
    return out.join(" · ");
  }

  /** Simple citation generator from a URL + title. */
  function cite(src, fmt, date) {
    const d = new Date(date || Date.now());
    const site = src.host; const title = (src.title || src.host).replace(/\s*[-|–].*$/, "").trim() || src.host;
    const url = src.url.startsWith("http") ? src.url : "";
    const long = d.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });
    const us = d.toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" });
    const mla = d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
    switch (fmt) {
      case "MLA": return `“${title}.” ${site}, ${url}. Accessed ${mla}.`;
      case "Harvard": return `${site} (${d.getFullYear()}) ${title}. Available at: ${url} (Accessed: ${long}).`;
      case "IEEE": return `“${title},” ${site}. [Online]. Available: ${url}. [Accessed: ${mla}].`;
      default: return `${title}. (${d.getFullYear()}). ${site}. Retrieved ${us}, from ${url}`;
    }
  }

  async function copy(text) { try { await navigator.clipboard.writeText(text); toast("Copied to clipboard"); } catch { toast("Copy failed — select and copy manually"); } }

  /** Current user (cached for 60s in sessionStorage). Resolves to null when logged out or offline. */
  let mePromise = null;
  function me(force) {
    const c = store.get("me");
    if (!force && c && Date.now() - c.at < 60000) return Promise.resolve(c.user);
    if (!mePromise) mePromise = api("/api/auth/me").then((r) => { store.set("me", { user: r.user, at: Date.now() }); mePromise = null; return r.user; }).catch(() => { mePromise = null; return null; });
    return mePromise;
  }

  /** Update any [data-auth] slots on the page: shows avatar + My writing when logged in, Log in otherwise. */
  async function paintAuth() {
    const user = await me();
    document.querySelectorAll("[data-auth=login]").forEach((el) => { if (user) el.hidden = true; });
    document.querySelectorAll("[data-auth=user]").forEach((el) => {
      if (!user) { el.hidden = true; return; }
      el.hidden = false; el.querySelectorAll(".avatar").forEach((a) => (a.textContent = user.initial));
    });
    return user;
  }

  /** Mobile bottom navigation (shown by CSS only at phone width). */
  function mountNav(active) {
    if (document.querySelector(".bottom-nav")) return;
    const icons = {
      home: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M4 11l8-7 8 7v9H4z"/></svg>',
      scan: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/></svg>',
      me: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/></svg>',
      reports: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5"/></svg>',
    };
    const items = [["home", "/", "Home"], ["scan", store.get("text") ? "/check" : "/#scan", "Scan"], ["me", "/me", "My writing"], ["reports", store.get("report") ? "/report" : "/me#reports", "Reports"]];
    const nav = document.createElement("nav"); nav.className = "bottom-nav"; nav.setAttribute("aria-label", "App");
    nav.innerHTML = items.map(([k, href, label]) => `<a href="${href}" ${k === active ? 'aria-current="page"' : ""}><span class="ic">${icons[k]}</span>${label}</a>`).join("");
    document.body.appendChild(nav);
    document.body.classList.add("has-nav");
  }

  /* ---------------- Gemini key (user's own, kept in this browser only) ---------------- */
  const gemini = {
    get() { try { return localStorage.getItem("burstai.gemini_key") || ""; } catch { return ""; } },
    set(k) { try { if (k) localStorage.setItem("burstai.gemini_key", k); else localStorage.removeItem("burstai.gemini_key"); } catch {} document.dispatchEvent(new Event("burstai:gemini")); },
    has() { return !!gemini.get(); },
  };
  const DIALOG_CSS = `
.gm-back{position:fixed;inset:0;background:rgba(14,27,61,.55);z-index:60;display:grid;place-items:center;padding:16px}
.gm-box{background:#fff;border:2px solid #0E1B3D;border-radius:24px;box-shadow:8px 8px 0 #0E1B3D;width:min(520px,100%);padding:24px;font-family:Lexend,system-ui,sans-serif;color:#0E1B3D}
.gm-box h2{font-family:"Bricolage Grotesque",Lexend,sans-serif;font-weight:800;font-size:26px;letter-spacing:-.03em;margin:0 0 6px;display:flex;align-items:center;gap:10px}
.gm-box h2 span{width:36px;height:36px;border-radius:12px;background:#C6F432;border:2px solid #0E1B3D;display:grid;place-items:center;font-size:18px}
.gm-box p{margin:0 0 14px;font-size:14px;color:#3D4966;line-height:1.55;font-weight:300}
.gm-box ul{margin:0 0 14px;padding-left:18px;font-size:14px;color:#3D4966;line-height:1.6}
.gm-box label{display:grid;gap:6px;font-size:14px;font-weight:500}
.gm-row{display:flex;gap:8px}
.gm-box input{flex:1;font:300 15px Lexend,sans-serif;color:#0E1B3D;border:2px solid #0E1B3D;border-radius:12px;padding:11px 14px;min-height:46px;min-width:0}
.gm-eye{border:2px solid #0E1B3D;background:#fff;border-radius:12px;min-width:46px;cursor:pointer;font:500 13px Lexend}
.gm-status{min-height:22px;font-size:13px;margin:10px 0 4px}
.gm-status.ok{color:#2b8a3e}.gm-status.err{color:#B4231F}
.gm-acts{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}
.gm-btn{font:600 14px Lexend;border:2px solid #0E1B3D;border-radius:12px;padding:0 16px;min-height:44px;cursor:pointer;background:#fff;color:#0E1B3D}
.gm-btn.p{background:#C6F432;box-shadow:4px 4px 0 #0E1B3D}.gm-btn.d{color:#B4231F;margin-left:auto}
.gm-btn:disabled{opacity:.6;cursor:progress}
.gm-small{font-size:12px;color:#55617E;margin-top:12px}
.gm-box a{color:#2F4BEA}
.gemini-chip{display:inline-flex;align-items:center;gap:8px;font:500 14px Lexend,sans-serif;color:#0E1B3D;background:#fff;border:2px solid #0E1B3D;border-radius:12px;padding:0 14px;min-height:44px;cursor:pointer}
.gemini-chip.on{background:#E6FAD0}
.gemini-chip .dot{width:10px;height:10px;border-radius:50%;background:#B8C2DE;border:2px solid #0E1B3D}
.gemini-chip.on .dot{background:#C6F432}
.gemini-nudge{display:flex;gap:12px;align-items:center;background:#E6FAD0;border:2px solid #0E1B3D;border-radius:16px;padding:12px 14px;font-size:14px;line-height:1.45}
.gemini-nudge button{font:600 13px Lexend;border:2px solid #0E1B3D;border-radius:10px;padding:0 12px;min-height:40px;cursor:pointer;background:#C6F432;white-space:nowrap;box-shadow:3px 3px 0 #0E1B3D}
`;
  function geminiDialog(onDone) {
    if (!document.getElementById("gm-style")) { const st = document.createElement("style"); st.id = "gm-style"; st.textContent = DIALOG_CSS; document.head.appendChild(st); }
    document.querySelector(".gm-back")?.remove();
    const back = document.createElement("div"); back.className = "gm-back"; back.setAttribute("role", "dialog"); back.setAttribute("aria-modal", "true"); back.setAttribute("aria-labelledby", "gm-title");
    const has = gemini.has();
    back.innerHTML = `<div class="gm-box">
      <h2 id="gm-title"><span>✦</span>Add your Gemini key</h2>
      <p>Give BurstAI a brain. With your own free Gemini key, grammar suggestions are checked in context (no more "pgvector → pg vector") and the AI detector gets a second opinion with reasons.</p>
      <ul><li>Free at <a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener">aistudio.google.com/apikey</a> → Create API key</li><li>Stays in <b>this browser only</b>. BurstAI never stores it.</li><li>While a key is set, the text you scan is also sent to Google Gemini.</li></ul>
      <label>Gemini API key<div class="gm-row"><input id="gm-key" type="password" autocomplete="off" spellcheck="false" placeholder="AIza…" value="${esc(gemini.get())}"><button type="button" class="gm-eye" id="gm-eye" aria-label="Show key">Show</button></div></label>
      <div class="gm-status" id="gm-status" aria-live="polite">${has ? "A key is saved on this device." : ""}</div>
      <div class="gm-acts"><button type="button" class="gm-btn p" id="gm-save">Validate &amp; save</button><button type="button" class="gm-btn" id="gm-cancel">Cancel</button>${has ? '<button type="button" class="gm-btn d" id="gm-remove">Remove key</button>' : ""}</div>
      <div class="gm-small">Without a key everything still works with the rule-based checks.</div>
    </div>`;
    const opener = document.activeElement;
    document.body.appendChild(back);
    const $ = (id) => back.querySelector("#" + id);
    const close = () => { back.remove(); document.removeEventListener("keydown", onKey); if (opener && opener.focus) opener.focus(); };
    const onKey = (e) => {
      if (e.key === "Escape") return close();
      if (e.key !== "Tab") return;
      // keep keyboard focus inside the dialog
      const f = [...back.querySelectorAll("a[href], button:not([disabled]), input")];
      const first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey);
    back.addEventListener("click", (e) => { if (e.target === back) close(); });
    $("gm-cancel").addEventListener("click", close);
    $("gm-eye").addEventListener("click", () => { const i = $("gm-key"); i.type = i.type === "password" ? "text" : "password"; $("gm-eye").textContent = i.type === "password" ? "Show" : "Hide"; });
    $("gm-remove")?.addEventListener("click", () => { gemini.set(""); toast("Gemini key removed"); close(); onDone && onDone(false); });
    $("gm-save").addEventListener("click", async () => {
      const key = $("gm-key").value.trim(); const st = $("gm-status");
      if (key.length < 20) { st.className = "gm-status err"; st.textContent = "That doesn't look like a Gemini key."; return; }
      $("gm-save").disabled = true; st.className = "gm-status"; st.textContent = "Checking the key with Gemini…";
      try {
        const r = await api("/api/gemini/validate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key }) });
        if (r.ok) { gemini.set(key); st.className = "gm-status ok"; st.textContent = `Works (${r.model}). Saved on this device.`; toast("Gemini connected ✦"); setTimeout(close, 700); onDone && onDone(true); }
        else { st.className = "gm-status err"; st.textContent = r.error || "Key rejected."; }
      } catch (e) { st.className = "gm-status err"; st.textContent = "Could not reach the engine: " + e.message; }
      $("gm-save").disabled = false;
    });
    setTimeout(() => $("gm-key").focus(), 50);
  }
  /** A small status chip: "Gemini: on/off", opens the dialog. */
  function geminiChip() {
    const b = document.createElement("button"); b.type = "button"; b.className = "gemini-chip";
    const paint = () => { const on = gemini.has(); b.classList.toggle("on", on); b.innerHTML = `<span class="dot"></span>${on ? "Gemini on" : "Add Gemini key"}`; b.title = on ? "Your Gemini key is active (click to change)" : "Add your free Gemini key for smarter grammar and AI detection"; };
    paint(); document.addEventListener("burstai:gemini", paint);
    b.addEventListener("click", () => geminiDialog());
    return b;
  }

  return { SRC, esc, words, host, pct, fmtDate, api, store, titleOf, reportId, toast, summarize, aiWhy, cite, copy, matchDesc, srcKind, me, paintAuth, mountNav, gemini, geminiDialog, geminiChip, textHash };
})();
