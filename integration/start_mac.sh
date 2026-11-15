#!/usr/bin/env bash
# Faraday on the Mac mini: ONE command.
#
#   integration/start_mac.sh                  # get everything up, check it all, start with --fresh
#   integration/start_mac.sh --no-fresh       # keep the old log and captures
#   integration/start_mac.sh --no-browser     # other args go to the app (real options only)
#
# What it does, in order (every step prints one [ OK ] / [WARN] / [FAIL] line):
#   1. config/settings.json (created from the example with llm.profile "mac" if missing)
#   2. Wi-Fi off?
#   3. LM Studio: waits for its server (starts it with `lms server start` if the lms
#      CLI is installed), then the 9B and 2B must be downloaded AND loaded (loads them
#      with `lms load` if lms is installed). Anything else loaded: eject warning.
#   4. Argmax STT: if `argmax-cli serve` isn't answering (and isn't already
#      starting), starts it itself in its own session (Ctrl-C on the app doesn't
#      stop it): stt.mac.serve_command, default plain `argmax-cli serve`; log in
#      cache/argmax-serve.log; waits until it answers (stops waiting if it dies).
#   5. TTS binary, object detection model, OpenCV 4.7+, mic, speaker, camera,
#      Flipper port, passphrase, owner voice enrollment.
#   6. A READY / NOT READY banner, then: python3 -m integration --fresh <args>
#
# Env: PYTHON (default python3), FARADAY_SETTINGS (default config/settings.json),
#      FARADAY_SKIP_CHECKS=1, FARADAY_DRY_RUN=1 (check only, don't start),
#      FARADAY_DEVICE_CHECKS=0 (no mic/speaker/camera probe), FARADAY_USE_LMS=0 (don't use lms),
#      FARADAY_START_STT=0 (never start argmax-cli serve; the tests set these three),
#      FARADAY_WAIT_LM_S (30), FARADAY_WAIT_STT_S (120).
set -u
cd "$(dirname "$0")/.." || exit 1

PY="${PYTHON:-python3}"
SETTINGS="${FARADAY_SETTINGS:-config/settings.json}"
WAIT_LM="${FARADAY_WAIT_LM_S:-30}"
WAIT_STT="${FARADAY_WAIT_STT_S:-120}"
N_OK=0; N_WARN=0; N_FAIL=0
FAIL_LINES=""
ok()   { printf '  \033[1;32m[ OK ]\033[0m %s\n' "$1"; N_OK=$((N_OK + 1)); }
warn() { printf '  \033[1;33m[WARN]\033[0m %s\n' "$1"; N_WARN=$((N_WARN + 1)); }
fail() { printf '  \033[1;31m[FAIL]\033[0m %s\n' "$1"; N_FAIL=$((N_FAIL + 1)); FAIL_LINES="$FAIL_LINES
    - $1"; }
note() { printf '         %s\n' "$1"; }

FRESH="--fresh"
APP_ARGS=()
for arg in "$@"; do
  case "$arg" in
    --no-fresh) FRESH="" ;;
    --fresh) ;;                                  # the default anyway
    *) APP_ARGS+=("$arg") ;;
  esac
done

echo
echo "  ============================================================"
echo "    FARADAY  -  Mac start  $(date '+%H:%M:%S')"
echo "  ============================================================"

# ---- 1. settings ------------------------------------------------------------------------
if [ ! -f "$SETTINGS" ]; then
  "$PY" - "$SETTINGS" <<'EOF' || exit 1
import json, sys
s = json.load(open("config/settings.example.json", encoding="utf-8"))
s["llm"]["profile"] = "mac"
json.dump(s, open(sys.argv[1], "w", encoding="utf-8"), indent=2)
EOF
  warn "created $SETTINGS from the example (profile mac). Change security.passphrase in it!"
fi

# Read the values we need from settings (shell-quoted by Python).
VALUES="$("$PY" - "$SETTINGS" <<'EOF'
import json, shlex, sys
from urllib.parse import urlparse
s = json.load(open(sys.argv[1], encoding="utf-8"))
llm = s["llm"]; prof = llm["profiles"].get(llm["profile"], {})
stt = s.get("stt", {}).get("mac", {}) or {}
tts = s.get("tts", {}).get("mac", {}) or {}
ex = json.load(open("config/settings.example.json", encoding="utf-8"))
argmax_bin = (tts.get("command") or [""])[0]
stt_url = stt.get("url", "") if stt.get("kind", "argmax") != "standin" else ""
serve = stt.get("serve_command")
if isinstance(serve, str):
    serve = shlex.split(serve)
