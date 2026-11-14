# hardware/flipper/

**Owner: NV laptop.** This is the Flipper Zero driver. It turns real 433 MHz radio into shared `doorbell`/`motion` events, and shared `outlet_on`/`outlet_off` actions and mode/alert events into real radio, LED and vibration.

## Use from integration/

```python
from hardware.flipper import Flipper

fl = Flipper(on_event=handle)      # or leave on_event out and read fl.events (queue.Queue)
fl.start()                         # connects (VID/PID autodetect, then setting) and listens
fl.execute(action)                 # outlet_on / outlet_off -> Future[(ok, output_lines)]
fl.apply_event(event)              # mode_changed / alert_changed -> LED (+ buzz on alarm)
fl.stop()
```

`on_event` runs on its own thread, so slow handlers don't block the radio. Events are already validated `shared.Event`s with `source: "flipper"`.

## How it works

- **One thread owns the serial port.** It keeps `subghz rx 433920000 0` running, parses the 5-line Princeton blocks, and merges packets per sensor into one event. A burst ends after a 1.5 s gap (`flipper.burst_gap_s`).
- **Commands go through a queue.** For each batch it presses Ctrl+C, waits for the `>: ` prompt, runs the commands, then starts rx again. Sends never overlap. rx resumes about 1.2 s after an outlet send and about 0.35 s after an LED change.
- **Keys.** Doorbell `0x00DEC608` and motion `0x00C5AFAE` come from `sensors` in the settings. Princeton 24-bit sensors match on the 20-bit serial and ignore the 4-bit button, because the real doorbell sent both `…608` and `…601`. Unknown keys are logged and ignored.
- **Reconnect.** If the cable drops, the driver reconnects by itself (tested: about 8 s on Windows), re-applies the LED and restarts rx.
- **LED.** Colours are green Home, blue Away, purple Night (`r140 b255`), yellow warning, red alarm, and the alert colour beats the mode colour. The stock firmware resets the LED by itself (after `vibro`, and after a few idle minutes), so the driver re-sends the colour every 15 s (`flipper.led_refresh_s`), but never in the middle of a burst. Alarm gives three buzz pulses.

## Tools

```
python -m hardware.flipper.manual_test          # live events; 1/2/3 outlets, c colours, h/a/n modes, w/x/r alerts, v buzz, q quit
python -m hardware.flipper.capture NAME          # record raw rx lines to samples/NAME.txt
python -m unittest hardware.flipper.test_flipper # parser, bursts, driver logic on the real samples
```

`samples/` holds real captures from this hardware: `doorbell_hold.txt` has 2 holds, `motion_wave.txt` has 2 waves of 17 packets each, and `help.txt` is the CLI command list.

## Known limits

- **Doorbell reception is marginal.** The doorbell must be *held* (a quick tap never decodes), and even holds decoded only about 1 in 3 late in testing, with raw rx just as bad. Motion decoded every time. See the handoff note.
- **Brief LED flickers around the alarm buzz.** The firmware puts its own LED colour back for a moment during `vibro`.
- **rx is deaf during sends and LED changes.** It's blind for about 1.2 s around each outlet send, about 0.35 s per LED change or refresh, and about 2.5 s for the alarm buzz.
