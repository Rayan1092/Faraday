"""Start Faraday: python3 -m integration   (the Mac: integration/start_mac.sh)

The real product only (owner decision 2026-11-15): the Flipper, Dell's pipeline
(modes, log, rules, Bonsai), the camera with vision and object detection, the
mic with the wake phrase and listening windows, Argmax speech, the dashboard
and the controls page. There are no fake or stand-in options here; the tests
and the rehearsal test pass their stand-ins to build_app() directly.

Hands-free: the mic opens by itself after a doorbell and after every question
Faraday asks, and the wake phrase "Hey Faraday" is listened for while idle. It is
always muted while Faraday speaks.

Console keys (then Enter):
  r          open one listening window now (ends when you stop talking)
  2 / 3      arm Away / Night        1   Home (asks for the passphrase)
  s          status                  q   quit
"""
from __future__ import annotations

import argparse
import getpass
import logging
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared import Mode  # noqa: E402

from hardware.settings import CONFIG_DIR, for_os, load_settings  # noqa: E402
from integration.app import App  # noqa: E402


PARTS = frozenset({"flipper", "capture_factory", "speaker", "stream_factory", "stt", "chime"})


def build_app(args: argparse.Namespace, settings, known_outlets=None,
              parts: Optional[Dict[str, Any]] = None) -> App:
    """The whole app, wired as on the Mac. `parts` is for the automated tests and
    the rehearsal test only: it swaps a hardware piece for a stand-in without
    changing any wiring ("flipper", "capture_factory", "speaker" (gate -> TTS),
    "stream_factory" (mic), "stt", "chime"). The product never passes it."""
    parts = parts or {}
    unknown = set(parts) - PARTS
    if unknown:
        raise ValueError(f"build_app: unknown parts {sorted(unknown)} (known: {sorted(PARTS)})")
    if "flipper" in parts:
        flipper = parts["flipper"]
    else:
        from hardware.flipper import Flipper
        flipper = Flipper(settings)
    log, rules = None, None
    if args.log:  # keep test runs out of logs/events.jsonl
        from log import EventLog
        from rules import RuleStore
        log = EventLog(args.log, settings["security"]["passphrase"])
        rules = RuleStore(Path(args.log).with_name(Path(args.log).stem + "-rules.json"))
    feed, camera = None, None
    if settings.get("camera", {}).get("enabled") is not False:
        from hardware.camera import CameraFeed, PhotoCamera
        from hardware.detect import Detector
        from hardware.vision import Vision
        detector = None if args.no_detect else Detector.from_settings(settings)
        feed = CameraFeed.from_settings(settings, detector)   # one owner: photos + live stream
        if "capture_factory" in parts:
            feed.capture_factory = parts["capture_factory"]
        camera = PhotoCamera(feed, Vision.from_settings(settings), detector)
    from hardware.audio import MicGate
    from hardware.speech import TextToSpeech
    gate = MicGate()                     # the speaker mutes the mic while talking
    speaker = parts["speaker"](gate) if "speaker" in parts else TextToSpeech.from_settings(settings, gate)
    app = App(settings, flipper, camera=camera, speaker=speaker,
              use_llm=not getattr(args, "offline", False),      # tests only; the product uses Bonsai
              mode=args.mode, event_log=log, rules=rules, dashboard=not args.no_dashboard,
              port=args.port,
              lights_off_at_start=bool(settings.get("integration", {}).get("lights_off_at_start", True)),
              known_outlets=known_outlets)
    app.video = feed                     # alarm recording
    from hardware.audio import resolve_speaker
    # one speaker for everything Faraday plays itself: an OUTPUT device by name or
    # number, or None = the system output (followed live on the Mac, like argmax-cli)
    speaker_device = resolve_speaker(settings.get("audio", {}).get("speaker_device"))
    if not args.no_mic:
        from hardware.audio import Recorder, play_chime
        from hardware.diarize import Diarizer
        from hardware.owner_voice import OwnerVoice
        from hardware.speech import SpeechToText
        from integration.voice import Voice
        audio = settings.get("audio", {})
        wake = dict(audio.get("wake", {}))
        if args.no_wake:
            wake["enabled"] = False
        diarizer = Diarizer.from_settings(settings)
        owner_voice = OwnerVoice.from_settings(settings, diarizer)      # None until enrolled
        recorder = Recorder.from_settings(settings, gate)
        if "stream_factory" in parts:
            recorder.stream_factory = parts["stream_factory"]
        chime = parts["chime"] if "chime" in parts else (lambda: play_chime(gate, speaker_device))
        stt = parts["stt"] if "stt" in parts else SpeechToText.from_settings(settings)
        app.voice = Voice(recorder, stt,
                          app.submit, out=lambda line: app.out(line), listen=audio.get("listen"), wake=wake,
                          diarizer=diarizer,
                          diarize_grace_s=Diarizer.grace_s(settings),
                          owner_voice=owner_voice, owner_voice_grace_s=OwnerVoice.grace_s(settings),
                          urgent=lambda: app.pipeline.modes.alert != "normal",
                          mode=lambda: app.pipeline.modes.mode,     # the visit after a ring: armed only
                          busy=app.speech_busy, chime=chime)
    # Part health (owner 2026-11-15): a part failing mid-run is said once, clearly,
    # and again when it's back; nothing stops (integration/health.py).
    from hardware.settings import REPO_ROOT
    from integration.health import Health, camera_check, lm_studio_check, tcp_check
    integ = settings.get("integration", {})
    logs_dir = Path(settings.get("paths", {}).get("logs", "logs"))
    health = Health(out=lambda line: app.out(line), interval_s=float(integ.get("health_interval_s", 5)),
                    grace_s=float(integ.get("health_grace_s", 10)),
                    log_path=(logs_dir if logs_dir.is_absolute() else REPO_ROOT / logs_dir) / "health.log")
    health.add("flipper", lambda: (flipper.connected.is_set(),
                                   "no USB connection (unplugged, or an app is open on the Flipper)"))
    if feed is not None:
        health.add("camera", camera_check(feed))
    stt_url = getattr(getattr(app.voice, "stt", None), "url", None) if app.voice else None
    if stt_url:
        health.add("stt", tcp_check(stt_url, "  (start it: argmax-cli serve)"))
    if not getattr(args, "offline", False):
        llm = settings.get("llm", {})
        prof = llm.get("profiles", {}).get(llm.get("profile"), {})
        base = str(prof.get("base_url", "")).rstrip("/")
        if base:
            health.add("bonsai", lm_studio_check(base, prof.get("model", "")))
    app.health = health
    if app.board is not None:
        # Hooks for Dell's dashboard (PLAN Q26). listening_hook drives the mic-open
        # indicator (True only while a visitor/owner window is recording). The
        # Listen button and its listen_hook are gone (owner 2026-11-14: the mic's
        # own mute button is the off switch; windows open by themselves).
        if app.voice is not None:
            voice = app.voice
            app.board.listening_hook = lambda: voice.listening
        app.board.control_hook = app.control
    if feed is not None:
        app.extras.append(feed)
    if not args.no_controls:
        from hardware.intercom import Intercom
        from integration.controls import ControlsServer
        intercom = Intercom(gate, speaker_device)
        if app.voice is not None:
            intercom.on_end = app.voice.on_intercom_end   # then hear the visitor's reply
        dash = settings.get("dashboard", {})
        cfg = settings.get("controls", {})
        dash_port = (app.board.httpd.server_address[1] if app.board is not None
                     else int(dash.get("port", 8080)))
        port = args.controls_port if args.controls_port is not None else int(
            cfg.get("port", dash_port + 1 if dash_port else 0))
        app.controls = ControlsServer(
            cfg.get("host", dash.get("host", "127.0.0.1")), port, feed=feed, intercom=intercom,
            control=app.control,
            outlet_state=app.outlet_states, dashboard_port=dash_port, health=health.status)
        app.extras.append(app.controls)
    return app


