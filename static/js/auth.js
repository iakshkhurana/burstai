(() => {
  const $ = (id) => document.getElementById(id);
  const form = $("form"), err = $("err");
  let mode = location.pathname.endsWith("signup") ? "signup" : "login";
  // only ever redirect to a path on this site
  const rawNext = new URLSearchParams(location.search).get("next") || "/me";
  const next = /^\/(?!\/)[\w\-./?=&%]*$/.test(rawNext) ? rawNext : "/me";

  function switchTo(m) { history.replaceState(null, "", "/" + m + location.search); setMode(m); }
  function setMode(m) {
    mode = m;
    document.querySelectorAll(".mode").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.mode === m)));
    const signup = m === "signup";
    $("f-name").hidden = !signup;
    $("f-agree").hidden = !signup;
    $("h").textContent = signup ? "Create your free account" : "Welcome back";
    $("sub").textContent = signup ? "No card and no trial. Just a place for your reports." : "Log in to see your scans and certificates.";
    $("submit").textContent = signup ? "Create account" : "Log in";
    const sw = $("switch"); sw.textContent = signup ? "Already have an account? " : "New here? ";
    const a = document.createElement("a"); a.href = signup ? "/login" : "/signup"; a.textContent = signup ? "Log in" : "Create a free account";
    a.addEventListener("click", (e) => { e.preventDefault(); switchTo(signup ? "login" : "signup"); });
    sw.appendChild(a);
    form.password.autocomplete = signup ? "new-password" : "current-password";
    err.hidden = true;
  }
  document.querySelectorAll(".mode").forEach((b) => b.addEventListener("click", () => switchTo(b.dataset.mode)));
  setMode(mode);

  form.addEventListener("submit", async (e) => {
    e.preventDefault(); err.hidden = true;
    const body = { name: form.name.value, email: form.email.value.trim(), password: form.password.value };
    if (!body.email || body.password.length < 8) { err.textContent = "Enter your email and a password of at least 8 characters."; err.hidden = false; return; }
    $("submit").disabled = true;
    try {
      await B.api("/api/auth/" + mode, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      B.store.del("me");
      location.href = next;
    } catch (ex) { err.textContent = ex.message; err.hidden = false; $("submit").disabled = false; }
  });
  B.me().then((u) => { if (u) location.replace(next); });
})();
