(() => {
  const $ = (id) => document.getElementById(id);
  const q = new URLSearchParams(location.search);
  const PANEL = { plag: { bg: "#FFD3DC", t: "Similarity" }, ai: { bg: "#B79CFF", t: "AI writing" }, grammar: { bg: "#7FB3FF", t: "Grammar" } };
  const C = 2 * Math.PI * 42;

  const state = {
    text: "", data: null, sum: null,
    tab: ["plag", "ai", "grammar"].includes(q.get("mode")) ? q.get("mode") : "plag",
    focus: null, fixed: {}, dismissed: {}, excludeQuotes: true, excludeBib: true,
    activeIssue: null,
  };

  // ------------------------------------------------------------------ boot
  (async function boot() {
    let text = B.store.get("text");
    if (q.get("sample")) { text = await (await fetch("/static/samples/urban_heat.txt")).text(); B.store.set("text", text); B.store.del("result"); }
    if (!text) { $("work").replaceWith($("tpl-gate").content.cloneNode(true)); return; }
    state.text = text;
    const { title } = B.titleOf(text);
    $("crumb").textContent = title; setChip(text);
    renderDoc();
    bindTabs();
    B.mountNav("scan");

    const cached = B.store.get("result");
    if (cached && cached.text === text) { onResult(cached); return; }

    try {
      const h = await B.api("/api/health").catch(() => null);
      if (h && !h.lm.loaded && !h.lm.error) $("s-sub").textContent = "Loading language models (first run can take a minute)…";
      const data = await B.api("/api/check", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, options: { exclude_quotes: true, exclude_references: true, exclude_citations: true } }),
      });
      B.store.set("result", data);
      onResult(data);
    } catch (err) {
      $("score").classList.remove("loading"); $("s-title").textContent = "Scan failed"; $("s-sub").textContent = err.message;
      $("big").textContent = "!";
      ["t-plag", "t-ai", "t-gram"].forEach((id) => ($(id).textContent = "–"));
    }
  })();

  function setChip(text) { const t = `${B.words(text).toLocaleString()} words`; $("chip").textContent = t; const sm = $("chip-sm"); if (sm) sm.textContent = t; }

  function onResult(data) {
    state.data = data; $("score").classList.remove("loading");
    recompute(); render();
    const s = state.sum;
    const hist = (B.store.local("history") || []).filter((h) => h.title !== s.title);
    hist.unshift({ title: s.title, sim: s.similarity ?? 0, ai: s.aiPct, date: Date.now() });
    B.store.local("history", hist.slice(0, 20));
    const report = buildReport(); B.store.set("report", report);
    // logged-in users get the scan saved to their account (scores only, never the essay)
    B.me().then((u) => { if (u && !state.savedOnce) { state.savedOnce = true; B.api("/api/scans", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(report) }).catch(() => {}); } });
  }

  function recompute() { state.sum = B.summarize(state.data, state); }

  // ------------------------------------------------------------------ tabs
  function bindTabs() {
    document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => { state.tab = b.dataset.tab; state.focus = null; render(); }));
    $("cert").addEventListener("click", (e) => {
      if (!state.sum) { e.preventDefault(); B.toast("Wait for the scan to finish first."); return; }
      B.store.set("report", buildReport());
    });
  }

  // ------------------------------------------------------------------ render
  function render() {
    document.querySelectorAll(".tab").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === state.tab)));
    const s = state.sum;
    if (s) {
      $("t-plag").textContent = s.similarity == null ? "–" : B.pct(s.similarity);
      $("t-ai").textContent = s.aiPct == null ? "–" : B.pct(s.aiPct);
      $("t-gram").textContent = s.G ? String(s.openIssues.length) : "–";
    }
    renderDoc(); renderScore(); renderPanel();
  }

  /** Document with per-sentence marks. Works before results too (plain text). */
  function renderDoc() {
    const text = state.text, s = state.sum;
    const { title, headingEnd } = B.titleOf(text);
    $("title").textContent = title;
    const body = $("body"); body.innerHTML = "";
    const bodyStart = headingEnd > 0 ? headingEnd : 0;

    // paragraphs = non-empty lines
    const paras = [];
    const re = /[^\n]+/g; let m;
    while ((m = re.exec(text))) { if (m.index + m[0].length <= bodyStart) continue; if (!m[0].trim()) continue; paras.push([m.index, m.index + m[0].length]); }

    const rows = s ? s.bodyRows : [];
    const issues = s ? s.issues : [];
    for (const [ps, pe] of paras) {
      const p = document.createElement("p");
      const inPara = rows.filter((r) => r.start >= ps && r.end <= pe);
      if (!inPara.length) { p.textContent = text.slice(ps, pe).trim(); body.appendChild(p); continue; }
      let cur = ps;
      for (const r of inPara) {
        if (r.start > cur) p.appendChild(document.createTextNode(text.slice(cur, r.start)));
        p.appendChild(sentenceNode(r, issues));
        cur = r.end;
      }
      if (cur < pe) p.appendChild(document.createTextNode(text.slice(cur, pe)));
      body.appendChild(p);
    }
  }

  function sentenceNode(r, issues) {
    const frag = document.createDocumentFragment();
    const tab = state.tab;
    if (tab === "plag" && r.src && !r.excluded) {
      const active = !state.focus || state.focus === r.src.n;
      const b = document.createElement("span"); b.className = "badge" + (active ? "" : " dim"); b.style.background = r.src.color; b.textContent = r.src.n; b.setAttribute("aria-hidden", "true");
      frag.appendChild(b);
    }
    const span = document.createElement("span"); span.className = "s"; span.dataset.i = r.index;
    if (tab === "plag") {
      if (r.src && !r.excluded) {
        const active = !state.focus || state.focus === r.src.n;
        span.classList.add(active ? "src-on" : "src-off", "clickable"); span.style.setProperty("--t", r.src.tint);
        span.title = `Source ${r.src.n} · ${r.src.host} · ${r.match.kind} match ${Math.round(r.match.score)}%`;
        span.addEventListener("click", () => { state.focus = state.focus === r.src.n ? null : r.src.n; render(); });
      } else if (r.excluded && r.excluded !== "heading") { span.classList.add("excl"); span.title = `Excluded from the score (${r.excluded})`; }
    } else if (tab === "ai" && r.ai != null) {
      if (r.ai >= 0.6) span.classList.add("ai-on"); else if (r.ai >= 0.45) span.classList.add("ai-soft");
      span.title = `AI likelihood ${Math.round(r.ai * 100)}%${r.ppl ? ` · perplexity ${r.ppl}` : ""}`;
    }
    // grammar marks nested inside the sentence
    const mine = issues.filter((it) => it.offset >= r.start && it.offset < r.end).sort((a, b) => a.offset - b.offset);
    let cur = r.start;
    for (const it of mine) {
      const a = Math.max(cur, it.offset), e = Math.min(r.end, it.offset + it.length);
      if (a > cur) span.appendChild(document.createTextNode(state.text.slice(cur, a)));
      const g = document.createElement("span");
      g.className = "g " + it.kind + (state.fixed[it.id] ? " fixed" : tab === "grammar" ? " on" : "");
      g.dataset.g = it.id; g.textContent = state.text.slice(a, e); g.title = it.message;
      if (tab === "grammar" && !state.fixed[it.id]) g.addEventListener("click", () => { state.activeIssue = it.id; renderPanel(); document.querySelector(`[data-fix="${it.id}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" }); });
      span.appendChild(g); cur = e;
    }
    if (cur < r.end) span.appendChild(document.createTextNode(state.text.slice(cur, r.end)));
    frag.appendChild(span);
    return frag;
  }

  function renderScore() {
    const s = state.sum; if (!s) return;
    const box = $("score"), tab = state.tab;
    box.style.background = PANEL[tab].bg;
    let v = "–", f = 0, title = PANEL[tab].t, sub = "", label = title;
    if (tab === "plag") {
      if (s.similarity == null) { sub = "Plagiarism check did not run."; }
      else { v = B.pct(s.similarity); f = s.similarity / 100; sub = s.sources.length ? `${s.sources.length} source${s.sources.length === 1 ? "" : "s"} share wording. Tap one to isolate it in the text.` : `No matching sources found across ${s.P.candidates_checked} page${s.P.candidates_checked === 1 ? "" : "s"} checked.`; label = `Similarity ${Math.round(s.similarity)} percent`; }
    } else if (tab === "ai") {
      if (s.aiPct == null) { sub = "AI detection did not run."; }
      else { v = B.pct(s.aiPct); f = s.aiPct / 100; sub = `${s.aiFlagged.length} of ${s.aiRows.length} sentences look AI-written. ${s.aiLevel} overall · ${s.aiConfidence} confidence.`; label = `AI writing ${Math.round(s.aiPct)} percent`; }
    } else {
      if (!s.G) { sub = "Grammar check unavailable."; }
      else { v = String(s.openIssues.length); f = s.issues.length ? s.openIssues.length / s.issues.length : 0; sub = s.openIssues.length ? "Accept or dismiss — the text updates live." : "All suggestions handled."; label = `${s.openIssues.length} grammar issues open`; }
    }
    $("big").textContent = v; $("tile").textContent = v; $("tile").style.background = PANEL[tab].bg; $("arc").setAttribute("stroke-dasharray", `${(C * Math.min(1, f)).toFixed(1)} ${C.toFixed(1)}`);
    $("s-title").textContent = title; $("s-sub").textContent = sub; $("ring").setAttribute("aria-label", label);
  }

  function renderPanel() {
    const s = state.sum, panel = $("panel");
    if (!s) { panel.innerHTML = `<div class="empty">Results will appear here.</div>`; return; }
    panel.innerHTML = "";
    const h = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
    const warns = s.warnings.map((w) => h("div", "warn", B.esc(w)));

    if (state.tab === "plag") {
      const row = h("div", "row", `<h3>Match overview</h3>`);
      const all = h("button", "link", "Show all"); all.type = "button"; all.addEventListener("click", () => { state.focus = null; render(); }); row.appendChild(all);
      panel.appendChild(row);
      if (!s.P) panel.appendChild(h("div", "empty", "Plagiarism check did not run."));
      else if (!s.sources.length) panel.appendChild(h("div", "empty", `No source shares wording with this text. ${s.P.candidates_checked} web pages were compared${s.P.queries.length ? ` using ${s.P.queries.length} search queries` : ""}.`));
      s.sources.forEach((src) => {
        const b = h("button", "src"); b.type = "button"; b.setAttribute("aria-pressed", String(state.focus === src.n)); b.style.setProperty("--t", src.tint);
        b.innerHTML = `<span class="n" style="background:${src.color}">${src.n}</span><span class="meta"><span class="host">${B.esc(src.host)}</span><span class="kind">${B.esc(src.kind)} · ${src.words} words</span></span><span class="pct">${B.pct(src.pct)}</span>`;
        b.addEventListener("click", () => { state.focus = state.focus === src.n ? null : src.n; render(); });
        panel.appendChild(b);
        if (state.focus === src.n) {
          const first = s.rows.find((r) => r.src === src);
          const d = h("div", "src-detail", `<b>${B.esc(src.title || src.host)}</b> · ${B.matchDesc[src.mainKind]}${src.url.startsWith("http") ? ` · <a href="${B.esc(src.url)}" target="_blank" rel="noopener">open source</a>` : ""}${first ? `<q>${B.esc(first.match.source_text)}</q>` : ""}`);
          panel.appendChild(d);
        }
      });
      const ex = h("div", "excl-box");
      ex.appendChild(checkbox("Exclude quotes", state.excludeQuotes, (v) => { state.excludeQuotes = v; recompute(); render(); }));
      ex.appendChild(checkbox("Exclude bibliography", state.excludeBib, (v) => { state.excludeBib = v; recompute(); render(); }));
      panel.appendChild(ex);
      panel.appendChild(corpusBlock());
      warns.forEach((w) => panel.appendChild(w));
    } else if (state.tab === "ai") {
      panel.appendChild(h("h3", "", "Rhythm by sentence"));
      if (!s.A) panel.appendChild(h("div", "empty", "AI detection did not run."));
      else {
        if (s.aiRows.length) {
          const bars = h("div", "rhythm"); bars.setAttribute("aria-hidden", "true");
          s.aiRows.forEach((r) => { const d = document.createElement("div"); d.style.height = Math.max(8, Math.round(r.ai * 100)) + "%"; if (r.ai >= 0.6) d.className = "hi"; d.title = `Sentence ${r.index + 1}: ${Math.round(r.ai * 100)}%`; bars.appendChild(d); });
          panel.appendChild(bars);
        } else panel.appendChild(h("div", "empty", "Sentence-level scores need the language models, which are not loaded yet."));
        const flagged = [...s.aiFlagged].sort((a, b) => b.ai - a.ai).slice(0, 6);
        if (!flagged.length) panel.appendChild(h("div", "empty", `No sentence crosses the 60% AI-likelihood line. ${B.esc(s.aiVerdict)}.`));
        flagged.forEach((r) => panel.appendChild(h("div", "ai-card", `<div class="hd"><span>Sentence ${s.bodyRows.indexOf(r) + 1}</span><span>${Math.round(r.ai * 100)}%</span></div><div class="tx">${B.esc(r.text)}</div><div class="why">${B.esc(B.aiWhy(r, s.A))}</div>`)));
        const sig = s.A.signals, st = s.A.stylometry;
        const kv = [["Verdict", s.A.verdict], ["Binoculars (lower = AI)", sig.binoculars], ["Perplexity (lower = AI)", sig.perplexity], ["Burstiness (lower = AI)", sig.burstiness], ["Sentence-length variation", st.sentence_len_cv], ["LLM-phrase rate /1000 words", st.llm_phrase_rate], ["Gemini judge", sig.gemini_p != null ? Math.round(sig.gemini_p * 100) + "%" : null]].filter(([, v]) => v != null && v !== "");
        panel.appendChild(h("div", "signals", kv.map(([k, v]) => `<span>${k}</span><span>${B.esc(String(v))}</span>`).join("")));
        if (s.A.reasons?.length) panel.appendChild(h("p", "note", s.A.reasons.slice(0, 3).map(B.esc).join(" · ")));
      }
      panel.appendChild(h("p", "note", "AI scores are probabilities, not proof."));
      warns.forEach((w) => panel.appendChild(w));
    } else {
      panel.appendChild(h("h3", "", `${s.openIssues.length} suggestion${s.openIssues.length === 1 ? "" : "s"}`));
      if (!s.G) panel.appendChild(h("div", "empty", "Grammar check unavailable right now (the free LanguageTool API may be rate-limited). Try again in a minute."));
      s.openIssues.forEach((it) => {
        const from = state.text.slice(it.offset, it.offset + it.length);
        const to = it.replacements[0];
        const card = h("div", "fix", `<div class="kind">${B.esc(it.kind)}</div><div class="change">${to != null ? `<s>${B.esc(from)}</s> → <b>${B.esc(to)}</b>` : `<b>${B.esc(from)}</b>`}</div><div class="why">${B.esc(it.message)}</div>`);
        card.dataset.fix = it.id;
        if (state.activeIssue === it.id) card.style.boxShadow = "4px 4px 0 #0E1B3D";
        const acts = h("div", "acts");
        if (to != null) { const a = h("button", "accept", "Accept"); a.type = "button"; a.addEventListener("click", () => accept(it, to)); acts.appendChild(a); }
        const d = h("button", "", "Dismiss"); d.type = "button"; d.addEventListener("click", () => { state.dismissed[it.id] = true; recompute(); render(); }); acts.appendChild(d);
        card.appendChild(acts); panel.appendChild(card);
      });
      if (s.G && !s.openIssues.length) panel.appendChild(h("div", "clean", "All clean. Nice work."));
      warns.forEach((w) => panel.appendChild(w));
    }
  }

  function checkbox(label, checked, onChange) {
    const l = document.createElement("label"); l.className = "check";
    const i = document.createElement("input"); i.type = "checkbox"; i.checked = checked; i.addEventListener("change", () => onChange(i.checked));
    l.append(i, document.createTextNode(label)); return l;
  }

  /** Apply a grammar fix to the live text and shift every offset after it. */
  function accept(it, to) {
    const delta = to.length - it.length;
    state.text = state.text.slice(0, it.offset) + to + state.text.slice(it.offset + it.length);
    const shift = (pos) => (pos > it.offset ? pos + delta : pos);
    const d = state.data;
    d.text = state.text;
    d.sentences.forEach((s) => { if (s.start > it.offset) s.start += delta; if (s.end > it.offset) s.end += delta; });
    (d.plagiarism?.sentences || []).forEach((s) => { if (s.start > it.offset) s.start += delta; if (s.end > it.offset) s.end += delta; });
    (d.ai?.sentences || []).forEach((s) => { if (s.start > it.offset) s.start += delta; if (s.end > it.offset) s.end += delta; });
    (d.grammar?.issues || []).forEach((g) => { if (g.offset > it.offset) g.offset = shift(g.offset); });
    it.length = to.length;
    state.fixed[it.id] = true;
    B.store.set("text", state.text); B.store.set("result", d);
    setChip(state.text);
    recompute(); render();
  }

  // ------------------------------------------------------------------ local corpus
  function corpusBlock() {
    const det = document.createElement("details"); det.className = "corpus";
    det.innerHTML = `<summary>Also compare against your own documents</summary><ul id="corpus-list"><li>Loading…</li></ul><label class="btn add"><input type="file" id="corpus-file" multiple hidden accept=".txt,.md,.docx,.pdf">Add documents</label>`;
    det.addEventListener("toggle", () => det.open && loadCorpus());
    det.querySelector("#corpus-file").addEventListener("change", async (e) => {
      for (const f of e.target.files) { const fd = new FormData(); fd.append("file", f); try { await B.api("/api/corpus", { method: "POST", body: fd }); } catch (err) { B.toast(`${f.name}: ${err.message}`); } }
      e.target.value = ""; loadCorpus(); B.toast("Added. Re-run the scan to compare against them.");
    });
    return det;
  }
  async function loadCorpus() {
    const ul = document.getElementById("corpus-list"); if (!ul) return;
    try {
      const docs = await B.api("/api/corpus");
      ul.innerHTML = docs.length ? "" : `<li><span style="color:#55617E">Empty — add past submissions or course readings.</span></li>`;
      docs.forEach((d) => {
        const li = document.createElement("li");
        li.innerHTML = `<span>${B.esc(d.title)}</span><small style="color:#55617E">${d.n_words.toLocaleString()} w</small><button type="button" aria-label="Remove">✕</button>`;
        li.querySelector("button").addEventListener("click", async () => { await B.api(`/api/corpus/${d.id}`, { method: "DELETE" }); loadCorpus(); });
        ul.appendChild(li);
      });
    } catch { ul.innerHTML = `<li>Could not load corpus.</li>`; }
  }

  // ------------------------------------------------------------------ report payload
  function buildReport() {
    const s = state.sum, now = Date.now();
    const kinds = {}; s.openIssues.forEach((it) => (kinds[it.kind] = (kinds[it.kind] || 0) + 1));
    return {
      id: B.reportId(state.text, new Date(now).toDateString()), date: now,
      title: s.title, words: s.totalWords, text: state.text,
      similarity: s.similarity, aiPct: s.aiPct, aiLevel: s.aiLevel, aiVerdict: s.aiVerdict, aiConfidence: s.aiConfidence,
      aiFlagged: s.aiFlagged.length, aiTotal: s.aiRows.length,
      sources: s.sources.map((x) => ({ n: x.n, url: x.url, host: x.host, title: x.title, kind: x.kind, pct: x.pct, words: x.words, color: x.color, tint: x.tint, mainKind: x.mainKind, sentences: x.sentences })),
      rows: s.bodyRows.map((r) => ({ text: r.text, src: r.src ? r.src.n : null, ai: r.ai, excluded: r.excluded, kind: r.match?.kind || null })),
      grammar: s.G ? { open: s.openIssues.length, kinds } : null,
      hasPlag: !!s.P, hasAi: !!s.A, hasGrammar: !!s.G,
      excludeQuotes: state.excludeQuotes, excludeBib: state.excludeBib,
    };
  }
})();
