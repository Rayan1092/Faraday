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

  # LM Studio
  MODELS="$(curl -s -m 5 "$LLM_URL/v1/models" 2>/dev/null)"
  if [ -z "$MODELS" ]; then
    fail "LM Studio not answering at $LLM_URL. Start its server (Developer tab, or: lms server start) and load $LLM_MODEL."
  elif printf '%s' "$MODELS" | "$PY" -c "import json,sys; ids=[m['id'] for m in json.load(sys.stdin).get('data',[])]; sys.exit(0 if sys.argv[1] in ids else 1)" "$LLM_MODEL"; then
    ok "LM Studio at $LLM_URL has $LLM_MODEL"
  else
    fail "LM Studio answers but has no model '$LLM_MODEL'. Load it, or fix llm.profiles.$PROFILE.model (see: curl $LLM_URL/v1/models)."
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
