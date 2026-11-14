# hardware/flipper/

**Owner: NV laptop.** This is the Flipper Zero driver. It turns real 433 MHz radio into shared `doorbell`/`motion` events, and shared `outlet_on`/`outlet_off` actions and mode/alert events into real radio, LED and vibration.

## Use from integration/

```python
from hardware.flipper import Flipper

fl = Flipper(on_event=handle)      # or leave on_event out and read fl.events (queue.Queue)
fl.start()                         # connects (VID/PID autodetect, then setting) and listens
fl.execute(action)                 # outlet_on / outlet_off -> Future[(ok, output_lines)]
fl.apply_event(event)              # alert_changed -> alarm: LED red + buzz; else LED off
fl.stop()
```

`on_event` runs on its own thread, so slow handlers don't block the radio. Events are already validated `shared.Event`s with `source: "flipper"`.

## How it works

- **One thread owns the serial port.** It keeps `subghz rx 433920000 0` running, parses the 5-line Princeton blocks, and merges packets per sensor into one event. A burst ends after a 1.5 s gap (`flipper.burst_gap_s`).
- **Commands go through a queue.** For each batch it presses Ctrl+C, waits for the `>: ` prompt, runs the commands, then starts rx again. Sends never overlap. rx resumes about 1.2 s after an outlet send and about 0.35 s after an LED change.
- **Keys.** Doorbell `0x00DEC608` and motion `0x00C5AFAE` come from `sensors` in the settings. Princeton 24-bit sensors match on the 20-bit serial and ignore the 4-bit button, because the real doorbell sent both `…608` and `…601`. Unknown keys are logged and ignored.
- **Reconnect.** If the cable drops, the driver reconnects by itself (tested: about 8 s on Windows), restarts rx and puts the LED back (red if an alarm is on, otherwise off).
- **LED.** Off at all times, except at **alarm**: red plus three buzz pulses, and off again when the alarm ends (passphrase disarm). No mode or warning colours (decided 2026-11-14). The driver ignores `flipper.led` in the settings; that map is for the dashboard's on-screen colours.
- **Why no mode colours.** The Flipper's own charge indicator shares the LED's red and green (green charged, red charging, off not charging). `subghz rx` pauses charging, so each time listening restarts after a pause the firmware rewrites red and green about a second later, and this can't be switched off from the stock CLI. On the real device the mode colours were unreadable. "Off" is self-healing, so there is no LED traffic at all outside an alarm. Red is not, so during an alarm the driver re-sends it 2 s after every long pause (connect, outlet send, buzz; `flipper.led_settle_s`) and every 15 s (`flipper.led_refresh_s`), never in the middle of a burst.

## Tools

```
python -m hardware.flipper.manual_test          # live events; 1/2/3 outlets, c colours, h/a/n modes, w/x/r alerts, v buzz, l learn, q quit
python -m hardware.flipper.manual_test --learn  # learn a new remote: prints the exact "sensors" line to paste
python -m hardware.flipper.capture NAME          # record raw rx lines to samples/NAME.txt
python -m unittest hardware.flipper.test_flipper # parser, bursts, driver logic on the real samples
```

`samples/` holds real captures from this hardware: `doorbell_hold.txt` has 2 holds, `motion_wave.txt` has 2 waves of 17 packets each, and `help.txt` is the CLI command list.

## New doorbell (learn mode)

Doorbell and motion keys live in `sensors` in `config/settings.json`, and nothing is hardcoded. To switch to a new doorbell:
1. Run `manual_test --learn` and hold the new button for about 2 s.
2. Copy the printed `"doorbell": {...},` line over the old entry.
3. Restart.

## Known limits

- **Doorbell reception is marginal.** The doorbell must be *held* (a quick tap never decodes), and even holds decoded only about 1 in 3 late in testing, with raw rx just as bad. Motion decoded every time. See the handoff note.
- **Red can drop out for a moment during an alarm.** After the buzz, an outlet send or a reconnect it may be dark for about 2 s before red returns. The dashboard always shows the right status.
- **rx is deaf during sends and LED changes.** It's blind for about 1.2 s around each outlet send, about 2.5 s for the alarm buzz, and about 0.35 s per red re-send during an alarm. Outside an alarm there are no LED pauses.