if not serve and argmax_bin and stt_url:
    serve = [argmax_bin, "serve"]                  # the known-working plain command
    port = urlparse(stt_url).port
    if port and port != 50060:
        serve += ["--port", str(port)]
check = s.get("audio", {}).get("voice_check", {}) or {}
vals = {
    "PROFILE": llm["profile"],
    "LLM_URL": str(prof.get("base_url", "")).rstrip("/"),
    "LLM_MODEL": prof.get("model", ""),
    "FAST_MODEL": prof.get("fast_model") or "",
    "VISION_MODEL": llm.get("vision_model") or "",
    "STT_URL": stt_url,
    "SERVE_CMD": " ".join(shlex.quote(str(p)) for p in (serve or [])),
    "TTS_BIN": argmax_bin,
    "TTS_KIND": tts.get("kind", ""),
    "PORT_GLOB": (s.get("flipper", {}).get("serial_port", {}) or {}).get("mac", ""),
    "EXAMPLE_PASS": "1" if s["security"]["passphrase"] == ex["security"]["passphrase"] else "0",
    "ENROLLMENT": check.get("enrollment") or "cache/owner_voice.wav",
    "CAMERA_INDEX": str((s.get("camera", {}) or {}).get("index", 0)),
    "MIC_DEVICE": "" if s.get("audio", {}).get("mic_device") is None else str(s["audio"]["mic_device"]),
    "SPEAKER_DEVICE": "" if s.get("audio", {}).get("speaker_device") is None else str(s["audio"]["speaker_device"]),
    "DIARIZE": "1" if (stt.get("diarize") or {}).get("command") else "0",
}
for k, v in vals.items():
    print(f"{k}={shlex.quote(str(v))}")
EOF
)" || { echo "cannot read $SETTINGS"; exit 1; }
eval "$VALUES"

LMS=""
if [ "${FARADAY_USE_LMS:-1}" != "0" ]; then          # tests set 0: never touch a real LM Studio
  LMS="$(command -v lms 2>/dev/null || true)"
  [ -z "$LMS" ] && [ -x "$HOME/.lmstudio/bin/lms" ] && LMS="$HOME/.lmstudio/bin/lms"
fi

IS_MAC=0; [ "$(uname -s 2>/dev/null)" = "Darwin" ] && IS_MAC=1

with_timeout() {  # with_timeout SECONDS cmd ...  (macOS has no `timeout`; perl does alarm)
  local secs="$1"; shift
  perl -e 'alarm shift; exec @ARGV' "$secs" "$@"
}

WATCH_PID=""     # set while waiting for a serve we started (checked on the Mac only)
wait_http() {    # wait_http URL SECONDS LABEL -> 0 answers, 1 timed out, 2 the process died
  local url="$1" secs="$2" label="$3" code end next
  end=$((SECONDS + secs)); next=$((SECONDS + 10))
  while [ "$SECONDS" -lt "$end" ]; do
    code="$(curl -s -m 2 -o /dev/null -w '%{http_code}' "$url" 2>/dev/null)"
    if [ -n "$code" ] && [ "$code" != "000" ]; then return 0; fi
    if [ -n "$WATCH_PID" ] && [ "$IS_MAC" = "1" ] && ! kill -0 "$WATCH_PID" 2>/dev/null; then
      return 2
    fi
    if [ "$SECONDS" -ge "$next" ]; then
      note "... waiting for $label ($((SECONDS + secs - end)) s)"; next=$((SECONDS + 10))
    fi
    sleep 1
  done
  return 1
}

