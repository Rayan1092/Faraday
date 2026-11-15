#!/usr/bin/env bash
# Start Faraday on the Mac mini.
#
#   integration/start_mac.sh                 # checks, then: python3 -m integration
#   integration/start_mac.sh --offline       # extra args go to the app
#
# Checks first (fails fast with a fix-it hint):
#   - config/settings.json exists (created from the example with llm.profile "mac" if not)
#   - LM Studio answers at the profile's base_url and lists the model
#   - the Argmax STT server (argmax-cli serve) answers at stt.mac.url
# Warns (doesn't stop): Wi-Fi on, profile not "mac", argmax-cli missing,
# Flipper port missing, example passphrase.
#
# Env: PYTHON (default python3), FARADAY_SETTINGS (default config/settings.json),
#      FARADAY_SKIP_CHECKS=1, FARADAY_DRY_RUN=1 (check only, don't start).
set -u
cd "$(dirname "$0")/.." || exit 1

PY="${PYTHON:-python3}"
SETTINGS="${FARADAY_SETTINGS:-config/settings.json}"
ok()   { printf '  \033[32mOK\033[0m   %s\n' "$1"; }
warn() { printf '  \033[33mWARN\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILED=1; }
FAILED=0

echo "Faraday Mac start ($(date '+%H:%M:%S'))"

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
s = json.load(open(sys.argv[1], encoding="utf-8"))
llm = s["llm"]; prof = llm["profiles"].get(llm["profile"], {})
stt = s.get("stt", {}).get("mac", {}) or {}
tts = s.get("tts", {}).get("mac", {}) or {}
ex = json.load(open("config/settings.example.json", encoding="utf-8"))
vals = {
    "PROFILE": llm["profile"],
    "LLM_URL": str(prof.get("base_url", "")).rstrip("/"),
    "LLM_MODEL": prof.get("model", ""),
    "FAST_MODEL": prof.get("fast_model") or "",
    "STT_URL": stt.get("url", "") if stt.get("kind", "argmax") != "standin" else "",
    "TTS_BIN": (tts.get("command") or [""])[0],
    "PORT_GLOB": (s.get("flipper", {}).get("serial_port", {}) or {}).get("mac", ""),
    "EXAMPLE_PASS": "1" if s["security"]["passphrase"] == ex["security"]["passphrase"] else "0",
}
for k, v in vals.items():
    print(f"{k}={shlex.quote(str(v))}")
EOF
)" || { echo "cannot read $SETTINGS"; exit 1; }
eval "$VALUES"

if [ "${FARADAY_SKIP_CHECKS:-0}" != "1" ]; then
  # Wi-Fi (the event rule: run with Wi-Fi off)
  if command -v networksetup >/dev/null 2>&1; then
    WIFI_DEV="$(networksetup -listallhardwareports 2>/dev/null | awk '/Wi-Fi|AirPort/{getline; print $2; exit}')"
    if [ -n "$WIFI_DEV" ] && networksetup -getairportpower "$WIFI_DEV" 2>/dev/null | grep -q "On"; then
      warn "Wi-Fi is ON ($WIFI_DEV). Turn it off for the demo."
    else
      ok "Wi-Fi off"
    fi
  fi

  [ "$PROFILE" = "mac" ] && ok "llm profile mac" || warn "llm.profile is '$PROFILE', not 'mac'"

  # LM Studio: the profile's model (smart, 9B) and fast_model (2B) must both be
  # downloaded AND loaded. /v1/models lists every downloaded model; /api/v0/models
  # says which are loaded. Only Bonsai 2 may run (event rule), and the Mac also has
  # 27B models installed, so anything else loaded gets an "eject" warning.
  # (-f: a 404 from an older LM Studio gives no body -> "can't tell", not "nothing loaded")
  MODELS="$(curl -s -m 5 "$LLM_URL/v1/models" 2>/dev/null)"
  V0="$(curl -s -f -m 5 "$LLM_URL/api/v0/models" 2>/dev/null)"
  if [ -z "$MODELS" ]; then
    fail "LM Studio not answering at $LLM_URL. Start its server (Developer tab, or: lms server start) and load $LLM_MODEL${FAST_MODEL:+ and $FAST_MODEL}."
  else
    REPORT="$(MODELS="$MODELS" V0="$V0" "$PY" - "$LLM_MODEL" "$FAST_MODEL" "$LLM_URL" <<'EOF'
import json, os, sys
need = [m for m in dict.fromkeys(sys.argv[1:3]) if m]
url = sys.argv[3]

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

  # Argmax STT server: any HTTP answer means it's up
  if [ -n "$STT_URL" ]; then
    STT_BASE="$(printf '%s' "$STT_URL" | sed -E 's#^(https?://[^/]+).*#\1#')"
    CODE="$(curl -s -m 5 -o /dev/null -w '%{http_code}' "$STT_BASE/" 2>/dev/null)"
    if [ -n "$CODE" ] && [ "$CODE" != "000" ]; then
      ok "Argmax STT answering at $STT_BASE (HTTP $CODE)"
    else
      fail "Argmax STT not answering at $STT_BASE. Start it: argmax-cli serve"
    fi
  else
    warn "no stt.mac.url: push-to-talk has no STT (typed text still works)"
  fi

  if [ -n "$TTS_BIN" ] && command -v "$TTS_BIN" >/dev/null 2>&1; then
    ok "TTS command found: $TTS_BIN"
  else
    warn "TTS command '$TTS_BIN' not found: Faraday will print instead of speaking"
  fi

  # shellcheck disable=SC2086
  if [ -n "$PORT_GLOB" ] && ls $PORT_GLOB >/dev/null 2>&1; then
    ok "Flipper port present: $(ls $PORT_GLOB | head -1)"
  else
    warn "no Flipper at $PORT_GLOB yet (plug it in, close apps on it; the app keeps retrying)"
  fi

  [ "$EXAMPLE_PASS" = "1" ] && warn "passphrase is still the public example one"

  if [ "$FAILED" != "0" ]; then
    echo "Not starting: fix the FAIL lines above (or FARADAY_SKIP_CHECKS=1 to start anyway)."
    exit 1
  fi
fi

if [ "${FARADAY_DRY_RUN:-0}" = "1" ]; then
  echo "Dry run: would start: $PY -m integration $*"
  exit 0
fi
echo "Starting: $PY -m integration $*"
exec "$PY" -m integration "$@"
