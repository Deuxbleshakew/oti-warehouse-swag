/* Otiwarehouse 1.11 — runs before the Badge Studio bundle.
   - fixes the bundle's "process is not defined" crash (blank page)
   - stores each event's badge project on the Otiwarehouse server instead of
     only in this browser (the bundle calls window.__OTI_BADGE_API)
   - adds the Otiwarehouse bar: event picker, backup / restore, save status
   - follows the site's theme and accent colour */
window.process = window.process || { env: { NODE_ENV: "production" } };
(function () {
  "use strict";
  var params = new URLSearchParams(location.search);
  var scope = params.get("scope") || "shared";
  var embed = params.get("embed") === "1";
  var hash = new URLSearchParams(location.hash.slice(1));
  var token = hash.get("token");
  if (token) {
    try { sessionStorage.setItem("oti_token", token); } catch (e) {}
    history.replaceState(null, "", location.pathname + location.search);   // keep the token out of history
  }
  if (!token) { try { token = sessionStorage.getItem("oti_token"); } catch (e) {} }
  var base = location.origin;
  var nativeFetch = window.fetch.bind(window);
  var lastRevision = 0;
  document.documentElement.dataset.otiTheme = "dark";
  try {
    var th = localStorage.getItem("oti_theme");
    if (th === "light") document.documentElement.dataset.otiTheme = "light";
  } catch (e) {}

  function status(text, kind) {
    var el = document.getElementById("oti-status");
    if (el) { el.textContent = text; el.className = "oti-status " + (kind || ""); }
  }
  function docUrl() { return base + "/badge-studio/document?scope=" + encodeURIComponent(scope); }

  window.__OTI_BADGE_API = async function (input, init, defaultDoc) {
    if (!token) return Response.json({ error: "Sign in to Otiwarehouse first, then open Badge Studio again." }, { status: 401 });
    var method = ((init && init.method) || "GET").toUpperCase();
    var headers = { Authorization: "Bearer " + token };
    try {
      if (method !== "PUT") {
        var r = await nativeFetch(docUrl(), { headers: headers, cache: "no-store" });
        var body = await r.json().catch(function () { return {}; });
        if (!r.ok) return Response.json({ error: body.detail || "Unable to load badges." }, { status: r.status });
        lastRevision = body.revision || 0;
        // A brand-new event starts empty instead of with the bundle's sample people.
        var doc = body.document || Object.assign({}, defaultDoc, { attendees: [] });
        status(body.document ? "Saved on Otiwarehouse" : "New badge project — saves automatically", "ok");
        return Response.json({ document: doc, revision: lastRevision });
      }
      headers["Content-Type"] = "application/json";
      status("Saving…");
      var r2 = await nativeFetch(docUrl(), { method: "PUT", headers: headers, body: init.body });
      var body2 = await r2.json().catch(function () { return {}; });
      if (!r2.ok) { status(body2.detail || "Not saved", "err"); return Response.json({ error: body2.detail || "Could not save badges." }, { status: r2.status }); }
      lastRevision = body2.revision;
      status("Saved " + new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }), "ok");
      return Response.json({ revision: body2.revision });
    } catch (err) {
      status("Offline — changes not saved", "err");
      return Response.json({ error: "Couldn't reach Otiwarehouse. Check your connection." }, { status: 503 });
    }
  };

  async function backup() {
    var r = await nativeFetch(docUrl(), { headers: { Authorization: "Bearer " + token }, cache: "no-store" });
    var body = await r.json();
    if (!body.document) { status("Nothing saved yet for this event.", "err"); return; }
    var blob = new Blob([JSON.stringify({ format: "Badge Studio Portable", version: 1, document: body.document }, null, 2)], { type: "application/json" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    var label = (document.getElementById("oti-scope") || {}).selectedOptions;
    var name = label && label[0] ? label[0].textContent.split(" · ")[0] : "Badge-Studio-Project";
    a.download = name.replace(/[^\w\- ]+/g, "").trim().replace(/\s+/g, "-") + ".json";
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 1500);
    status("Backup downloaded.", "ok");
  }
  async function restore(file) {
    try {
      if (file.size > 2e6) throw new Error("The project backup is too large.");
      var data = JSON.parse(await file.text());
      if (data.format !== "Badge Studio Portable" || data.version !== 1 || !data.document || !Array.isArray(data.document.attendees))
        throw new Error("Choose a Badge Studio project backup (.json).");
      if (!confirm("Replace this event's badges with the backup? This can't be undone — download a backup first if you need the current badges.")) return;
      var cur = await (await nativeFetch(docUrl(), { headers: { Authorization: "Bearer " + token }, cache: "no-store" })).json();
      var r = await nativeFetch(docUrl(), { method: "PUT", headers: { Authorization: "Bearer " + token, "Content-Type": "application/json" },
        body: JSON.stringify({ document: data.document, revision: cur.revision || 0 }) });
      if (!r.ok) throw new Error(((await r.json().catch(function () { return {}; })).detail) || "Could not restore.");
      location.reload();
    } catch (e) { status(e.message, "err"); }
  }

  function applyTheme(msg) {
    if (!msg) return;
    document.documentElement.dataset.otiTheme = msg.theme === "light" ? "light" : "dark";
    Object.keys(msg.vars || {}).forEach(function (k) { if (msg.vars[k]) document.documentElement.style.setProperty("--c-" + k.replace(/^--/, ""), msg.vars[k]); });
  }
  window.addEventListener("message", function (e) { if (e.origin === location.origin && e.data && e.data.type === "oti-theme") applyTheme(e.data); });
  try { if (window.parent !== window) window.parent.postMessage({ type: "oti-request-theme" }, location.origin); } catch (e) {}
  // Full-page mode: pick up the user's accent saved by the main site.
  try {
    var acc = localStorage.getItem("oti_accent");
    if (acc && /^#[0-9a-f]{6}$/i.test(acc)) document.documentElement.style.setProperty("--c-accent", acc);
  } catch (e) {}

  document.addEventListener("DOMContentLoaded", function () {
    if (embed) document.documentElement.classList.add("oti-embed");
    var bar = document.getElementById("oti-bar");
    if (!token) {
      document.getElementById("root").innerHTML = '<div class="oti-signin"><h2>Sign in to Otiwarehouse</h2><p>Open Badge Studio from the Otiwarehouse site so your badges save to your account.</p><p><a href="/" style="color:inherit">Go to Otiwarehouse</a></p></div>';
    }
    document.getElementById("oti-backup").onclick = function () { backup().catch(function (e) { status(e.message, "err"); }); };
    var file = document.getElementById("oti-restore-file");
    document.getElementById("oti-restore").onclick = function () { file.click(); };
    file.onchange = function () { if (file.files[0]) restore(file.files[0]); file.value = ""; };
    var sel = document.getElementById("oti-scope");
    if (!embed && token && sel) {
      nativeFetch(base + "/badge-studio/scopes", { headers: { Authorization: "Bearer " + token } }).then(function (r) { return r.json(); }).then(function (rows) {
        sel.innerHTML = rows.map(function (s) {
          var o = document.createElement("option"); o.value = s.scope; o.textContent = s.name + (s.event_date ? " · " + s.event_date : ""); if (s.scope === scope) o.setAttribute("selected", ""); return o.outerHTML;
        }).join("");
      }).catch(function () {});
      sel.onchange = function () { location.search = "?scope=" + encodeURIComponent(sel.value); };
    }
    if (bar) bar.hidden = false;
  });
})();