def console(app: App) -> None:
    modes = app.pipeline.modes
    while True:
        try:
            line = input(f"\n[{modes.mode}/{modes.alert}]> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        key, _, rest = line.partition(" ")
        key = key.lower()
        if key == "q":
            return
        if key == "r":
            if app.voice is None:
                print("  mic is off (--no-mic)")
            elif not app.voice.listen_once("console"):
                print("  already listening")
        elif key in ("2", "3"):
            if app.pipeline.owner_set_mode(Mode.AWAY if key == "2" else Mode.NIGHT) is None:
                print(f"  already {modes.mode}")
        elif key == "1":
            if not modes.armed:
                print("  already home")
                continue
            attempt = rest or getpass.getpass("  passphrase: ")
            if app.pipeline.owner_passphrase(attempt) is None:
                print("  wrong passphrase")
        elif key == "s":
            print(f"  mode {modes.mode}, alert {modes.alert}, LED {app.flipper.current_color()}, "
                  f"flipper {'connected' if app.flipper.connected.is_set() else 'NOT connected'}")
        elif key == "h" or line:
            print(__doc__.split("Console keys", 1)[1])


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except AttributeError:
            pass
    ap = argparse.ArgumentParser(prog="python3 -m integration", description="Start Faraday",
                                 allow_abbrev=False)        # no prefix can bring back an old flag
    ap.add_argument("--no-mic", action="store_true", help="no microphone at all")
    ap.add_argument("--no-wake", action="store_true", help='mic only in windows, no "Hey Faraday"')
    ap.add_argument("--no-controls", action="store_true", help="no camera stream / intercom server")
    ap.add_argument("--controls-port", type=int, help="controls port (default dashboard port + 1)")
    ap.add_argument("--no-detect", action="store_true",
                    help="no object detection (boxes on the stream, counts on photos)")
    ap.add_argument("--mode", choices=sorted(Mode.ALL), default=Mode.HOME)
    ap.add_argument("--log", help="log file (default: logs/events.jsonl)")
    ap.add_argument("--port", type=int, help="dashboard port (default from settings)")
    ap.add_argument("--no-dashboard", action="store_true")
    ap.add_argument("--fresh", action="store_true",
                    help="move logs/ and captures/ to old_runs/<time>/ first (empty log)")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true", help="show driver details")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    settings = load_settings()
    known_outlets = None
    if args.fresh:
        from integration.fresh import archive_run, outlet_states_in
        moved = archive_run(settings)
        print(f"Fresh start: old logs and captures moved to {moved}" if moved
              else "Fresh start: nothing to move")
        if moved is not None:
            logs_name = Path(settings.get("paths", {}).get("logs", "logs")).name
            known_outlets = outlet_states_in(moved / logs_name / "events.jsonl")
    app = build_app(args, settings, known_outlets)
    print(f"Faraday. Settings: {'settings.json' if (CONFIG_DIR / 'settings.json').exists() else 'settings.example.json (public example passphrase!)'}")
    try:                    # the app's speaker vs the system output that argmax-cli --play uses
        from hardware.audio import speaker_report
        for level, msg in speaker_report(settings.get("audio", {}).get("speaker_device")):
            print(f"  [speaker] {msg}" if level == "OK" else f"  [speaker] {level}: {msg}")
    except Exception as e:
        print(f"  [speaker] WARN: can't check the speaker ({e!r})")
    print(f"Flipper: autodetect, then the settings port. Log: {app.pipeline.log.path}")
    app.start()
    if app.board is not None:
        print(f"Dashboard: {app.board.url}")
        if not args.no_browser:
            webbrowser.open(app.board.url)
    controls = getattr(app, "controls", None)
    if controls is not None:
        controls.start()
        print(f"Controls (camera, intercom, lights): {controls.url}")
    if app.voice is not None:
        app.voice.start_wake()
    vision = getattr(app.camera, "vision", None)
    if vision is not None:
        # LM Studio loads the vision part on the first image: do it now, not at the doorbell
        def warm_vision() -> None:
            from hardware.vision import FIX_HINT
            secs = vision.warm_up()
            if secs is not None:
                print(f"  [vision] ready in {secs} s; one description takes {vision.answer_s} s "
                      f"({vision.model}, no-think: {vision.strategy})")
            elif vision.disabled_why.startswith("the model thinks"):
                print(f"  [vision] {vision.model} thinks before every answer: photos are described by the "
                      f"object detector. Fix: {FIX_HINT}")
            elif vision.disabled_until:
                print(f"  [vision] {vision.model}: {vision.disabled_why}. Photos are described by the "
                      "object detector meanwhile.")
            else:
                print("  [vision] warm-up failed (is the model loaded in LM Studio? see the log)")
        threading.Thread(target=warm_vision, name="faraday-vision-warmup", daemon=True).start()
    detector = getattr(app.camera, "detector", None)
    if detector is not None:
        print(f"Object detection: {detector.engine}"
              + (f" + faces ({detector.faces.engine})" if detector.faces is not None else ""))
    prerender = getattr(app.speaker, "prerender", None)
    warm_up = getattr(app.speaker, "warm_up", None)
    tts_cfg = for_os(settings.get("tts", {})) or {}
    lines: List[str] = []
    if prerender is not None and getattr(app.speaker, "cache_dir", None) and tts_cfg.get("prerender", True):
        # Faraday's fixed lines (the backups for Bonsai's live lines), rendered once with
        # the fixed voice: instant and identical
        from integration.fixed_lines import fixed_lines
        lines = fixed_lines()
    if lines or (warm_up is not None and tts_cfg.get("prewarm", True)):
        def warm_tts() -> None:
            if warm_up is not None and tts_cfg.get("prewarm", True):
                # most lines are spoken live with argmax-cli --play: load the voice now
                secs = warm_up(wait=app.speech_busy, stop=app._stopping)
                if secs is not None:
                    print(f"  [tts] argmax voice warm in {secs} s")
                elif not app._stopping.is_set():
                    print("  [tts] argmax warm-up didn't finish (a line was spoken first, or see the log)")
            if lines:
                made = prerender(lines, wait=app.speech_busy, stop=app._stopping)
                print(f"  [tts] {made} fixed line(s) rendered, {len(lines)} cached")
        threading.Thread(target=warm_tts, name="faraday-tts-prerender", daemon=True).start()
    print("Keys: r listen now, 1/2/3 home/away/night, s status, q quit")
    try:
        console(app)
    finally:
        app.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