if [ "${FARADAY_SKIP_CHECKS:-0}" != "1" ]; then
  # ---- 2. Wi-Fi (the event rule: run with Wi-Fi off) ---------------------------------------
  if command -v networksetup >/dev/null 2>&1; then
    WIFI_DEV="$(networksetup -listallhardwareports 2>/dev/null | awk '/Wi-Fi|AirPort/{getline; print $2; exit}')"
    if [ -n "$WIFI_DEV" ] && networksetup -getairportpower "$WIFI_DEV" 2>/dev/null | grep -q "On"; then
      warn "Wi-Fi is ON ($WIFI_DEV). Turn it off for the demo."
    else
      ok "Wi-Fi off"
    fi
  fi

  [ "$PROFILE" = "mac" ] && ok "llm profile mac" || warn "llm.profile is '$PROFILE', not 'mac'"

  # ---- 3. LM Studio ------------------------------------------------------------------------
  # The profile's model (9B) and fast_model (2B) must both be downloaded AND loaded.
  # /v1/models lists every downloaded model; /api/v0/models says which are loaded. Only
  # Bonsai 2 may run (event rule), and the Mac also has 27B models installed, so anything
  # else loaded gets an "eject" warning. (-f: a 404 from an older LM Studio gives no body
  # -> "can't tell", not "nothing loaded")
  if ! curl -s -m 3 "$LLM_URL/v1/models" >/dev/null 2>&1; then
    if [ -n "$LMS" ]; then
      note "LM Studio server not up: starting it ($LMS server start)"
      with_timeout 30 "$LMS" server start </dev/null >/dev/null 2>&1 || true
    fi
    wait_http "$LLM_URL/v1/models" "$WAIT_LM" "LM Studio" || true
  fi
  MODELS="$(curl -s -m 5 "$LLM_URL/v1/models" 2>/dev/null)"
  V0="$(curl -s -f -m 5 "$LLM_URL/api/v0/models" 2>/dev/null)"
  if [ -n "$MODELS" ] && [ -n "$LMS" ] && [ -n "$V0" ]; then
    for m in "$LLM_MODEL" "$FAST_MODEL" "$VISION_MODEL"; do
      [ -z "$m" ] && continue
      # only a downloaded model (lms load never downloads), and only if it isn't loaded
      if printf '%s' "$MODELS" | "$PY" -c 'import json,sys; m=sys.argv[1]; d=json.load(sys.stdin).get("data",[]); sys.exit(0 if any(x.get("id")==m for x in d) else 1)' "$m" 2>/dev/null \
         && ! printf '%s' "$V0" | "$PY" -c 'import json,sys; m=sys.argv[1]; d=json.load(sys.stdin).get("data",[]); sys.exit(0 if any(x.get("id")==m and str(x.get("state","")).lower()=="loaded" for x in d) else 1)' "$m" 2>/dev/null; then
        note "loading $m with lms (up to 2 min) ..."
        with_timeout 120 "$LMS" load "$m" --yes </dev/null >/dev/null 2>&1 || true
      fi
    done
    V0="$(curl -s -f -m 5 "$LLM_URL/api/v0/models" 2>/dev/null)"
  fi
  if [ -z "$MODELS" ]; then
    fail "LM Studio not answering at $LLM_URL. Start its server (Developer tab, or: lms server start) and load $LLM_MODEL${FAST_MODEL:+ and $FAST_MODEL}${VISION_MODEL:+ and $VISION_MODEL}."
  else
    REPORT="$(MODELS="$MODELS" V0="$V0" "$PY" - "$LLM_MODEL" "$FAST_MODEL" "$VISION_MODEL" "$LLM_URL" <<'EOF'
import json, os, sys
need = [m for m in dict.fromkeys(sys.argv[1:4]) if m]     # 9B, 2B, and llm.vision_model if set
url = sys.argv[4]

def ids(raw, loaded_only=False):
    try:
        data = json.loads(raw).get("data")
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    return [m.get("id") for m in data
            if not loaded_only or str(m.get("state", "")).lower() == "loaded"]

have = ids(os.environ.get("MODELS", "")) or []
loaded = ids(os.environ.get("V0", ""), loaded_only=True)
for m in need:
    if m in have:
        print(f"OK|LM Studio at {url} has {m} downloaded")
    else:
        print(f"FAIL|LM Studio has no model '{m}' downloaded. Download it, or fix llm.profiles in settings (see: curl {url}/v1/models).")
if loaded is None:
    print("WARN|can't tell which models are loaded (no /api/v0/models); every request names its model id exactly")
else:
    for m in need:
        if m in loaded:
            print(f"OK|{m} is loaded")
        elif m in have:
            print(f"FAIL|{m} is downloaded but NOT loaded (loaded now: {', '.join(loaded) or 'nothing'}). Load it in LM Studio. Only Bonsai 2 is allowed.")
    extra = [m for m in loaded if m not in need]
    if extra:
        print(f"WARN|other models are loaded ({', '.join(extra)}): eject them. Only Bonsai 2 is allowed, and they use RAM.")
