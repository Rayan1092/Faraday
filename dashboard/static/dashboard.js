// Faraday dashboard. Polls /api/state about once a second and redraws.
// Fully offline, no libraries. Everything from the log goes in with textContent
// (never innerHTML). innerHTML is only used for the fixed icon shapes below.
"use strict";

(function () {
  const POLL_MS = 1000;
  const $ = (id) => document.getElementById(id);
  let lastVersion;
  let lastPhotoKey = null;
  let toastKey = null;

  // ---- thin-line icons (24x24, drawn here, no icon library) ----------------

  const ICONS = {
    lock: '<rect x="5" y="11" width="14" height="9" rx="2.5"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    unlock: '<rect x="5" y="11" width="14" height="9" rx="2.5"/><path d="M8 11V8a4 4 0 0 1 7.6-1.7"/>',
    door: '<path d="M6 21V4.5A1.5 1.5 0 0 1 7.5 3h9A1.5 1.5 0 0 1 18 4.5V21"/><path d="M4 21h16"/><path d="M14.5 12.5v.01"/>',
    motion: '<path d="M8.5 8.5a5 5 0 0 0 0 7"/><path d="M5.6 5.6a9 9 0 0 0 0 12.8"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M18.4 5.6a9 9 0 0 1 0 12.8"/><circle cx="12" cy="12" r="1.3"/>',
    bulb: '<path d="M9.5 18h5"/><path d="M10.5 21h3"/><path d="M12 3a6 6 0 0 0-3.6 10.8c.7.6 1.1 1.4 1.1 2.2h5c0-.8.4-1.6 1.1-2.2A6 6 0 0 0 12 3z"/>',
    camera: '<rect x="3" y="7" width="18" height="13" rx="3"/><path d="M8.5 7l1.4-2.5h4.2L15.5 7"/><circle cx="12" cy="13.5" r="3.4"/>',
    mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0"/><path d="M12 17.5V21"/>',
    speaker: '<path d="M4 9.5v5h3.5L12 18V6L7.5 9.5H4z"/><path d="M15.5 9a4.2 4.2 0 0 1 0 6"/><path d="M18 6.5a8 8 0 0 1 0 11"/>',
    shield: '<path d="M12 3l7 3v5.2c0 4.6-3 8.2-7 9.8-4-1.6-7-5.2-7-9.8V6l7-3z"/>',
    alert: '<path d="M12 4l8.5 15h-17L12 4z"/><path d="M12 10v4"/><path d="M12 16.8v.01"/>',
    message: '<path d="M4.5 5.5h15a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H9l-4.5 3.5V6.5a1 1 0 0 1 1-1z"/>',
    rule: '<path d="M5 7h9"/><path d="M5 12h14"/><path d="M5 17h6"/><circle cx="17.5" cy="7" r="1.5"/><circle cx="14.5" cy="17" r="1.5"/>',
    activity: '<path d="M3 12h4l2.5-6 5 12 2.5-6h4"/>',
    more: '<circle cx="6" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="18" cy="12" r="1.2"/>',
    close: '<path d="M7 7l10 10"/><path d="M17 7L7 17"/>',
  };

  function setIcon(el, name) {
    el.classList.add("ico");
    el.dataset.icon = name;
    el.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true">' + (ICONS[name] || "") + "</svg>";
  }

  function icon(name) {
    const span = document.createElement("span");
    setIcon(span, name);
    return span;
  }

  document.querySelectorAll("[data-icon]").forEach((el) => setIcon(el, el.dataset.icon));

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

  function sentence(title) {
    // "THE AI CAN'T DISARM" -> "The AI can't disarm"
    const s = String(title || "").toLowerCase();
    return (s.charAt(0).toUpperCase() + s.slice(1)).replace(/\bai\b/g, "AI");
  }

  // ---- render ----------------------------------------------------------------

  const ALERT_TEXT = { normal: "All clear", warning: "Warning", alarm: "Alarm" };

  function render(s) {
    document.body.dataset.status = s.status.color;
    document.body.dataset.alert = s.alert;
    $("mode").textContent = sentence(s.mode);
    $("alert").textContent = ALERT_TEXT[s.alert] || sentence(s.alert);
    const ringIcon = $("ring-icon");
    const want = s.mode === "home" ? "unlock" : "lock";
    if (ringIcon.dataset.icon !== want) setIcon(ringIcon, want);
    for (const m of ["away", "night", "home"]) {
      $("btn-" + m).classList.toggle("active", s.mode === m);
    }
    $("btn-talk").hidden = !s.talk_available;

    renderNet(s.network);
    renderToast(s.banner);
    renderPhoto(s.photo);
    renderLights(s.lights, s.control_available);
    if (s.version === lastVersion) return; // the log didn't change
    lastVersion = s.version;
    renderTalk(s.talk);
    renderFeed(s.feed);
    renderRules(s.rules);
    renderNotes(s.summary, s.messages);
  }

  function renderNet(network) {
    // A real check from the server (no packets sent): red if any network is up.
    const online = network && network.offline === false;
    $("net-text").textContent = online ? "ONLINE · WI-FI ON" : "OFFLINE";
    $("net").classList.toggle("online", online);
    $("net").title = (network && network.detail) || "";
  }

  function renderToast(banner) {
    const toast = $("toast");
    if (!banner) {
      toast.classList.remove("show");
      document.body.classList.remove("toast-on");
      toastKey = null;
      return;
    }
    const key = banner.kind + "|" + banner.title + "|" + banner.text;
    if (key === toastKey) return;
    toastKey = key;
    $("toast-title").textContent = sentence(banner.title);
    $("toast-text").textContent = banner.text;
    toast.className = "toast " + banner.kind;
    // next frame, so the slide-in plays even when the kind changes
    requestAnimationFrame(() => {
      toast.classList.add("show");
      // make room in the activity panel, so no entry sits half-hidden under the toast
      document.body.style.setProperty("--toast-h", toast.offsetHeight + "px");
      document.body.classList.add("toast-on");
    });
  }

  function renderPhoto(photo) {
    const key = photo ? (photo.url || "") + "|" + photo.time : "";
    if (photo) {
      $("photo-text").textContent = photo.description || "No description.";
      $("photo-meta").textContent = photo.time + (photo.reason ? " · " + photo.reason : "");
    }
    renderThreat(photo && photo.threat);
    if (key === lastPhotoKey) return;
    lastPhotoKey = key;
    if (!photo || !photo.url) return showPhoto(null);
    const src = photo.url + "?t=" + Date.now();
    const probe = new Image();
    probe.onload = () => showPhoto(src);
    probe.onerror = () => showPhoto(null);
    probe.src = src;
  }

  function renderThreat(threat) {
    const box = $("threat");
    box.replaceChildren();
    box.className = "threat" + (threat ? " cat-" + threat.category : "");
    if (!threat) return;
    const pill = el("span", "threat-pill");
    pill.append(el("span", "threat-dot"), el("span", "threat-name", threat.name));
    box.append(pill);
    if (threat.reason) box.append(el("span", "threat-reason", threat.reason));
  }

  function showPhoto(src) {
    const img = $("photo");
    const backdrop = $("backdrop");
    if (src) {
      img.src = src;
      img.hidden = false;
      $("photo-empty").hidden = true;
      backdrop.style.backgroundImage = 'url("' + src.replace(/["\\]/g, "") + '")';
      backdrop.classList.add("on");
    } else {
      img.hidden = true;
      img.removeAttribute("src");
      $("photo-empty").hidden = false;
      backdrop.classList.remove("on");
    }
  }

  // Light switches. The tile always shows the state from the log (what integration
  // really sent), never what the click hoped for. Tiles are updated in place so a
  // poll doesn't steal keyboard focus from a switch.
  const lightTiles = {};
  const lightBusy = {};   // outlet -> true while a switch request is in flight
  const lightNote = {};   // outlet -> { text, until } short error under the name

  function lightTile(light) {
    let tile = lightTiles[light.id];
    if (!tile) {
      tile = el("button", "light");
      tile.type = "button";
      tile.dataset.outlet = light.id;
      const words = el("div", "words");
      words.append(el("div", "name", light.name), el("div", "state"));
      tile.append(icon("bulb"), words, el("span", "switch"));
      tile.onclick = () => switchLight(light.id, !tile.classList.contains("on"));
      lightTiles[light.id] = tile;
    }
    return tile;
  }

  function renderLights(lights, canSwitch) {
    const box = $("lights");
    const now = Date.now();
    const shown = [];
    for (const light of lights) {
      if (light.id === "spare" && !light.on) continue; // only show the spare when it's on
      const tile = lightTile(light);
      const note = lightNote[light.id];
      const busy = !!lightBusy[light.id];
      tile.classList.toggle("on", light.on);
      tile.classList.toggle("busy", busy);
      tile.disabled = !canSwitch || busy;
      tile.classList.toggle("switchable", !!canSwitch);
      tile.setAttribute("aria-pressed", light.on ? "true" : "false");
      tile.setAttribute("aria-label", light.name + (light.on ? " on" : " off") +
        (canSwitch ? "" : " (switches not connected)"));
      tile.querySelector(".state").textContent =
        busy ? "Switching…" : note && note.until > now ? note.text : light.on ? "On" : "Off";
      shown.push(tile);
    }
    if (shown.length !== box.children.length || shown.some((t, i) => box.children[i] !== t)) {
      box.replaceChildren(...shown);
    }
  }

  function switchLight(outlet, on) {
    if (lightBusy[outlet]) return;
    lightBusy[outlet] = true;
    delete lightNote[outlet];
    poll();
    post("/api/control", { outlet, on })
      .then(({ status, data }) => {
        if (status !== 200) lightNote[outlet] = { text: data.error || "Didn't switch", until: Date.now() + 4000 };
      })
      .catch(() => { lightNote[outlet] = { text: "App not responding", until: Date.now() + 4000 }; })
      .finally(() => { delete lightBusy[outlet]; poll(); });
  }

  let lastTalk = [];
  const TALK_MAX = 3;

  function renderTalk(talk) {
    lastTalk = talk;
    const list = $("talk");
    list.replaceChildren();
    if (!talk.length) list.append(el("li", "empty", "Nothing yet."));
    for (const t of talk.slice(-TALK_MAX)) {
      const li = el("li", t.who);
      const who = el("div", "who", t.who === "heard" ? "Heard" : "Faraday");
      who.append(el("time", "", t.time));
      li.append(icon(t.who === "heard" ? "mic" : "speaker"), who, el("div", "line", t.text));
      list.append(li);
    }
    // Only whole entries: drop the oldest until the rest fit, so nothing is cut in half.
    while (list.children.length > 1 && list.scrollHeight > list.clientHeight + 1) {
      list.firstElementChild.remove();
    }
  }
  window.addEventListener("resize", () => renderTalk(lastTalk));

  const FEED_ICON = {
    doorbell: "door", motion: "motion", speech_heard: "mic", photo_described: "camera",
    mode_changed: "shield", alert_changed: "alert", speak: "speaker", outlet_on: "bulb",
    outlet_off: "bulb", take_photo: "camera", save_message: "message", add_rule: "rule",
    summarize_log: "activity", set_mode: "shield",
  };

  function renderFeed(feed) {
    const list = $("feed");
    list.replaceChildren();
    if (!feed.length) list.append(el("li", "empty", "Waiting for the first event."));
    for (const f of feed) {
      const li = el("li", f.level + " " + f.kind);
      const node = el("span", "node");
      node.append(icon(FEED_ICON[f.type] || "activity"));
      const text = el("span", "text", f.text);
      if (f.detail) text.append(el("span", "detail", f.detail));
      li.append(node, text, el("time", "", f.time));
      list.append(li);
    }
  }

  function renderRules(rules) {
    const list = $("rules");
    list.replaceChildren();
    if (!rules.length) list.append(el("li", "empty", "Say \u201cWhen there's motion at night, turn on the lamp.\u201d"));
    for (const r of rules) {
      const li = el("li");
      const del = el("button", "rule-del");
      del.type = "button";
      del.title = "Delete rule";
      del.setAttribute("aria-label", "Delete rule");
      del.append(icon("close"));
      del.onclick = () => post("/api/rules/delete", { id: r.id }).then(poll);
      li.append(el("span", "rule-text", r.text), del);
      if (r.description) li.append(el("span", "rule-said", "\u201c" + r.description + "\u201d"));
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

  // ---- polling -----------------------------------------------------------------

  function poll() {
    return fetch("/api/state", { cache: "no-store" })
      .then((r) => r.json())
      .then((s) => {
        $("link").textContent = "Live";
        $("link").classList.remove("down");
        render(s);
      })
      .catch(() => {
        $("link").textContent = "App not responding";
        $("link").classList.add("down");
      });
  }

  // ---- controls ------------------------------------------------------------------

  $("btn-away").onclick = () => post("/api/mode", { mode: "away" }).then(poll);
  $("btn-night").onclick = () => post("/api/mode", { mode: "night" }).then(poll);

  const dialog = $("home-dialog");
  const pass = $("home-pass");
  function closeDialog() { dialog.hidden = true; pass.value = ""; $("home-error").textContent = ""; }
  $("btn-home").onclick = () => {
    if (document.body.dataset.status === "green" && $("btn-home").classList.contains("active")) return;
    dialog.hidden = false; pass.value = ""; pass.focus();
  };
  $("home-cancel").onclick = closeDialog;
  dialog.addEventListener("click", (e) => { if (e.target === dialog) closeDialog(); });
  $("home-form").onsubmit = (e) => {
    e.preventDefault();
    const attempt = pass.value;
    pass.value = ""; // never keep it around
    post("/api/home", { passphrase: attempt }).then(({ data }) => {
      if (data.ok) { closeDialog(); poll(); }
      else { $("home-error").textContent = data.error || "Wrong passphrase."; pass.focus(); }
    });
  };

  const backup = $("backup");
  const backupBtn = $("btn-backup");
  function setBackup(open) { backup.hidden = !open; backupBtn.setAttribute("aria-expanded", String(open)); }
  backupBtn.onclick = (e) => { e.stopPropagation(); setBackup(backup.hidden); };
  document.addEventListener("click", (e) => {
    if (!backup.hidden && !backup.contains(e.target) && e.target !== backupBtn) setBackup(false);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { setBackup(false); closeDialog(); }
  });

  $("btn-talk").onclick = () => post("/api/talk", {});
  $("btn-ring").onclick = () => post("/api/sensor", { type: "doorbell" }).then(poll);
  $("btn-motion").onclick = () => post("/api/sensor", { type: "motion" }).then(poll);

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
