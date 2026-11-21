/* Shared helpers for all burstai pages. */
window.__errs = [];
window.addEventListener("error", (e) => window.__errs.push(e.message));
window.addEventListener("unhandledrejection", (e) => window.__errs.push(String(e.reason)));
window.B = (() => {
  const SRC = [
    { c: "#FF5C7A", t: "#FFD3DC" }, { c: "#FFB020", t: "#FFE6B3" }, { c: "#22C38E", t: "#C6F1E0" },
    { c: "#7FB3FF", t: "#DDE9FF" }, { c: "#B79CFF", t: "#E6DCFF" }, { c: "#FFD23F", t: "#FFF2B8" },
    { c: "#7CF2D0", t: "#D3FBEF" }, { c: "#FFB4C6", t: "#FFE3EA" },
  ];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const words = (s) => (String(s || "").match(/[A-Za-z0-9À-ɏऀ-ॿ][\w'’À-ɏऀ-ॿ-]*/g) || []).length;
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

  function reportId(text, date) {
    let h = 5381; const s = text + "|" + date;
    for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
    const al = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"; let u = Math.abs(h), o = "";
    for (let i = 0; i < 6; i++) { o += al[u % al.length]; u = Math.floor(u / al.length) + i * 7; }
    return "BA-" + o.slice(0, 4) + "-" + o.slice(4);
  }

  let toastT;
  function toast(msg) {
    const t = document.getElementById("toast"); if (!t) return;
    t.textContent = msg; t.classList.add("show"); clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove("show"), 2600);
  }

  const srcKind = (url, origin) => origin === "corpus" ? "Your corpus" : origin === "wikipedia" || /wikipedia\.org/.test(url) ? "Wikipedia" : /doi\.org|arxiv\.org|semanticscholar|openalex|ncbi\.nlm|springer|sciencedirect|jstor|researchgate|\.edu\//.test(url) ? "Academic paper" : "Website";
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
      warnings: [...(P?.warnings || []), ...(A?.warnings || []), ...(data.plagiarism?.error ? ["Plagiarism check failed: " + data.plagiarism.error] : []), ...(data.ai?.error ? ["AI detection failed: " + data.ai.error] : []), ...(data.grammar && !data.grammar.available && data.grammar.error ? ["Grammar check unavailable: " + data.grammar.error] : [])],
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

  return { SRC, esc, words, host, pct, fmtDate, api, store, titleOf, reportId, toast, summarize, aiWhy, cite, copy, matchDesc, srcKind, me, paintAuth, mountNav };
})();
