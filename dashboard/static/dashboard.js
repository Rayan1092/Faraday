// Faraday dashboard. Polls /api/state about once a second and redraws.
// Fully offline, no libraries. All text goes in with textContent (never innerHTML),
// so nothing from the log can inject markup.
"use strict";

(function () {
  const POLL_MS = 1000;
  const $ = (id) => document.getElementById(id);
  let lastVersion = undefined;
  let lastPhotoUrl = null;

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function post(path, body) {
    return fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Faraday": "1" },
      body: JSON.stringify(body || {}),
    }).then((r) => r.json().then((data) => ({ status: r.status, data })));
  }

  // ---- render --------------------------------------------------------------

  function render(s) {
    document.body.dataset.status = s.status.color;
    $("mode").textContent = s.mode.toUpperCase();
    $("alert").textContent = s.alert === "normal" ? "" : "⚠ " + s.alert.toUpperCase();
    for (const m of ["away", "night", "home"]) {
      $("btn-" + m).classList.toggle("active", s.mode === m);
    }

    const notice = $("notice");
    if (s.alert === "alarm") {
      notice.textContent = "ALARM: motion again within a minute";
      notice.className = "notice alarm";
      notice.hidden = false;
    } else if (s.notice) {
      notice.textContent = s.notice.text + " (" + s.notice.time + ")";
      notice.className = "notice";
      notice.hidden = false;
    } else {
      notice.hidden = true;
    }

    renderPhoto(s.photo);
    renderLights(s.lights);
    if (s.version === lastVersion) return; // the log didn't change
    lastVersion = s.version;
    renderTalk(s.talk);
    renderFeed(s.feed);
    renderRules(s.rules);
    renderNotes(s.summary, s.messages);
  }

  function renderPhoto(photo) {
    if (!photo) return;
    if (photo.url !== lastPhotoUrl) {
      lastPhotoUrl = photo.url;
      const img = $("photo");
      img.onerror = () => { img.onerror = null; img.src = "/static/no-photo.svg"; };
      img.src = photo.url ? photo.url + "?t=" + Date.now() : "/static/no-photo.svg";
    }
    $("photo-text").textContent = photo.description || "No description.";
    $("photo-meta").textContent = photo.time + (photo.reason ? " · " + photo.reason : "");
  }

  function renderLights(lights) {
    const box = $("lights");
    box.replaceChildren();
    for (const light of lights) {
      if (light.id === "spare" && !light.on) continue; // only show the spare when it's on
      const row = el("div", "light" + (light.on ? " on" : ""));
      row.append(el("span", "name", light.name), el("span", "state", light.on ? "ON" : "OFF"));
      box.append(row);
    }
  }

  function renderTalk(talk) {
    const list = $("talk");
    list.replaceChildren();
    if (!talk.length) list.append(el("li", "empty", "Nothing yet."));
    for (const t of talk) {
      const li = el("li", t.who);
      li.append(el("span", "who", (t.who === "heard" ? "Heard " : "Faraday said ") + t.time),
                document.createTextNode(t.text));
      list.append(li);
    }
    // The list is bottom-aligned in CSS, so the newest line is always visible.
  }

  function renderFeed(feed) {
    const list = $("feed");
    list.replaceChildren();
    for (const f of feed) {
      const li = el("li", f.level + " " + f.kind);
      li.append(el("span", "time", f.time), el("span", "text", f.text));
      list.append(li);
    }
  }

  function renderRules(rules) {
    const list = $("rules");
    list.replaceChildren();
    if (!rules.length) list.append(el("li", "empty", 'Say "When there\'s motion at night, turn on the lamp."'));
    for (const r of rules) {
      const li = el("li");
      const del = el("button", "btn small", "✕");
      del.type = "button";
      del.title = "Delete rule";
      del.onclick = () => post("/api/rules/delete", { id: r.id }).then(poll);
      li.append(del, el("span", "rule-text", r.text));
      if (r.description) li.append(el("span", "said", "“" + r.description + "”"));
      list.append(li);
    }
  }

  function renderNotes(summary, messages) {
    const box = $("summary");
    box.hidden = !summary;
    if (summary) box.textContent = summary.text;
    $("notes-title").textContent = summary ? "Summary & messages" : "Messages";
    const list = $("messages");
    list.replaceChildren();
    if (!messages.length) list.append(el("li", "empty", "No messages."));
    for (const m of messages) {
      const li = el("li", "", m.text);
      li.append(el("span", "meta", m.sender + " · " + m.time));
      list.append(li);
    }
  }

  // ---- polling -------------------------------------------------------------

  function poll() {
    return fetch("/api/state", { cache: "no-store" })
      .then((r) => r.json())
      .then((s) => {
        $("link").textContent = "live";
        $("link").classList.remove("down");
        render(s);
      })
      .catch(() => {
        $("link").textContent = "app not responding";
        $("link").classList.add("down");
      });
  }

  // ---- buttons -------------------------------------------------------------

  $("btn-away").onclick = () => post("/api/mode", { mode: "away" }).then(poll);
  $("btn-night").onclick = () => post("/api/mode", { mode: "night" }).then(poll);

  const dialog = $("home-dialog");
  const pass = $("home-pass");
  function closeDialog() { dialog.hidden = true; pass.value = ""; $("home-error").textContent = ""; }
  $("btn-home").onclick = () => { dialog.hidden = false; pass.value = ""; pass.focus(); };
  $("home-cancel").onclick = closeDialog;
  dialog.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDialog(); });
  $("home-form").onsubmit = (e) => {
    e.preventDefault();
    const attempt = pass.value;
    pass.value = ""; // never keep it around
    post("/api/home", { passphrase: attempt }).then(({ data }) => {
      if (data.ok) { closeDialog(); poll(); }
      else { $("home-error").textContent = data.error || "Wrong passphrase."; pass.focus(); }
    });
  };

  $("say-form").onsubmit = (e) => {
    e.preventDefault();
    const box = $("say-text");
    const text = box.value.trim();
    if (!text) return;
    box.value = "";
    post("/api/say", { text }).then(poll);
  };

  poll();
  setInterval(poll, POLL_MS);
})();