EOF
)"
    while IFS='|' read -r level msg; do
      case "$level" in
        OK)   ok "$msg" ;;
        WARN) warn "$msg" ;;
        FAIL) fail "$msg" ;;
      esac
    done <<< "$REPORT"
  fi

  # ---- 4. Argmax STT server: any HTTP answer means it's up ---------------------------------
  if [ -n "$STT_URL" ]; then
    STT_BASE="$(printf '%s' "$STT_URL" | sed -E 's#^(https?://[^/]+).*#\1#')"
    CODE="$(curl -s -m 5 -o /dev/null -w '%{http_code}' "$STT_BASE/" 2>/dev/null)"
    if [ -n "$CODE" ] && [ "$CODE" != "000" ]; then
      ok "Argmax STT answering at $STT_BASE (HTTP $CODE)"
    elif [ -n "$SERVE_CMD" ] && [ "${FARADAY_START_STT:-1}" != "0" ]; then
      mkdir -p cache
      RUNNING=""
      [ "$IS_MAC" = "1" ] && RUNNING="$(pgrep -f "argmax-cli serve" 2>/dev/null | head -1)"
      if [ -n "$RUNNING" ]; then
        note "argmax-cli serve is already starting (pid $RUNNING): waiting for it, not starting another"
        SERVE_PID="$RUNNING"
      else
        note "Argmax STT not running: starting it ($SERVE_CMD)"
        # its own session: Ctrl-C on the app doesn't stop it; the pid is argmax-cli's own
        SERVE_PID="$("$PY" -c 'import shlex, subprocess, sys
p = subprocess.Popen(shlex.split(sys.argv[1]), stdin=subprocess.DEVNULL, stdout=open(sys.argv[2], "ab"),
                     stderr=subprocess.STDOUT, start_new_session=True)
print(p.pid)' "$SERVE_CMD" cache/argmax-serve.log 2>>cache/argmax-serve.log)"
      fi
      WATCH_PID="$SERVE_PID"
      wait_http "$STT_BASE/" "$WAIT_STT" "argmax-cli serve"; RC=$?
      WATCH_PID=""
      if [ "$RC" = "0" ]; then
        ok "Argmax STT started and answering at $STT_BASE (pid $SERVE_PID; log cache/argmax-serve.log)"
      elif [ "$RC" = "2" ]; then
        fail "argmax-cli serve stopped right away (log: cache/argmax-serve.log). Check the command, or set stt.mac.serve_command"
        tail -n 5 cache/argmax-serve.log 2>/dev/null | while IFS= read -r line; do note "log: $line"; done
      else
        fail "Argmax STT did not come up at $STT_BASE within ${WAIT_STT} s (pid ${SERVE_PID:-?} may still be loading: wait a moment and run start_mac.sh again; it won't start a second copy). Log: cache/argmax-serve.log"
        tail -n 5 cache/argmax-serve.log 2>/dev/null | while IFS= read -r line; do note "log: $line"; done
      fi
    else
      fail "Argmax STT not answering at $STT_BASE. Start it: argmax-cli serve"
    fi
  else
    fail "no Argmax STT configured (stt.mac): the product needs speech recognition"
  fi

  # ---- 5. the rest -----------------------------------------------------------------------------
  if [ "$TTS_KIND" != "argmax" ]; then
    fail "tts.mac.kind is '$TTS_KIND': the product speaks with Argmax (\"kind\": \"argmax\")"
  elif [ -n "$TTS_BIN" ] && command -v "$TTS_BIN" >/dev/null 2>&1; then
    ok "TTS command found: $TTS_BIN"
  else
    fail "TTS command '$TTS_BIN' not found: fix tts.mac.command (Faraday must speak)"
  fi

  # Object detection (hardware/detect.py): the model comes with git pull; OpenCV must be
  # 4.7+ (NMSBoxesBatched, FaceDetectorYN). Without it detection is just off.
  if [ -f hardware/models/yolox_tiny.onnx ]; then
    ok "object detection model present (hardware/models/yolox_tiny.onnx)"
  else
    warn "hardware/models/yolox_tiny.onnx missing: git pull with Wi-Fi on (detection will be off)"
  fi
  CV_VER="$("$PY" -c 'import cv2; print(cv2.__version__)' 2>/dev/null)"
  if [ -z "$CV_VER" ]; then
    fail "OpenCV not installed for $PY: pip3 install -r requirements.txt (Wi-Fi on)"
  elif "$PY" -c 'import sys, cv2; v = tuple(int(x) for x in cv2.__version__.split(".")[:2]); sys.exit(0 if v >= (4, 7) else 1)' 2>/dev/null; then
    ok "OpenCV $CV_VER"
  else
    warn "OpenCV $CV_VER is older than 4.7: object detection off. pip3 install -U opencv-python (Wi-Fi on)"
  fi

  if [ "${FARADAY_DEVICE_CHECKS:-1}" != "0" ]; then
    DEVICES="$("$PY" -u - "$CAMERA_INDEX" "$MIC_DEVICE" "$SPEAKER_DEVICE" <<'EOF' 2>/dev/null
