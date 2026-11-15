// Faraday Controls page: live camera, Snapshot, Talk to visitor, light switches.
// Fully offline, no libraries; text from the log goes in with textContent only.
//
// Two servers (PLAN Q26/Q27):
// - this dashboard server: /api/state (mode, lights, photos), /api/control, /api/snapshot
// - integration's controls server on dashboard port + 1: /stream.mjpg, /api/status,
//   /api/intercom (raw 16-bit mono PCM, header X-Sample-Rate), /api/intercom/end
"use strict";

(function () {
  const $ = (id) => document.getElementById(id);
  const { setIcon, icon } = window.FaradayIcons;
  document.querySelectorAll("[data-icon]").forEach((n) => setIcon(n, n.dataset.icon));

  const H = { "X-Faraday": "1" };
  let controlsPort = null;
  let state = null;
  let lastVersion;

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function post(url, body) {
    return fetch(url, {
      method: "POST",
      headers: Object.assign({ "Content-Type": "application/json" }, H),
      body: JSON.stringify(body || {}),
    }).then((r) => r.json().catch(() => ({})).then((data) => ({ status: r.status, data })));
  }

  // The controls server is on the same host as this page, one port up, so it also
  // works from a phone at the Mac's address.
  function ctrl(path) {
    return location.protocol + "//" + location.hostname + ":" + controlsPort + path;
  }

  // ---- state from the dashboard (mode, lights, photos) ---------------------------

  const ALERT_TEXT = { normal: "All clear", warning: "Warning", alarm: "Alarm" };
  const cap = (s) => String(s || "").charAt(0).toUpperCase() + String(s || "").slice(1);

  function poll() {
    return fetch("/api/state", { cache: "no-store" })
      .then((r) => r.json())
      .then((s) => {
        state = s;
        document.body.dataset.status = s.status.color;
        document.body.dataset.alert = s.alert;
        $("status-text").textContent = cap(s.mode) + " · " + (ALERT_TEXT[s.alert] || cap(s.alert));
        renderLights(s.lights, s.control_available);
        if (s.version !== lastVersion) { lastVersion = s.version; renderCaptions(s.talk || []); }
        checkSnapshot(s.photo);
        if (controlsPort === null && s.controls_port) {
          controlsPort = s.controls_port;
          startCamera();
          pollControls();
        }
      })
      .catch(() => { $("status-text").textContent = "App not responding"; });
  }

  // ---- live captions: everything heard and said at the door ----------------------

  function renderCaptions(talk) {
    const list = $("captions");
    const atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 40;
    list.replaceChildren();
    if (!talk.length) list.append(el("li", "empty", "Nothing said at the door yet."));
    for (const t of talk) {
      const li = el("li", t.who);
      const who = el("div", "who", t.who === "heard" ? "Heard" : "Faraday");
      who.append(el("time", "", t.time));
      li.append(who, el("div", "line", t.text));
      list.append(li);
    }
    if (atBottom) list.scrollTop = list.scrollHeight;   // follow along unless scrolled up
  }

  // ---- live camera ----------------------------------------------------------------

  let camDown = false;   // the status check said there's no camera: restart when it's back

  function camOff(text, retry) {
    $("cam").hidden = true;
    $("live").hidden = true;
    $("cam-off").hidden = false;
    $("cam-off-text").textContent = text;
    $("cam-retry").hidden = !retry;
  }

  function startCamera() {
    const img = $("cam");
    img.onload = () => {
      img.hidden = false;
      $("live").hidden = false;
      $("cam-off").hidden = true;
    };
    img.onerror = () => camOff("The camera isn't sending video.", true);
    img.src = ctrl("/stream.mjpg?t=" + Date.now());
  }
  $("cam-retry").onclick = () => { camOff("Connecting to the camera…", false); startCamera(); };

  // integration's /api/status: is there a camera, a door speaker for the intercom?
  let intercomReady = false;
  // ?autotalk=N holds Talk for N seconds once the speaker is ready (automated browser test)
  let autoTalk = Number(new URLSearchParams(location.search).get("autotalk") || 0);
  function pollControls() {
    fetch(ctrl("/api/status"), { cache: "no-store" })
      .then((r) => r.json())
      .then((s) => {
        intercomReady = !!s.intercom;
        if (!s.camera) { camDown = true; camOff("The camera isn't connected.", true); }
        else if (camDown) { camDown = false; camOff("Connecting to the camera…", false); startCamera(); }
        if (!talking && !starting) setTalkIdle();
        if (autoTalk > 0 && intercomReady) {
          const hold = autoTalk * 1000;
          autoTalk = 0;
          held = true;
          startTalk().then(() => setTimeout(release, hold));
        }
      })
      .catch(() => {
        intercomReady = false;
        camDown = true;
        camOff("The camera and intercom server isn't running (port " + controlsPort + ").", true);
        if (!talking && !starting) setTalkIdle();
      });
  }

  // ---- snapshot -----------------------------------------------------------------

  let snapId = null;
  let snapTimer = null;

  $("btn-snap").onclick = () => {
    if (snapId) return;
    $("btn-snap").disabled = true;
    $("snap-label").textContent = "Taking a photo…";
    post("/api/snapshot", {}).then(({ status, data }) => {
      if (status !== 202) return snapDone(data.error || "The snapshot didn't start.");
      snapId = data.id;
      snapTimer = setTimeout(() => snapDone("No photo came back. Is the camera connected?"), 45000);
      poll();
    }).catch(() => snapDone("App not responding."));
  };

  function snapDone(error) {
    clearTimeout(snapTimer);
    snapId = null;
    $("btn-snap").disabled = false;
    $("snap-label").textContent = "Snapshot";
    if (error) showSnap({ description: error, time: "", reason: "" });
  }

  function checkSnapshot(photo) {
    if (!snapId || !photo || photo.request !== snapId) return;
    if (photo.description === "Looking at the photo...") {
      $("snap-label").textContent = "Looking at the photo…";
      return;
    }
    showSnap(photo);
    snapDone(null);
  }

  function showSnap(photo) {
    $("snap").hidden = false;
    const img = $("snap-img");
    if (photo.url) { img.src = photo.url; img.hidden = false; } else { img.hidden = true; img.removeAttribute("src"); }
    $("snap-text").textContent = photo.description || "";
    $("snap-meta").textContent = photo.time ? "Snapshot · " + photo.time : "";
    const box = $("snap-threat");
    box.replaceChildren();
    box.className = "threat" + (photo.threat ? " cat-" + photo.threat.category : "");
    if (photo.threat) {
      const pill = el("span", "threat-pill");
      pill.append(el("span", "threat-dot"), el("span", "threat-name", photo.threat.name));
      box.append(pill);
      if (photo.threat.reason) box.append(el("span", "threat-reason", photo.threat.reason));
    }
  }

  // ---- talk to visitor: browser mic -> 16-bit PCM chunks -> door speaker ----------

  const RATE = 16000;
  let ctx = null, source = null, node = null, stream = null;
  let held = false, starting = false, talking = false, queue = Promise.resolve();
  const talkBtn = $("btn-talk");

  function setTalkIdle() {
    talkBtn.classList.remove("live", "starting");
    talkBtn.disabled = !intercomReady;
    $("talk-label").textContent = "Hold to talk to visitor";
    const note = $("talk-note");
    note.classList.toggle("bad", !intercomReady);
    note.textContent = intercomReady ? "The door mic is muted while you talk."
      : controlsPort === null ? "Connecting…" : "The door speaker isn't connected.";
  }

  function micHelp(kind, name) {
    const origins = [location.origin, ctrl("")].join(",");
    const texts = {
      insecure: ["Chrome blocked the microphone on this address",
        "Chrome only lets a page use the microphone on a trusted address, and " + location.origin +
        " isn't one yet. Do this once on this computer:"],
      denied: ["Microphone blocked",
        "The browser said no to the microphone. Click the icon at the left of the address bar, set " +
        "Microphone to Allow, then reload this page."],
      missing: ["No microphone found", "Plug in a microphone (or pick one in your system settings), then try again."],
      busy: ["The microphone is busy", "Another app is using the microphone. Close it, then try again."],
      other: ["The microphone didn't start", "The browser said: " + name + ". Try again, or reload the page."],
    };
    const [title, text] = texts[kind];
    $("mic-help-title").textContent = title;
    $("mic-help-text").textContent = text;
    $("mic-origins").textContent = origins;
    $("mic-steps").hidden = kind !== "insecure";
    $("mic-help").hidden = false;
  }

  function micKind(err) {
    const name = (err && err.name) || "Error";
    if (name === "NotAllowedError" || name === "SecurityError") return window.isSecureContext ? "denied" : "insecure";
    if (name === "NotFoundError" || name === "OverconstrainedError") return "missing";
    if (name === "NotReadableError" || name === "AbortError") return "busy";
    return "other";
  }

  function toInt16(f32) {
    const out = new Int16Array(f32.length);
    for (let i = 0; i < f32.length; i++) {
      const s = Math.max(-1, Math.min(1, f32[i]));
      out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    return out;
  }

  function send(pcm, rate) {
    queue = queue.then(() => fetch(ctrl("/api/intercom"), {
      method: "POST",
      body: pcm.buffer,
      headers: Object.assign({ "Content-Type": "application/octet-stream", "X-Sample-Rate": String(rate) }, H),
    }).then((r) => {
      if (!r.ok && talking) {
        stopTalk();
        $("talk-note").classList.add("bad");
        $("talk-note").textContent = r.status === 503 ? "The door speaker isn't connected." : "The door speaker didn't play that (" + r.status + ").";
      }
    }).catch(() => {
      if (talking) {
        stopTalk();
        $("talk-note").classList.add("bad");
        $("talk-note").textContent = "Lost the connection to the door speaker.";
      }
    }));
  }

  async function startTalk() {
    if (starting || talking) return;
    $("mic-help").hidden = true;
    if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      micHelp("insecure");
      return;
    }
    starting = true;
    talkBtn.classList.add("starting");
    $("talk-label").textContent = "Starting the mic…";
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
      });
    } catch (err) {
      starting = false;
      setTalkIdle();
      micHelp(micKind(err), (err && err.name) || "Error");
      return;
    }
    starting = false;
    if (!held) {                       // released while the permission prompt was open
      stream.getTracks().forEach((t) => t.stop());
      stream = null;
      setTalkIdle();
      return;
    }
    try {
      ctx = new AudioContext({ sampleRate: RATE });
    } catch (e) {
      ctx = new AudioContext();        // the speaker side takes 8-48 kHz
    }
    if (ctx.state === "suspended") ctx.resume();
    source = ctx.createMediaStreamSource(stream);
    node = ctx.createScriptProcessor(2048, 1, 1);
    node.onaudioprocess = (ev) => {
      if (talking) send(toInt16(ev.inputBuffer.getChannelData(0)), ctx.sampleRate);
    };
    source.connect(node);
    node.connect(ctx.destination);     // needed for onaudioprocess to run; outputs silence
    talking = true;
    talkBtn.classList.remove("starting");
    talkBtn.classList.add("live");
    $("talk-label").textContent = "Talking… release to stop";
    $("talk-note").classList.remove("bad");
    $("talk-note").textContent = "The visitor hears you. The door mic is muted.";
  }

  function stopTalk() {
    if (!talking) return;
    talking = false;
    queue = queue.then(() => post(ctrl("/api/intercom/end"), {}).catch(() => {}));
    try { node.disconnect(); source.disconnect(); ctx.close(); } catch (e) { /* already closed */ }
    if (stream) stream.getTracks().forEach((t) => t.stop());
    node = source = ctx = stream = null;
    setTalkIdle();
  }

  function press(e) {
    if (talkBtn.disabled) return;
    if (e && e.pointerId !== undefined) {
      e.preventDefault();
      try { talkBtn.setPointerCapture(e.pointerId); } catch (err) { /* not capturable */ }
    }
    held = true;
    startTalk();
  }
  function release() {
    held = false;
    stopTalk();
  }
  talkBtn.addEventListener("pointerdown", press);
  talkBtn.addEventListener("pointerup", release);
  talkBtn.addEventListener("pointercancel", release);
  talkBtn.addEventListener("lostpointercapture", release);
  talkBtn.addEventListener("contextmenu", (e) => e.preventDefault());   // long-press on phones
  talkBtn.addEventListener("keydown", (e) => {
    if ((e.key === " " || e.key === "Enter") && !e.repeat) { e.preventDefault(); press(); }
  });
  talkBtn.addEventListener("keyup", (e) => { if (e.key === " " || e.key === "Enter") release(); });
  talkBtn.addEventListener("blur", release);
  document.addEventListener("visibilitychange", () => { if (document.hidden) release(); });

  // ---- lights (same switches as the dashboard dock, state from the log) ------------

  const tiles = {};
  const busy = {};
  const notes = {};

  function renderLights(lights, canSwitch) {
    const box = $("cp-lights");
    const now = Date.now();
    $("lights-note").hidden = !!canSwitch;
    lights.forEach((light, i) => {
      let tile = tiles[light.id];
      if (!tile) {
        tile = el("button", "light");
        tile.type = "button";
        const words = el("div", "words");
        words.append(el("div", "name", light.name), el("div", "state"));
        tile.append(icon("bulb"), words, el("span", "switch"));
        tile.onclick = () => switchLight(light.id, !tile.classList.contains("on"));
        tiles[light.id] = tile;
      }
      const note = notes[light.id];
      tile.classList.toggle("on", light.on);
      tile.classList.toggle("busy", !!busy[light.id]);
      tile.classList.toggle("switchable", !!canSwitch);
      tile.disabled = !canSwitch || !!busy[light.id];
      tile.setAttribute("aria-pressed", light.on ? "true" : "false");
      tile.setAttribute("aria-label", light.name + (light.on ? " on" : " off"));
      tile.querySelector(".state").textContent = busy[light.id] ? "Switching…"
        : note && note.until > now ? note.text : light.on ? "On" : "Off";
      if (box.children[i] !== tile) box.insertBefore(tile, box.children[i] || null);
    });
  }

  function switchLight(outlet, on) {
    if (busy[outlet]) return;
    busy[outlet] = true;
    delete notes[outlet];
    if (state) renderLights(state.lights, state.control_available);
    post("/api/control", { outlet, on })
      .then(({ status, data }) => {
        if (status !== 200) notes[outlet] = { text: data.error || "Didn't switch", until: Date.now() + 4000 };
      })
      .catch(() => { notes[outlet] = { text: "App not responding", until: Date.now() + 4000 }; })
      .finally(() => { delete busy[outlet]; poll(); });
  }

  // ---- go ------------------------------------------------------------------------

  setTalkIdle();
  poll();
  setInterval(poll, 1000);
  setInterval(() => { if (controlsPort !== null) pollControls(); }, 3000);
})();
