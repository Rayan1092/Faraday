# Faraday: notes for Claude and teammates

Faraday is an offline AI home guard built for the Cornell Local AI Hackathon. Submissions close at **noon Sunday, 2026-11-15**.

**Read [docs/PLAN.md](docs/PLAN.md) first, then [docs/FORMATS.md](docs/FORMATS.md).** Leave a handoff note in [docs/team/history/](docs/team/history/) after every task.

## Git rules (permanent)

- Commit as the configured git user only.
- Never add `Co-Authored-By`, "Generated with Claude", or any other Claude attribution to commits, PR titles, or PR bodies.
- Work on your own branch, keep PRs small, and pull before you start (see Collision rules in PLAN.md).

## Event rules (hard)

- The app must run on a Mac with **Wi-Fi off**. Nothing may call the internet at runtime. The final target is a Mac mini (Apple Silicon) with the Flipper, mic, camera and speaker plugged in.
- Development also happens on Windows laptops, so all Python must be cross-platform. Use `pathlib`, no shell-only tricks, and take OS-specific values from `config/settings.json`.
- **The only LLM is Bonsai 2 (2B/4B/9B)**, through LM Studio's OpenAI-compatible server. Keep prompts short, because long context uses a lot of RAM.
- **Speech uses Argmax OSS on the Mac.** STT is `argmax-cli serve` (OpenAI-compatible `/v1/audio/transcriptions`, default port 50060). TTS runs the argmax-cli TTS command as a subprocess. On Windows, use a stand-in behind the same interface.
- The AI chooses only from: `set_mode`, `outlet_on`, `outlet_off`, `speak`, `take_photo`, `save_message`, `summarize_log`, `add_rule`. **The AI can never disarm.** Disarming is a passphrase check in plain code in `modes/`.
- Alert rule (in `modes/`, not the AI): the first motion in Away or Night sets `warning`. Motion again within 60 s sets `alarm`. Only the passphrase disarm returns it to `normal`. Every change sends an `alert_changed` event.

## Hardware facts

- Flipper Zero, stock firmware, USB CLI. The serial port comes from settings: `COM5` on the Windows dev laptop, `/dev/cu.usbmodemflip_*` on the Mac. The CLI fails if an app is open on the Flipper.
- Receive: `subghz rx 433920000 0`
- Doorbell: Princeton 24-bit key `0x00DEC608`, needs about a 1 s hold.
- Motion: Princeton 24-bit key `0x00C5AFAE`, about 17 packets per trigger, merged into one event.
- Send: `subghz tx_from_file /ext/subghz/<name>.sub 10 0` with `outlet1_on/off`, `outlet2_on/off`, `outlet3_on/off`. The Flipper can't listen while sending, so pause rx, send, then resume rx.
- Outlet 1 is the porch light, outlet 2 the living room lamp, outlet 3 spare.
- Flipper LED: **off at all times, except at ALARM: red plus three buzzes**, back to off when the passphrase disarms. No mode colours (decided 2026-11-14: the firmware's charge indicator owns the LED's red and green while listening, so mode colours were unreadable). The dashboard is the status display.

## Ownership

| Folder | Owner |
|---|---|
| `hardware/` (flipper, audio, camera, speech clients), `integration/` | NV laptop |
| `brain/`, `modes/`, `log/`, `rules/`, `dashboard/`, `sim/`, `docs/submission/` | Dell laptop |
| `shared/`, `config/` | Both. Change only by agreement |
| `docs/PLAN.md`, `docs/FORMATS.md`, `CLAUDE.md` | Both. Change only by agreement |

Don't edit another laptop's folders. If you need a change there, add a note to Open Questions in PLAN.md or ask.

## Practical

- `pip install -r requirements.txt`
- Copy `config/settings.example.json` to `config/settings.json` and edit it. Never commit `settings.json`; it holds the passphrase.
- Runtime output goes in `captures/` and `logs/`, both gitignored. Note that `log/` is code and `logs/` is data.
- `shared/` is stdlib-only. Build messages with `make_event` / `make_action`, which validate them.
- Format tests: `python -m unittest shared.test_formats`. They also check every JSON example in FORMATS.md, so run them after any change to `shared/` or FORMATS.md.
