/* Originality report. Three sources of data:
 *   - local     : the scan just run in this browser (sessionStorage) - full sentence view
 *   - account   : a scan saved to the user's account - scores and sources only (no essay text is stored)
 *   - verified  : /v/<id> - ONLY what the engine itself signed; cannot be edited by anyone
 * Everything is rendered with textContent / DOM nodes; no stored value is ever injected as HTML. */
(() => {
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
  const level = (p) => (p == null ? "–" : p >= 70 ? "High" : p >= 35 ? "Moderate" : "Low");
  let R = null, mode = "local", savedId = null;

  (async function boot() {
    const m = location.pathname.match(/^\/v\/(BA-[0-9A-F]{5}-[0-9A-F]{5})$/);
    const scanId = new URLSearchParams(location.search).get("scan");
    if (location.pathname.startsWith("/v/")) {
      try {
        if (!m) throw new Error("bad id");
        R = fromAttestation(await B.api("/api/reports/" + m[1]));
        mode = "verified"; savedId = R.id;
        $("back").href = "/"; $("back").textContent = "Home"; $("back-sm").href = "/";
      } catch { return gate("Report not found", "This verification link does not match a report signed by this BurstAI engine."); }
    } else if (scanId && /^\d+$/.test(scanId)) {
      try { R = await B.api("/api/scans/" + scanId); mode = "account"; $("back").href = "/me"; $("back").textContent = "My writing"; $("back-sm").href = "/me"; }
      catch { location.replace("/login?next=" + encodeURIComponent(location.pathname + location.search)); return; }
    } else {
      R = B.store.get("report");
      if (!R) { location.replace("/check"); return; }
    }
    normalise();
    render();
    B.mountNav("reports");
  })();

  function gate(title, msg) {
    const main = $("main"); main.textContent = "";
    const g = el("div", "gate"), card = el("div", "card");
    card.append(el("h2", "", title), el("p", "", msg));
    const a = el("a", "btn btn-lime", "Run a scan"); a.href = "/"; card.appendChild(a);
    g.appendChild(card); main.appendChild(g);
  }

  /** Build the view model from an engine-signed attestation (the only thing a verified page shows). */
  function fromAttestation(doc) {
    const A = JSON.parse(doc.payload);
    return {
      id: doc.id, title: doc.title || "Untitled", date: A.checked_at * 1000, words: A.words, nSentences: A.sentences,
      similarity: A.similarity, hasPlag: A.similarity != null, pages: A.pages_compared,
      sources: (A.sources || []).map((s, i) => ({ n: i + 1, url: String(s.url || ""), title: String(s.title || ""), pct: Number(s.percent) || 0, origin: s.origin })),
      aiPct: A.ai, hasAi: A.ai != null, aiVerdict: A.ai_verdict || "", aiConfidence: A.ai_confidence || "",
      grammar: A.grammar_issues != null ? { open: A.grammar_issues, kinds: {} } : null,
      rows: [], textHash: A.text_sha256, engine: A.engine, exclusions: A.exclusions || {}, coverage: A.coverage || null,
    };
  }

  /** Coerce every field to a safe type; never trust stored values. */
  function normalise() {
    const n = (v) => (v == null || !Number.isFinite(Number(v)) ? null : Number(v));
    R.title = String(R.title || "Untitled").slice(0, 160);
    R.words = n(R.words) || 0; R.date = n(R.date) || Date.now();
    R.similarity = n(R.similarity); R.aiPct = n(R.aiPct);
    R.hasPlag = !!R.hasPlag && R.similarity != null; R.hasAi = !!R.hasAi && R.aiPct != null;
    R.aiLevel = level(R.aiPct);
    R.sources = (Array.isArray(R.sources) ? R.sources : []).slice(0, 25).map((s, i) => {
      const url = String(s.url || "");
      const pal = B.SRC[i % B.SRC.length];
      return { n: i + 1, origN: n(s.n) || i + 1, url, http: /^https?:\/\//.test(url), host: B.host(url), title: String(s.title || "").slice(0, 200),
               kind: B.srcKind(url, s.origin || (s.kind === "Your corpus" ? "corpus" : s.kind === "Academic paper" ? "academic" : "")),
               pct: Math.max(0, Math.min(100, n(s.pct) || 0)), mainKind: B.matchDesc[s.mainKind] ? s.mainKind : null, color: pal.c, tint: pal.t };
    });
    const byOrig = {}; R.sources.forEach((s) => (byOrig[s.origN] = s));
    R.rows = (Array.isArray(R.rows) ? R.rows : []).slice(0, 3000).map((r) => ({
      text: typeof r.text === "string" ? r.text : null, src: r.src != null ? byOrig[n(r.src)] || null : null,
      ai: n(r.ai), excluded: r.excluded ? String(r.excluded).slice(0, 20) : null, kind: B.matchDesc[r.kind] ? r.kind : null,
    }));
    R.hasText = R.rows.some((r) => r.text);
    if (R.grammar) R.grammar = { open: n(R.grammar.open) || 0, kinds: R.grammar.kinds && typeof R.grammar.kinds === "object" ? R.grammar.kinds : {} };
    R.aiFlagged = mode === "verified" ? null : R.rows.filter((r) => r.ai != null && r.ai >= 0.6).length;
    R.aiTotal = R.rows.filter((r) => r.ai != null).length;
    if (mode === "local" && R.attestation?.sig) R.id = B.reportId(R.attestation.sig);
    R.id = /^BA-[0-9A-F]{5}-[0-9A-F]{5}$/.test(String(R.id)) ? R.id : "";
  }

  function render() {
    document.title = `${R.title} — Originality report — BurstAI`;
    $("eyebrow").textContent = mode === "verified" ? "ORIGINALITY REPORT · SIGNED COPY" : mode === "account" ? "ORIGINALITY REPORT · SAVED SCAN" : "ORIGINALITY REPORT";
    $("title").textContent = R.title;
    $("meta").textContent = `${R.words.toLocaleString()} words · checked ${B.fmtDate(R.date)}${R.id ? ` · report ID ${R.id}` : ""}`;

    // --- score cards
    if (R.hasPlag) {
      $("p-tag").textContent = plural(R.sources.length, "source");
      $("p-big").textContent = B.pct(R.similarity);
      const bar = $("p-bar"); bar.textContent = "";
      R.sources.forEach((s) => { const d = el("div"); d.style.width = s.pct.toFixed(1) + "%"; d.style.background = s.color; bar.appendChild(d); });
    } else { $("p-tag").textContent = "not run"; $("p-big").textContent = "–"; }
    if (R.hasAi) {
      $("a-tag").textContent = R.aiLevel; $("a-big").textContent = B.pct(R.aiPct);
      $("a-sub").textContent = [R.aiFlagged != null && R.aiTotal ? `${R.aiFlagged} of ${R.aiTotal} sentences flagged` : R.aiVerdict, R.aiConfidence ? `${R.aiConfidence} confidence` : ""].filter(Boolean).join(" · ");
    } else { $("a-tag").textContent = "no score"; $("a-big").textContent = "–"; $("a-sub").textContent = R.aiVerdict || "AI detection did not produce a score for this text."; }
    if (R.grammar) {
      const kinds = Object.keys(R.grammar.kinds).slice(0, 5);
      $("g-tag").textContent = plural(R.grammar.open, "issue"); $("g-big").textContent = String(R.grammar.open);
      $("g-sub").textContent = kinds.length ? kinds.join(" · ") : R.grammar.open ? "Suggestions found" : "Nothing left to fix";
    } else { $("g-tag").textContent = "not run"; $("g-big").textContent = "–"; }

    $("summary").textContent = summaryText();
    renderNotice();
    renderSentences();
    renderSources();
    renderCertificate();
  }

  function renderNotice() {
    const box = $("notice"); box.textContent = ""; box.hidden = true;
    const add = (t) => { box.hidden = false; box.appendChild(el("p", "", t)); };
    if (mode === "verified") add(`These scores and sources were produced and signed by the BurstAI engine on ${B.fmtDate(R.date)}; nobody can edit them. They describe one specific text (fingerprint ${R.textHash.slice(0, 12)}…). This page does not prove who wrote that text.`);
    if (mode === "account") add("Saved scans keep scores and sources only. For privacy the essay text is never stored, so sentence-level details are available only right after a scan.");
    // what was actually searched - taken from the signed result whenever there is one
    let cov = R.coverage;
    if (!cov && R.attestation?.payload) { try { cov = JSON.parse(R.attestation.payload).coverage; } catch {} }
    if (R.hasPlag && cov && typeof cov === "object") {
      const n = (k) => Math.max(0, Number(cov[k]) || 0);
      add(`Compared against ${n("web")} web page${n("web") === 1 ? "" : "s"}, ${n("wikipedia")} Wikipedia article${n("wikipedia") === 1 ? "" : "s"}, ${n("academic")} academic abstract${n("academic") === 1 ? "" : "s"}${n("corpus") ? ` and ${n("corpus")} private document${n("corpus") === 1 ? "" : "s"}` : ""}.`);
      if (cov.web_searched === false) add("Important: web search engines did not accept queries during this scan, so ordinary web pages were NOT checked. The similarity score may be understated and is not evidence of originality. Scan again later.");
      else if (!n("web") && n("queries")) add(`The web was searched for ${n("queries")} distinctive phrases from the text and no page containing them was found.`);
    }
    if (mode === "local" && R.edited) add("You edited the text after the scan (grammar fixes). The certificate covers the text as it was scanned; run a new scan to certify the edited version.");
  }

  function renderSentences() {
    const sec = $("sec-table"), flagSec = $("sec-flags");
    if (!R.rows.length) { sec.hidden = true; flagSec.hidden = true; return; }
    const tb = $("rows"); tb.textContent = "";
    const label = (r, i, max) => (r.text ? (r.text.length > max ? r.text.slice(0, max).trimEnd() + "…" : r.text) : `Sentence ${i + 1} (text not stored)`);
    R.rows.forEach((r, i) => {
      const tr = el("tr");
      tr.appendChild(el("td", "n", String(i + 1)));
      const tx = el("td", "tx", label(r, i, 140));
      if (!r.text) tx.style.color = "#8A93AC";
      if (r.excluded) { tx.append(" "); tx.appendChild(el("span", "srcpill none", `(${r.excluded})`)); }
      tr.appendChild(tx);
      const tdS = el("td");
      if (r.src && !r.excluded) { const p = el("span", "srcpill", `Source ${r.src.n}`); p.style.background = r.src.tint; tdS.appendChild(p); } else tdS.appendChild(el("span", "srcpill none", "—"));
      tr.appendChild(tdS);
      const tdA = el("td");
      if (r.ai == null) tdA.appendChild(el("span", "srcpill none", "—"));
      else {
        const ai = Math.round(r.ai * 100), wrap = el("div", "aibar"), trk = el("div", "trk"), fill = el("div", ai > 60 ? "hi" : "");
        fill.style.width = ai + "%"; trk.appendChild(fill); wrap.append(trk, el("span", "", ai + "%")); tdA.appendChild(wrap);
      }
      tr.appendChild(tdA); tb.appendChild(tr);
    });

    // flagged sentence cards (phone layout)
    const fc = $("flagcards"); fc.textContent = "";
    const flagged = R.rows.map((r, i) => ({ r, i })).filter(({ r }) => (r.src && !r.excluded) || (r.ai != null && r.ai >= 0.6));
    if (!flagged.length) { const c = el("div", "flag-card"); c.style.background = "#E6FAD0"; c.appendChild(el("div", "tx", "Nothing flagged. No source overlap and no AI-like sentences.")); fc.appendChild(c); }
    flagged.forEach(({ r, i }) => {
      const isSrc = r.src && !r.excluded;
      const card = el("div", "flag-card"); card.style.background = isSrc ? r.src.tint : "#F0EAFF";
      const hd = el("div", "hd"); hd.append(el("span", "", `Sentence ${i + 1}`), el("span", "tag", isSrc ? `Source ${r.src.n}` : `AI ${Math.round(r.ai * 100)}%`));
      card.append(hd, el("div", "tx", label(r, i, 160))); fc.appendChild(card);
    });
  }

  function renderSources() {
    const list = $("sources"); list.textContent = "";
    if (!R.sources.length) {
      const row = el("div", "srcrow"), inn = el("div", "in");
      const b = el("b", "", "No matching sources"); b.style.fontWeight = "600";
      inn.append(b, el("div", "sub", R.hasPlag ? "No public source shares wording with this text. That is not proof of originality: private documents and paywalled pages cannot be searched." : "Plagiarism check was not run."));
      row.appendChild(inn); list.appendChild(row); return;
    }
    R.sources.forEach((s) => {
      const row = el("div", "srcrow");
      const badge = el("span", "n", String(s.n)); badge.style.background = s.color;
      const inn = el("div", "in"), hd = el("div", "hd"), b = el("b");
      const name = `${s.host} — ${(s.title || s.host).slice(0, 90)}`;
      if (s.http) { const a = el("a", "", name); a.href = s.url; a.target = "_blank"; a.rel = "noopener noreferrer"; b.appendChild(a); } else b.textContent = name;
      hd.append(b, el("span", "pct", B.pct(s.pct)));
      const sIdx = R.rows.map((r, i) => (r.src === s && !r.excluded ? i + 1 : null)).filter(Boolean);
      const where = sIdx.length ? `sentence${sIdx.length > 1 ? "s" : ""} ${sIdx.slice(0, 4).join(", ")}${sIdx.length > 4 ? "…" : ""}` : "";
      inn.append(hd, el("div", "sub", [s.kind, where, s.mainKind ? B.matchDesc[s.mainKind] : ""].filter(Boolean).join(" · ")));
      if (s.http) {
        const btn = el("button", "cite-btn", "Cite this source ▾"); btn.type = "button";
        const cites = el("div", "cites"), fmtBox = el("div", "fmt"), txt = el("div", "txt"), cp = el("button", "btn cp", "Copy citation"); cp.type = "button";
        let fmt = "APA";
        const draw = () => { fmtBox.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x.textContent === fmt))); txt.textContent = B.cite(s, fmt, R.date); };
        ["APA", "MLA", "Harvard", "IEEE"].forEach((f) => { const x = el("button", "", f); x.type = "button"; x.addEventListener("click", () => { fmt = f; draw(); }); fmtBox.appendChild(x); });
        btn.addEventListener("click", () => { cites.classList.toggle("open"); btn.setAttribute("aria-expanded", String(cites.classList.contains("open"))); draw(); });
        cp.addEventListener("click", () => B.copy(txt.textContent));
        cites.append(fmtBox, txt, cp); inn.append(btn, cites);
      }
      row.append(badge, inn); list.appendChild(row);
    });
  }

  function summaryText() {
    const parts = [];
    if (R.hasPlag) {
      if (!R.sources.length) parts.push("No sentence matches a public source that the search could reach.");
      else if (R.rows.length) {
        const exact = R.rows.filter((r) => r.src && !r.excluded && r.kind === "exact").length;
        const para = R.rows.filter((r) => r.src && !r.excluded && r.kind && r.kind !== "exact").length;
        const sIdx = R.rows.map((r, i) => (r.src && !r.excluded ? i + 1 : null)).filter(Boolean);
        parts.push(`${sIdx.length === 1 ? "Sentence" : "Sentences"} ${sIdx.slice(0, 5).join(", ")}${sIdx.length > 5 ? " and others" : ""} ${sIdx.length === 1 ? "matches" : "match"} ${plural(R.sources.length, "public source")}${exact ? ` (${exact} near-verbatim)` : ""}${para ? `, ${para} by lighter rewrite or meaning` : ""} — these need quotation marks and citations, or rewriting.`);
      } else parts.push(`${B.pct(R.similarity)} of the counted words match ${plural(R.sources.length, "public source")}. Matching passages need quotation marks and citations, or rewriting.`);
    }
    if (R.hasAi) {
      if (R.aiFlagged) parts.push(`${plural(R.aiFlagged, "sentence")} read${R.aiFlagged === 1 ? "s" : ""} as generic and evenly paced in the way language models write — worth a second look. This is a statistical estimate, not proof.`);
      else parts.push(`AI-writing likelihood is ${R.aiLevel.toLowerCase()} (${B.pct(R.aiPct)}${R.aiVerdict ? `, ${R.aiVerdict.toLowerCase()}` : ""}). This is a statistical estimate, not proof.`);
    }
    if (R.grammar) parts.push(R.grammar.open ? `${plural(R.grammar.open, "writing suggestion")} ${R.grammar.open === 1 ? "is" : "are"} open.` : "No grammar issues remain.");
    return parts.join(" ") || "This report has no completed checks.";
  }

  // ------------------------------------------------------------------ certificate / verification
  function renderCertificate() {
    const box = $("verify"), mk = $("mk"), check = $("verify-doc");
    check.hidden = mode !== "verified";
    if (mode === "verified") { showVerify(R.id); return; }
    if (!R.attestation?.payload) {
      box.textContent = mode === "account" ? "This saved scan has no signed result attached. Run the scan again to create a certificate." : "Run the scan again to get a signed result that can be certified.";
      mk.disabled = true; $("share").disabled = true; return;
    }
    box.textContent = "Create a signed copy of this result. Anyone with the link can check the scores and confirm a document matches, no account needed. The essay itself is not stored.";
  }
  async function save() {
    if (savedId) return savedId;
    if (!R.attestation?.payload) throw new Error("run the scan again to get a signed result");
    const r = await B.api("/api/reports", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ payload: R.attestation.payload, sig: R.attestation.sig, title: R.title }) });
    savedId = r.id; return savedId;
  }
  function showVerify(id) {
    const box = $("verify"); box.textContent = "";
    box.append("Verify at ", el("b", "", `${location.host}/v/${id}`), " — no account needed.");
    const mk = $("mk"); mk.disabled = false; mk.textContent = "Copy verification link"; mk.onclick = () => B.copy(`${location.origin}/v/${id}`);
  }
  $("mk").addEventListener("click", async () => { if (savedId) return; try { showVerify(await save()); B.toast("Certificate created"); } catch (e) { B.toast("Could not create it: " + e.message); } });
  $("share").addEventListener("click", async () => { try { const id = await save(); showVerify(id); B.copy(`${location.origin}/v/${id}`); } catch (e) { B.toast("Could not create a link: " + e.message); } });
  $("pdf").addEventListener("click", () => window.print());

  // Teacher's check: does this document match the text the engine signed?
  $("vd-check").addEventListener("click", async () => {
    const out = $("vd-result"), text = $("vd-text").value;
    if (!text.trim()) { out.className = "vd-result"; out.textContent = "Paste the document text first."; return; }
    const h = await B.textHash(text);
    const ok = h === R.textHash;
    out.className = "vd-result " + (ok ? "ok" : "bad");
    out.textContent = ok ? "Match. This is exactly the text that was scanned for this report." : "No match. This text differs from the one that was scanned (even one changed word breaks the match).";
  });
})();
