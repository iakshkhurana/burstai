(() => {
  const $ = (id) => document.getElementById(id);
  const ta = $("essay"), cta = $("cta"), count = $("count"), box = $("scan");
  const CTA = { plag: "Check plagiarism", ai: "Detect AI", grammar: "Fix grammar" };
  let mode = "plag";

  function setMode(m) {
    mode = CTA[m] ? m : "plag";
    document.querySelectorAll(".mode").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.mode === mode)));
    cta.textContent = CTA[mode];
  }
  document.querySelectorAll(".mode").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));
  // Scroll the *page* to the scan box (never the hero card itself) and focus the textarea.
  function goScan(e, m) {
    if (e) e.preventDefault();
    if (m) setMode(m);
    const top = box.getBoundingClientRect().top + window.scrollY - 24;
    window.scrollTo({ top, behavior: "smooth" });
    setTimeout(() => ta.focus({ preventScroll: true }), 450);
  }
  document.querySelectorAll('a[href="#scan"]').forEach((a) => a.addEventListener("click", (e) => goScan(e, a.dataset.mode)));
  if (location.hash === "#scan") setTimeout(() => goScan(null, null), 50);

  ta.addEventListener("input", () => { count.textContent = `${B.words(ta.value).toLocaleString()} / no limit words`; });

  // --- upload + drag & drop
  $("upload").addEventListener("click", () => $("file").click());
  $("file").addEventListener("change", (e) => { if (e.target.files[0]) loadFile(e.target.files[0]); e.target.value = ""; });
  ["dragenter", "dragover"].forEach((ev) => box.addEventListener(ev, (e) => { e.preventDefault(); box.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((ev) => box.addEventListener(ev, (e) => { e.preventDefault(); box.classList.remove("drag"); }));
  box.addEventListener("drop", (e) => { const f = e.dataTransfer?.files?.[0]; if (f) loadFile(f); });
  async function loadFile(f) {
    cta.disabled = true; cta.textContent = "Reading file…";
    try {
      const fd = new FormData(); fd.append("file", f);
      const r = await B.api("/api/extract", { method: "POST", body: fd });
      ta.value = r.text; ta.dispatchEvent(new Event("input"));
      B.toast(`Loaded ${f.name}`);
    } catch (err) { B.toast("Could not read file: " + err.message); }
    cta.disabled = false; cta.textContent = CTA[mode];
  }

  // --- submit
  box.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = ta.value.trim();
    if (!text) { B.toast("Paste some text first — or try the sample from the banner."); ta.focus(); return; }
    if (B.words(text) < 20) { B.toast("Add at least 20 words for a meaningful scan."); ta.focus(); return; }
    B.store.set("text", text); B.store.del("result");
    location.href = "/check?mode=" + mode;
  });

  // mobile menu + auth state
  const menu = $("menu"), nav = document.querySelector(".nav");
  menu.addEventListener("click", () => { const open = nav.classList.toggle("open"); menu.setAttribute("aria-expanded", String(open)); });
  nav.querySelectorAll("nav a").forEach((a) => a.addEventListener("click", () => { nav.classList.remove("open"); menu.setAttribute("aria-expanded", "false"); }));
  B.paintAuth();

  // --- personalise the dashboard from local history
  const hist = B.store.local("history") || [];
  if (hist.length) {
    const recent = $("recent"); recent.innerHTML = "";
    hist.slice(0, 3).forEach((h) => {
      const d = document.createElement("div");
      d.innerHTML = `<span>${B.esc(h.title)}</span><span class="${h.sim >= 25 ? "hi" : ""}">${B.pct(h.sim)}</span>`;
      recent.appendChild(d);
    });
    const bars = $("bars"); bars.innerHTML = "";
    hist.slice(0, 8).reverse().forEach((h) => { const d = document.createElement("div"); d.style.height = Math.max(6, 100 - h.sim) + "%"; bars.appendChild(d); });
    $("bars-note").textContent = `${hist.length} scan${hist.length === 1 ? "" : "s"} on this device`;
    $("streak").textContent = `${hist.length} scan${hist.length === 1 ? "" : "s"} so far`;
    $("cert-title").textContent = hist[0].title; $("cert-sim").textContent = B.pct(hist[0].sim); $("cert-ai").textContent = hist[0].ai == null ? "–" : B.pct(hist[0].ai);
  }

  const q = new URLSearchParams(location.search).get("mode"); if (q) setMode(q);
})();
