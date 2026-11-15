// Faraday dashboard. Polls /api/state about once a second and redraws.
// Fully offline, no libraries. Everything from the log goes in with textContent
// (never innerHTML). innerHTML is only used for the fixed icon shapes (icons.js).
"use strict";

(function () {
  const POLL_MS = 1000;
  const $ = (id) => document.getElementById(id);
  let lastVersion;
  let lastPhotoKey = null;
  let toastKey = null;

  const { setIcon, icon } = window.FaradayIcons;   // static/icons.js

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
    // "DISARM REFUSED" -> "Disarm refused" ("ai" stays "AI")
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
    $("mic-open").hidden = s.listening !== true;   // integration's mic state, never a guess

    renderToast(s.banner);
    renderLive(s);
    renderPhoto(s.photo);
    renderChips(s.photo);
    renderRecording(s.recording, s.alert);
    renderLights(s.lights);
    if (s.version === lastVersion) return; // the log didn't change
    lastVersion = s.version;
    renderTalk(s.talk);
    renderFeed(s.feed);
    renderRules(s.rules, s.instructions || []);
    renderNotes(s.summary, s.messages);
  }

  // Live camera in the centre: integration's stream on the controls port, with the
  // object detector's boxes drawn in. The latest photo shows until the stream is up and
  // whenever it's down. A stream <img> reports "load" once and never a drop (Chrome,
  // Safari), so integration's /api/status is polled: no camera or no server -> the
  // photo, camera back -> the stream again. Paused while the page is hidden.
  let controlsPort = null;
  let liveUp = false;
  let camDown = false;
  let lastPhotoSrc = null;
  const CAMERA_POLL_MS = 3000;
  const ctrl = (path) => location.protocol + "//" + location.hostname + ":" + controlsPort + path;

  function stopLive() {
    const img = $("live");
    liveUp = false;
    img.onload = img.onerror = null;
    img.removeAttribute("src");           // closes the stream connection
    img.hidden = true;
    $("live-badge").hidden = true;
    showPhoto(lastPhotoSrc);
  }

  function startLive() {
    if (controlsPort === null || document.hidden) return;
    const img = $("live");
    img.onload = () => {
      liveUp = true;
      img.hidden = false;
      $("live-badge").hidden = false;
      $("photo").hidden = true;
      $("photo-empty").hidden = true;
    };
    img.onerror = () => { camDown = true; stopLive(); };   // the status poll brings it back
    img.src = ctrl("/stream.mjpg?t=" + Date.now());
  }

  function pollCamera() {
    if (controlsPort === null || document.hidden) return;
    fetch(ctrl("/api/status"), { cache: "no-store" })
      .then((r) => r.json())
      .then((s) => {
        if (!s.camera) { if (!camDown) { camDown = true; stopLive(); } }
        else if (camDown) { camDown = false; startLive(); }
      })
      .catch(() => { if (!camDown) { camDown = true; stopLive(); } });
  }

  function renderLive(s) {
    if (controlsPort === null && s.controls_port) {
      controlsPort = s.controls_port;
      startLive();
      setInterval(pollCamera, CAMERA_POLL_MS);
    }
  }

  // No stream while nobody is looking: the camera and its detector rest.
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stopLive();
    else if (!camDown) startLive();
  });

  // What the detector saw in the latest photo, as small chips under the camera.
  function renderChips(photo) {
    const box = $("chips");
    const chips = (photo && photo.chips) || [];
    box.replaceChildren(...chips.map((c) => el("span", "chip", c.count > 1 ? c.count + " × " + c.label : c.label)));
    box.hidden = !chips.length;
  }

  // "Recording saved" under the camera during the alarm it belongs to (the activity
  // entry keeps the record after the passphrase).
  function renderRecording(rec, alert) {
    const show = alert === "alarm" && !!(rec && rec.for_alarm);
    $("recording").hidden = !show;
    if (show) $("recording-text").textContent = "Recording saved · " + rec.time;
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
    lastPhotoSrc = src;
    if (src) {
      img.src = src;
      img.hidden = liveUp;            // the live camera, when it's up, stays in front
      $("photo-empty").hidden = true;
      backdrop.style.backgroundImage = 'url("' + src.replace(/["\\]/g, "") + '")';
      backdrop.classList.add("on");
    } else {
      img.hidden = true;
      img.removeAttribute("src");
      $("photo-empty").hidden = liveUp;
      backdrop.classList.remove("on");
    }
  }

  // Lights: read-only on/off status from the log (what integration really sent).
  // Switching them is on the Controls page only (owner, 2026-11-15).
  const lightTiles = {};

  function lightTile(light) {
    let tile = lightTiles[light.id];
    if (!tile) {
      tile = el("div", "light");
      tile.dataset.outlet = light.id;
      tile.setAttribute("role", "img");
      const words = el("div", "words");
      words.append(el("div", "name", light.name), el("div", "state"));
      tile.append(icon("bulb"), words);
      lightTiles[light.id] = tile;
    }
    return tile;
  }

  function renderLights(lights) {
    const box = $("lights");
    const shown = [];
    for (const light of lights) {
      if (light.id === "spare" && !light.on) continue; // only show the spare when it's on
      const tile = lightTile(light);
      tile.classList.toggle("on", light.on);
      tile.setAttribute("aria-label", light.name + (light.on ? " is on" : " is off"));
      tile.querySelector(".state").textContent = light.on ? "On" : "Off";
      shown.push(tile);
    }
    if (shown.length !== box.children.length || shown.some((t, i) => box.children[i] !== t)) {
      box.replaceChildren(...shown);
    }
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
      if (t.voice) who.append(el("span", "voice " + t.voice, t.voice === "owner" ? "Owner's voice" : "Another voice"));
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
    summarize_log: "activity", set_mode: "shield", recording_saved: "camera",
  };

  function renderFeed(feed) {
    const list = $("feed");
    list.replaceChildren();
    if (!feed.length) list.append(el("li", "empty", "Waiting for the first event."));
    for (const f of feed) {
      const li = el("li", f.level + " " + f.kind + (f.thumb ? " has-thumb" : ""));
      const node = el("span", "node");
      node.append(icon(FEED_ICON[f.type] || "activity"));
      const text = el("span", "text", f.text);
      if (f.detail) text.append(el("span", "detail", f.detail));
      li.append(node, text);
      if (f.thumb) {                      // photos and recordings as small thumbnails
        const img = el("img", "thumb");
        img.alt = "";
        img.loading = "lazy";
        img.onerror = () => img.remove();
        img.src = f.thumb;
        li.append(img);
      }
      li.append(el("time", "", f.time));
      list.append(li);
    }
  }

  function deleteButton(title, path, id) {
    const del = el("button", "rule-del");
    del.type = "button";
    del.title = title;
    del.setAttribute("aria-label", title);
    del.append(icon("close"));
    del.onclick = () => post(path, { id }).then(poll);
    return del;
  }

  function renderRules(rules, instructions) {
    const list = $("rules");
    list.replaceChildren();
    $("rules-title").textContent = instructions.length ? "Rules & instructions" : "Rules";
    // Owner instructions first: words Faraday passes on to visitors (never actions).
    for (const ins of instructions) {
      const li = el("li", "instruction");
      const text = el("span", "rule-text");
      text.append(el("span", "ins-who", ins.who), document.createTextNode(ins.text));
      li.append(text, deleteButton("Delete instruction", "/api/instructions/delete", ins.id));
      list.append(li);
    }
    if (!rules.length && !instructions.length) {
      list.append(el("li", "empty", "Say \u201cWhen there's motion at night, turn on the lamp.\u201d"));
    }
    for (const r of rules) {
      const li = el("li");
      li.append(el("span", "rule-text", r.text), deleteButton("Delete rule", "/api/rules/delete", r.id));
      if (r.description) li.append(el("span", "rule-said", "\u201c" + r.description + "\u201d"));
      list.append(li);
    }
  }

  let lastNotes = [null, []];

  function renderNotes(summary, messages) {
    lastNotes = [summary, messages];
    const box = $("summary");
    box.hidden = !summary;
    if (summary) box.textContent = summary.text;
    $("notes-title").textContent = summary ? "Summary & messages" : "Messages";
    const list = $("messages");
    list.replaceChildren();
    if (!messages.length) list.append(el("li", "empty", "No messages."));
    for (const m of messages) {            // newest first
      const li = el("li");
      li.append(el("span", "msg-text", m.text), el("span", "meta", m.sender + " · " + m.time));
      list.append(li);
    }
    // Only whole messages: drop the oldest until the rest fit. If the newest alone is
    // still too tall, clamp its text so its sender and time stay visible.
    while (list.children.length > 1 && list.scrollHeight > list.clientHeight + 1) {
      list.lastElementChild.remove();
    }
    const newest = list.firstElementChild;
    if (newest && list.scrollHeight > list.clientHeight + 1) newest.classList.add("clamp");
    if (newest && list.scrollHeight > list.clientHeight + 1) newest.classList.add("clamp1");
  }
  window.addEventListener("resize", () => renderNotes(lastNotes[0], lastNotes[1]));

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

  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDialog(); });

  poll();
  setInterval(poll, POLL_MS);
})();