import sys
def dev(v):
    return None if v == "" else (int(v) if v.lstrip("-").isdigit() else v)
try:
    import sounddevice as sd
    mic, spk = dev(sys.argv[2]), dev(sys.argv[3])
    i = sd.query_devices(mic, kind="input") if mic is not None else sd.query_devices(kind="input")
    o = sd.query_devices(spk, kind="output") if spk is not None else sd.query_devices(kind="output")
    print(f"OK|mic: {i['name']}")
    print(f"OK|speaker: {o['name']}")
except Exception as e:
    print(f"FAIL|no mic/speaker ({e}). Plug them in; python3 -m hardware.check devices")
try:
    import cv2
    idx = int(sys.argv[1])
    cap = cv2.VideoCapture(idx, cv2.CAP_AVFOUNDATION) if sys.platform == "darwin" else cv2.VideoCapture(idx)
    ok, frame = False, None
    if cap.isOpened():
        for _ in range(5):                     # dark frames while exposure settles
            ok, frame = cap.read()
    cap.release()
    if ok and frame is not None:
        print(f"OK|camera {idx}: {frame.shape[1]}x{frame.shape[0]}")
    else:
        print(f"WARN|camera {idx} gave no picture (plugged in? Terminal allowed to use the camera?)")
except Exception as e:
    print(f"WARN|camera check failed: {e}")
EOF
)"
    while IFS='|' read -r level msg; do
      case "$level" in
        OK)   ok "$msg" ;;
        WARN) warn "$msg" ;;
        FAIL) fail "$msg" ;;
      esac
    done <<< "$DEVICES"
    [ -z "$DEVICES" ] && warn "the mic/speaker/camera check gave no answer ($PY could not run it)"
  fi

  # shellcheck disable=SC2086
  if [ -n "$PORT_GLOB" ] && ls $PORT_GLOB >/dev/null 2>&1; then
    ok "Flipper port present: $(ls $PORT_GLOB | head -1)"
  else
    warn "no Flipper at $PORT_GLOB yet (plug it in, close apps on it; the app keeps retrying)"
  fi

  [ "$EXAMPLE_PASS" = "1" ] && warn "passphrase is still the public example one"
  if [ -f "$ENROLLMENT" ] && [ "$DIARIZE" = "1" ]; then
    ok "owner voice enrolled ($ENROLLMENT): voice check on (display only)"
  elif [ -f "$ENROLLMENT" ]; then
    note "owner voice enrolled, but stt.mac.diarize has no command: voice check off"
  else
    note "owner voice not enrolled: voice check off (python3 -m hardware.owner_voice enroll)"
  fi

  # ---- 6. the verdict ----------------------------------------------------------------------
  echo "  ------------------------------------------------------------"
  if [ "$N_FAIL" != "0" ]; then
    printf '  \033[1;31m  NOT READY: %s FAIL\033[0m, %s warn, %s ok. Nothing started.\n' "$N_FAIL" "$N_WARN" "$N_OK"
    printf '  Fix:%s\n' "$FAIL_LINES"
    echo "  (FARADAY_SKIP_CHECKS=1 starts anyway.)"
    echo "  ============================================================"
    exit 1
  fi
  printf '  \033[1;32m  READY\033[0m: %s ok, %s warn.\n' "$N_OK" "$N_WARN"
  echo "  ============================================================"
fi

if [ "${FARADAY_DRY_RUN:-0}" = "1" ]; then
  echo "Dry run: would start: $PY -m integration $FRESH ${APP_ARGS[*]:-}"
  exit 0
fi
echo "Starting: $PY -m integration $FRESH ${APP_ARGS[*]:-}"
# shellcheck disable=SC2086
exec "$PY" -m integration $FRESH ${APP_ARGS[@]+"${APP_ARGS[@]}"}   # bash 3.2 (macOS) safe
