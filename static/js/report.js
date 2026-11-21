(() => {
  const $ = (id) => document.getElementById(id);
  let R = null, savedId = null;

  (async function boot() {
    const m = location.pathname.match(/^\/v\/([A-Za-z0-9-]+)/);
    if (m) {
      try { R = await B.api("/api/reports/" + m[1]); savedId = m[1]; $("back").href = "/"; $("back").textContent = "Home"; }
      catch { $("main").innerHTML = `<div class="gate"><div class="card"><h2>Report not found</h2><p>This verification link does not match a saved report on this server.</p><a class="btn btn-lime" href="/">Run a scan</a></div></div>`; return; }
    } else if (new URLSearchParams(location.search).get("scan")) {
      try { R = await B.api("/api/scans/" + new URLSearchParams(location.search).get("scan")); $("back").href = "/me"; $("back").textContent = "My writing"; $("back-sm").href = "/me"; }
      catch { location.replace("/login?next=" + encodeURIComponent(location.pathname + location.search)); return; }
    } else {
      R = B.store.get("report");
      if (!R) { location.replace("/check"); return; }
    }
    render();
    B.mountNav("reports");
  })();

  function render() {
    document.title = `${R.title} — Originality report — BurstAI`;
    $("eyebrow").textContent = savedId ? "ORIGINALITY REPORT · VERIFIED COPY" : "ORIGINALITY REPORT";
    $("title").textContent = R.title;
    $("meta").textContent = `${R.words.toLocaleString()} words · checked ${B.fmtDate(R.date)} · report ID ${R.id}`;

    // --- score cards
    if (R.hasPlag) {
      $("p-tag").textContent = `${R.sources.length} source${R.sources.length === 1 ? "" : "s"}`;
      $("p-big").textContent = B.pct(R.similarity);
      const bar = $("p-bar"); bar.innerHTML = "";
      R.sources.forEach((s) => { const d = document.createElement("div"); d.style.width = s.pct.toFixed(1) + "%"; d.style.background = s.color; bar.appendChild(d); });
    } else { $("p-tag").textContent = "not run"; $("p-big").textContent = "–"; }
    if (R.hasAi) {
      $("a-tag").textContent = R.aiLevel; $("a-big").textContent = B.pct(R.aiPct);
      $("a-sub").textContent = `${R.aiFlagged} of ${R.aiTotal} sentences flagged · ${R.aiConfidence} confidence`;
    } else { $("a-tag").textContent = "not run"; $("a-big").textContent = "–"; }
    if (R.grammar) {
      const kinds = Object.keys(R.grammar.kinds);
      $("g-tag").textContent = `${R.grammar.open} issue${R.grammar.open === 1 ? "" : "s"}`; $("g-big").textContent = String(R.grammar.open);
      $("g-sub").textContent = kinds.length ? kinds.join(" · ") : "Nothing left to fix";
    } else { $("g-tag").textContent = "not run"; $("g-big").textContent = "–"; }

    $("summary").textContent = summaryText();

    // --- table
    const tb = $("rows"); tb.innerHTML = "";
    R.rows.forEach((r, i) => {
      const src = r.src ? R.sources.find((s) => s.n === r.src) : null;
      const tr = document.createElement("tr");
      const ai = r.ai == null ? null : Math.round(r.ai * 100);
      tr.innerHTML = `<td class="n">${i + 1}</td><td class="tx">${B.esc(r.text.length > 140 ? r.text.slice(0, 140).trimEnd() + "…" : r.text)}${r.excluded ? ` <span class="srcpill none">(${r.excluded})</span>` : ""}</td>` +
        `<td>${src && !r.excluded ? `<span class="srcpill" style="background:${src.tint}">Source ${src.n}</span>` : `<span class="srcpill none">—</span>`}</td>` +
        `<td>${ai == null ? `<span class="srcpill none">—</span>` : `<div class="aibar"><div class="trk"><div class="${ai > 60 ? "hi" : ""}" style="width:${ai}%"></div></div><span>${ai}%</span></div>`}</td>`;
      tb.appendChild(tr);
    });

    // --- flagged sentence cards (phone layout)
    const fc = $("flagcards"); fc.innerHTML = "";
    const flagged = R.rows.map((r, i) => ({ r, i })).filter(({ r }) => (r.src && !r.excluded) || (r.ai != null && r.ai >= 0.6));
    if (!flagged.length) fc.innerHTML = `<div class="flag-card" style="background:#E6FAD0"><div class="tx">Nothing flagged. No source overlap and no AI-like sentences.</div></div>`;
    flagged.forEach(({ r, i }) => {
      const src = r.src ? R.sources.find((s) => s.n === r.src) : null;
      const isSrc = src && !r.excluded;
      const card = document.createElement("div"); card.className = "flag-card"; card.style.background = isSrc ? src.tint : "#F0EAFF";
      card.innerHTML = `<div class="hd"><span>Sentence ${i + 1}</span><span class="tag">${isSrc ? "Source " + src.n : "AI " + Math.round(r.ai * 100) + "%"}</span></div><div class="tx">${B.esc(r.text.length > 160 ? r.text.slice(0, 160).trimEnd() + "…" : r.text)}</div>`;
      fc.appendChild(card);
    });

    // --- sources + citations
    const list = $("sources"); list.innerHTML = "";
    if (!R.sources.length) list.innerHTML = `<div class="srcrow"><div class="in"><b style="font-weight:600">No matching sources</b><div class="sub">${R.hasPlag ? "Nothing on the open web shares wording with this text." : "Plagiarism check was not run."}</div></div></div>`;
    R.sources.forEach((s) => {
      const row = document.createElement("div"); row.className = "srcrow";
      const sIdx = R.rows.map((r, i) => (r.src === s.n && !r.excluded ? i + 1 : null)).filter(Boolean);
      const where = sIdx.length ? `sentence${sIdx.length > 1 ? "s" : ""} ${sIdx.slice(0, 4).join(", ")}${sIdx.length > 4 ? "…" : ""}` : "";
      row.innerHTML = `<span class="n" style="background:${s.color}">${s.n}</span><div class="in"><div class="hd"><b>${s.url.startsWith("http") ? `<a href="${B.esc(s.url)}" target="_blank" rel="noopener">` : ""}${B.esc(s.host)} — ${B.esc((s.title || s.host).slice(0, 90))}${s.url.startsWith("http") ? "</a>" : ""}</b><span class="pct">${B.pct(s.pct)}</span></div><div class="sub">${B.esc(s.kind)} · ${where ? where + ", " : ""}${B.matchDesc[s.mainKind]}</div><button type="button" class="cite-btn">Cite this source ▾</button><div class="cites"><div class="fmt"></div><div class="txt"></div><button type="button" class="btn cp" style="min-height:36px;padding:6px 12px;font-size:13px">Copy citation</button></div></div>`;
      const cites = row.querySelector(".cites"), fmtBox = row.querySelector(".fmt"), txt = row.querySelector(".txt");
      let fmt = "APA";
      const draw = () => { fmtBox.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.textContent === fmt))); txt.textContent = B.cite(s, fmt, R.date); };
      ["APA", "MLA", "Harvard", "IEEE"].forEach((f) => { const b = document.createElement("button"); b.type = "button"; b.textContent = f; b.addEventListener("click", () => { fmt = f; draw(); }); fmtBox.appendChild(b); });
      row.querySelector(".cite-btn").addEventListener("click", () => { cites.classList.toggle("open"); draw(); });
      row.querySelector(".cp").addEventListener("click", () => B.copy(txt.textContent));
      list.appendChild(row);
    });

    if (savedId) showVerify(savedId);
  }

  function summaryText() {
    const parts = [];
    if (R.hasPlag) {
      const exact = R.rows.filter((r) => r.src && !r.excluded && r.kind === "exact").length;
      const para = R.rows.filter((r) => r.src && !r.excluded && r.kind !== "exact").length;
      if (!R.sources.length) parts.push("No sentence matches a public source, so the wording appears to be the author's own.");
      else {
        const sIdx = R.rows.map((r, i) => (r.src && !r.excluded ? i + 1 : null)).filter(Boolean);
        parts.push(`${sIdx.length === 1 ? "Sentence" : "Sentences"} ${sIdx.slice(0, 5).join(", ")}${sIdx.length > 5 ? " and others" : ""} ${sIdx.length === 1 ? "matches" : "match"} ${R.sources.length} public source${R.sources.length === 1 ? "" : "s"}${exact ? ` (${exact} near-verbatim)` : ""}${para ? ` and ${para} by meaning` : ""} — these need quotation marks and citations, or rewriting.`);
      }
    }
    if (R.hasAi) {
      if (R.aiFlagged) parts.push(`${R.aiFlagged} sentence${R.aiFlagged === 1 ? "" : "s"} read${R.aiFlagged === 1 ? "s" : ""} as generic and evenly paced in the way language models write — worth rewriting in your own voice.`);
      else parts.push(`No sentence reads as machine-written; the text shows natural variation (${R.aiLevel.toLowerCase()} AI likelihood overall).`);
    }
    if (R.grammar) parts.push(R.grammar.open ? `${R.grammar.open} writing issue${R.grammar.open === 1 ? "" : "s"} remain${R.grammar.open === 1 ? "s" : ""} to fix.` : "No grammar issues remain.");
    return parts.join(" ") || "This report has no completed checks.";
  }

  async function save() {
    if (savedId) return savedId;
    const r = await B.api("/api/reports", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(R) });
    savedId = r.id; return savedId;
  }
  function showVerify(id) {
    const url = `${location.origin}/v/${id}`;
    $("verify").innerHTML = `Verify at <b>${B.esc(location.host)}/v/${B.esc(id)}</b> — no account needed.`;
    $("mk").textContent = "Copy verification link"; $("mk").onclick = () => B.copy(url);
  }
  $("mk").addEventListener("click", async () => { try { showVerify(await save()); B.toast("Certificate created"); } catch (e) { B.toast("Could not save: " + e.message); } }, { once: true });
  $("share").addEventListener("click", async () => { try { const id = await save(); showVerify(id); B.copy(`${location.origin}/v/${id}`); } catch (e) { B.toast("Could not save: " + e.message); } });
  $("pdf").addEventListener("click", () => window.print());
})();
