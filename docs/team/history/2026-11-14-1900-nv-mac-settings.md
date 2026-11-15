# Mac mini: exact `config/settings.json` (NV, for the next Mac session)

Faraday is cloned to `~/Faraday` with the requirements installed. `config/settings.json` is gitignored, so copy the block below into `~/Faraday/config/settings.json` each time the clone is fresh. Only one line needs your own value: `security.passphrase`.

Facts this is built on (owner, 2026-11-14):
- LM Studio server at `http://127.0.0.1:1234`, Bonsai 9B loaded, exact model id **`ternary-bonsai-2-9b-mlx`**, Parallel left at 1.
- The Mac also has `qwen/qwen3.8-27b` and `prism-ml/bonsai-27b` installed. **They are not allowed** (event rule: Bonsai 2 only). Every request names `ternary-bonsai-2-9b-mlx` exactly, and `start_mac.sh` fails if that isn't the loaded model.
- Argmax STT: `.build/release/argmax-cli serve` at `http://127.0.0.1:50060`, model `large-v3-v20240930_626MB`.
- Argmax TTS: `argmax-cli tts --text "..." --speaker aiden --play` (streams to the speaker, starts almost instantly).

```json
{
  "integration": {"fake_flipper": false},

  "flipper": {
    "serial_port": {"windows": "COM5", "mac": "/dev/cu.usbmodemflip_*"},
    "baud": 230400,
    "rx_command": "subghz rx 433920000 0",
    "tx_command": "subghz tx_from_file /ext/subghz/{name}.sub 10 0",
    "burst_gap_s": 1.5,
    "led_refresh_s": 15,
    "led": {"alarm": "red"}
  },

  "sensors": {
    "doorbell": {"protocol": "Princeton", "bits": 24, "key": "0x00DEC608"},
    "motion":   {"protocol": "Princeton", "bits": 24, "key": "0x00C5AFAE", "merge_window_s": 5}
  },

  "alerts": {"alarm_window_s": 60},

  "outlets": {
    "porch_light":      {"number": 1, "on": "outlet1_on", "off": "outlet1_off"},
    "living_room_lamp": {"number": 2, "on": "outlet2_on", "off": "outlet2_off"},
    "spare":            {"number": 3, "on": "outlet3_on", "off": "outlet3_off"}
  },

  "llm": {
    "profile": "mac",
    "profiles": {
      "mac":       {"base_url": "http://127.0.0.1:1234", "model": "ternary-bonsai-2-9b-mlx",
                    "fast_model": "ternary-bonsai-2-2b-mlx"},
      "nv_backup": {"base_url": "http://127.0.0.1:1234", "model": "ternary-bonsai-2-4b@q2_0",
                    "fast_model": "ternary-bonsai-2-4b@q2_0"}
    },
    "max_tokens": 200,
    "temperature": 0.2,
    "timeout_s": 30,
    "recent_log_lines": 8,
    "reasoning_effort": "none"
  },

  "stt": {
    "mac": {
      "kind": "argmax",
      "url": "http://127.0.0.1:50060/v1/audio/transcriptions",
      "model": "large-v3-v20240930_626MB",
      "language": "en",
      "diarize": {
        "command": ["/Users/captain/clueless/vendor/argmax-oss-swift/.build/release/argmax-cli",
                    "diarize", "--audio-path", "{wav}"],
        "timeout_s": 20,
        "grace_s": 0
      }
    },
    "windows": {"kind": "standin"}
  },

  "tts": {
    "mac": {
      "kind": "argmax",
      "command": ["/Users/captain/clueless/vendor/argmax-oss-swift/.build/release/argmax-cli",
                  "tts", "--text", "{text}", "--speaker", "aiden", "--play"]
    },
    "windows": {"kind": "standin"}
  },

  "audio": {
    "mic_device": null, "speaker_device": null, "sample_rate": 16000, "max_record_s": 8,
    "listen": {"max_s": 15, "silence_s": 1.8, "no_speech_s": 8, "after_speech_s": 0.3},
    "wake": {"enabled": true, "phrase": "hey faraday", "silence_s": 0.7, "max_s": 10}
  },

  "camera": {"index": 0, "width": 1280, "height": 720},

  "security": {"passphrase": "YOUR PASSPHRASE HERE, plain words, no digits"},

  "dashboard": {"host": "127.0.0.1", "port": 8080},

  "paths": {"captures": "captures", "logs": "logs"}
}
```

## Mac session, in order
1. `cd ~/Faraday && git pull`
2. Paste the block above into `config/settings.json` and set the passphrase. If the file already exists, at least replace the `llm`, `stt` and `tts` sections.
3. LM Studio: load **only** `ternary-bonsai-2-9b-mlx` and `ternary-bonsai-2-2b-mlx` (Dell's #27: the 2B does the threat label and quick commands). Eject the 27B models; they're not allowed and use RAM. The 2B id comes from Dell's settings and isn't confirmed on the Mac yet; `start_mac.sh` says so if it's wrong. Start the server. Set Parallel above 1 if the alarm confrontation queues behind other calls (PLAN mode table note).
4. `.build/release/argmax-cli serve` in its own terminal.
5. Wi-Fi off.
6. `integration/start_mac.sh`. It fails with a fix-it line if LM Studio or Argmax don't answer, or if the 9B or the 2B isn't downloaded and loaded. Any other loaded model gets an eject warning. It warns about Wi-Fi, a missing Flipper port and the example passphrase.
7. First run of each device: macOS asks for Terminal's camera and microphone access; allow both.

## Checks that still need the Mac
- `argmax-cli diarize --help`: confirm the flags in `stt.mac.diarize.command` (the `--audio-path` flag is a guess) and what it prints. Then time it next to STT; if it's usually only a little slower, raise `grace_s` to e.g. 0.5.
- Hands-free: ring the doorbell, answer after the greeting; say "Hey Faraday, what happened while I was out?"; say just "Faraday" and check nothing happens.
- Controls page `http://127.0.0.1:8081/`: live camera, hold-to-talk to the door speaker (door mic muted meanwhile), lights, Listen.
- `python3 -m hardware.check camera`, `say "Hello"`, `record 4` (real STT), `mute`.
- `python3 -m integration`: push-to-talk `r` with real speech, the passphrase spoken, and the spoken lines through `--play`.
- Bonsai 9B vision on a real photo (PLAN Q2 is confirmed only for the 4B).
