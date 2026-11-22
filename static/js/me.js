(() => {
  const $ = (id) => document.getElementById(id);
  const DAY = 86400000;

  (async function boot() {
    const user = await B.me();
    let scans = [];
    if (user) {
      $("welcome").textContent = `Welcome back, ${user.name.split(" ")[0]}`;
      $("avatar").textContent = user.initial;
      try { scans = (await B.api("/api/scans")).map((s) => ({ ...s, date: s.created * 1000, sim: s.similarity, ai: s.ai, server: true })); } catch {}
    } else {
      $("welcome").textContent = "Scans on this device";
      $("avatar").textContent = "?"; $("avatar").title = "Log in to sync across devices";
      $("avatar").addEventListener("click", () => (location.href = "/login?next=/me"));
      $("logout").textContent = "Log in"; $("logout").addEventListener("click", () => (location.href = "/login?next=/me"), { once: true });
    }
    // logging out also wipes anything about the last scan kept in this tab (shared computers)
    const wipeSession = () => { try { sessionStorage.clear(); } catch {} };
    if (user) $("logout").addEventListener("click", async () => { await B.api("/api/auth/logout", { method: "POST" }).catch(() => {}); wipeSession(); location.href = "/"; });

    $("clear-device").addEventListener("click", () => {
      try { localStorage.removeItem("burstai.history"); localStorage.removeItem("burstai.gemini_key"); } catch {}
      wipeSession(); B.toast("History and saved key cleared from this device"); setTimeout(() => location.reload(), 700);
    });
    if (user) {
      const del = $("delete-account"), form = $("delete-form"), derr = $("delete-err");
      del.hidden = false;
      del.addEventListener("click", () => { form.hidden = false; del.hidden = true; form.password.focus(); });
      $("delete-cancel").addEventListener("click", () => { form.hidden = true; del.hidden = false; derr.hidden = true; });
      form.addEventListener("submit", async (e) => {
        e.preventDefault(); derr.hidden = true;
        try {
          await B.api("/api/auth/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: form.password.value }) });
          try { localStorage.removeItem("burstai.history"); } catch {}
          wipeSession(); location.href = "/";
        } catch (ex) { derr.textContent = ex.message; derr.hidden = false; }
      });
    }
    // merge local history (device) with server scans, newest first, de-duplicated by title+day
    const local = (B.store.local("history") || []).map((h) => ({ ...h, local: true }));
    const seen = new Set();
    const all = [...scans, ...local].sort((a, b) => b.date - a.date).filter((s) => { const k = s.title + "|" + new Date(s.date).toDateString(); if (seen.has(k)) return false; seen.add(k); return true; });
    render(all, user);
    B.mountNav("me");
  })();

  function render(all, user) {
    // streak: consecutive days (ending today or yesterday) with at least one scan
    const days = new Set(all.map((s) => Math.floor(s.date / DAY)));
    let streak = 0, d = Math.floor(Date.now() / DAY);
    if (!days.has(d)) d -= 1;
    while (days.has(d)) { streak++; d--; }
    $("streak").textContent = streak === 1 ? "1 day" : `${streak} days`;
    $("streak-sub").textContent = all.length ? `writing streak · ${all.length} scan${all.length === 1 ? "" : "s"} total` : "writing streak · run your first scan";
    const labels = ["M", "T", "W", "T", "F", "S", "S"];
    const today = new Date(); const dow = (today.getDay() + 6) % 7; // Monday = 0
    const wrap = $("days"); wrap.innerHTML = "";
    labels.forEach((l, i) => {
      const dayNum = Math.floor((Date.now() - (dow - i) * DAY) / DAY);
      const done = days.has(dayNum);
      const el = document.createElement("div"); el.innerHTML = `<span style="background:${done ? "#C6F432" : "transparent"};border-color:${done ? "#C6F432" : "#2C3A63"}"></span>${l}`;
      if (i > dow) el.style.opacity = ".45";
      wrap.appendChild(el);
    });

    const last = all.slice(0, 8).reverse();
    const bars = $("trend"); bars.innerHTML = "";
    if (last.length) {
      last.forEach((s, i) => { const b = document.createElement("div"); b.style.height = Math.max(6, 100 - (s.sim || 0)) + "%"; b.style.background = i === last.length - 1 ? "#C6F432" : "#DCE3FB"; b.title = `${s.title}: ${B.pct(100 - (s.sim || 0))} original`; bars.appendChild(b); });
      const first = 100 - (last[0].sim || 0), lastV = 100 - (last[last.length - 1].sim || 0);
      $("trend-note").textContent = last.length < 2 ? "Run a few more scans to see a trend." : lastV >= first ? "Original writing has gone up across your recent scans." : "Original writing dipped in your latest scan — check the sources panel.";
      $("trend-sub").textContent = `last ${last.length} scan${last.length === 1 ? "" : "s"}`;
    } else { $("trend-note").textContent = "No scans yet. Your originality trend will appear here."; }

    const list = $("recent"); list.innerHTML = "";
    if (!all.length) list.innerHTML = `<div class="empty">No scans yet. <a href="/">Run your first scan</a>${user ? "" : " or <a href='/login?next=/me'>log in</a> to see scans from other devices"}.</div>`;
    all.slice(0, 30).forEach((s) => {
      const a = document.createElement("a"); a.className = "scan-row"; a.href = s.server ? `/report?scan=${s.id}` : "/report";
      const tint = (s.sim || 0) >= 25 ? "#FFE6B3" : (s.sim || 0) >= 10 ? "#FFD3DC" : "#C6F1E0";
      a.innerHTML = `<span class="ico" style="background:${tint}"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#0E1B3D" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5"/></svg></span><span class="meta"><span class="t">${B.esc(s.title)}</span><span class="w">${when(s.date)} · AI ${s.ai == null ? "–" : B.pct(s.ai)}${s.server ? "" : " · this device"}</span></span><span class="sim" style="color:${(s.sim || 0) >= 25 ? "#B4231F" : "#0E1B3D"}">${B.pct(s.sim || 0)}</span>`;
      if (!s.server) a.addEventListener("click", (e) => { if (!B.store.get("report")) { e.preventDefault(); B.toast("Only the latest scan on this device can be reopened. Log in to keep all of them."); } });
      list.appendChild(a);
    });
  }

  function when(t) {
    const diff = Date.now() - t;
    if (diff < DAY && new Date(t).toDateString() === new Date().toDateString()) return "Today";
    if (diff < 2 * DAY) return "Yesterday";
    if (diff < 7 * DAY) return new Date(t).toLocaleDateString("en-GB", { weekday: "short" });
    return new Date(t).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
  }
})();
