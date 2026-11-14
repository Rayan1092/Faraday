# Faraday

A home guard that runs on one Mac mini with the Wi-Fi turned off.

It answers the door, takes messages, turns on lights, learns simple rules when you say them out loud, and tells you what happened while you were gone. Nothing leaves the box. And the AI can't unlock your house, ever.

We built it for the Cornell Local AI Hackathon.

> Status: the software runs end to end in our simulator. There are 214 passing tests: 186 for the software and 28 for the radio driver, which replay real radio captures. The Flipper radio driver works on real hardware (on a Windows laptop). The full run on the Mac mini is next. Anything marked **[TBD]** hasn't been proven on real hardware yet.

## Why offline

Most smart doorbells and cameras send video of your front door to a company's server. That means someone else stores footage of you, your family and everyone who visits. If their servers go down or your internet drops, the camera gets a lot less smart.

Faraday does all of it on one computer in your house. The radio, the AI, the speech and the dashboard all run locally. Nothing in it calls the internet, so it should keep working with no network at all. We'll show that by turning the Wi-Fi off at the start of the demo. **[TBD: full run on the Mac mini with Wi-Fi off]**

## How it works

```
 doorbell / motion sensor  --433 MHz-->  Flipper Zero  --USB-->  Mac mini
 mic --> Argmax speech-to-text ------------------------------>   |
                                                                  v
                      plain code: modes, alerts, lights, photos (always happens)
                      Bonsai 2 in LM Studio: what to say, messages, rules (on top)
                                                                  |
 speaker <-- Argmax text-to-speech <------------------------------+
 outlets <-- Flipper sends 433 MHz <------------------------------+
 projector <-- local dashboard (http://127.0.0.1:8080) <----------+
```

**The radio.** Cheap 433 MHz sensors (a doorbell button and a motion sensor) send short fixed codes. A Flipper Zero plugged in by USB listens with its normal firmware, and our driver reads every code it reports.
- **Codes.** Each sensor has its own. We match on the sensor's serial and ignore the button bits, because our doorbell sometimes sends a slightly different code for the same press.
- **Motion.** One wave sends 17 copies of the code over about 2 seconds, and we merge them into one event.
- **Lights.** The Flipper replays a saved code to a cheap remote outlet. Outlet 1 is the porch light and outlet 2 the living room lamp.
- **Listening and sending.** The Flipper can't do both at once. So the driver pauses listening, sends, and starts again about 1.2 seconds later.
- **LED.** The Flipper's LED shows the mode (green, blue, purple), or the alert colour (yellow, red), and it buzzes three times on an alarm.
- **Reconnect.** If the USB cable drops, the driver reconnects by itself in about 8 seconds.

We tested all of that on the real Flipper on a Windows laptop, with 25 tests that replay real radio captures. **[TBD: same check on the Mac mini]**

**The modes.** Faraday is in Home, Away or Night. Each mode has a table of what always happens, written in plain code:

| | Home | Away | Night |
|---|---|---|---|
| Doorbell | says "Someone is at the door" | photo, then the AI greets the visitor and offers to take a message | porch light, photo, no sound |
| Motion (first time) | nothing | porch light, photo, "You're on camera" | porch light, photo |
| Motion again within a minute (alarm) | nothing | lamp too, alarm line, then one AI line about what it sees | lamp too, then one AI line about what it sees |

The lights and photos come from code, not the AI, so they happen even if the model is slow or wrong.

**The AI.** We use Bonsai 2, a small model that runs locally in LM Studio. It gets the mode, the new event and the last few log lines, and picks from a fixed menu of 8 actions: set the mode, turn an outlet on or off, speak, take a photo, save a message, summarize the log, or add a rule. There is no "disarm" on the menu. We use it for the parts that need understanding: talking to visitors, saving their message, turning "when there's motion at night, turn on the lamp too" into a rule, and summarizing the day. We tested with the 4B model on a laptop. The plan is the 9B on the Mac mini. **[TBD: 9B model id and speed on the Mac mini]**

**Speech.** Argmax runs speech-to-text and text-to-speech on the Mac. Faraday hears you through the mic and answers through the speaker. **[TBD: confirm the Argmax commands on the Mac]**

**The camera.** When something happens, Faraday takes a photo, shows it on the dashboard right away, and asks Bonsai for a one-line description. We checked that the 4B Bonsai in LM Studio really looks at images: it correctly described a test picture of a green triangle. Big photos are slow (a full HD image timed out), so we shrink them to 640 px first. **[TBD: real camera photos on the Mac mini]**

**Safety: AI for understanding, code for safety.**
- The AI can arm the house but can never disarm it. Disarming only happens when someone says or types the passphrase, and that check is plain code.
- The AI never sees the passphrase. It's blanked out of speech before anything gets logged or shown.
- Everything the AI picks goes through checks in code. Unknown actions, bad arguments and anything that would change the mode while armed get dropped.
- If the model errors, times out or says something useless, simple keyword rules take over, so the demo never freezes.
- In Away mode whoever is talking is a visitor, so they can only leave a message. They can't add rules, switch lights or hear the summary.

**The dashboard.** A big local web page for the projector shows:
- the mode, with the same colour as the Flipper's LED,
- live events,
- the latest photo,
- what Faraday heard and said,
- which lights are on, the rules and the messages.

It loads nothing from the internet. No CDNs, no web fonts.

## Run it

You need Python 3.9 or newer. The simulator and dashboard only use the standard library.

```
git clone https://github.com/Rayan1092/Faraday.git
cd Faraday
cp config/settings.example.json config/settings.json    # then set your own passphrase
```

**Try it without any hardware or AI.** This uses simple rules instead of Bonsai:

```
python -m sim --offline --dashboard
```

The dashboard opens in your browser. In the terminal, type a key and press Enter:
- `d` is the doorbell, and `m` is motion.
- `t <words>` is something someone said.
- `1`, `2`, `3` switch to Home, Away and Night. Home asks for the passphrase.
- `q` quits.

**With Bonsai.** Install LM Studio, load a Bonsai 2 model and start its local server. Put the server address and model name in `config/settings.json` under `llm`. Then run:

```
python -m sim --dashboard
```

On Windows use `py` instead of `python`.

**On the Mac mini with the real hardware.** **[TBD: the start command once integration/ is merged]**. Plug in the Flipper (close any app on it first), the camera, the mic and the speaker. Start LM Studio with the 9B model and raise its Parallel setting above 1, so a background call doesn't wait in line. Start Argmax. Then turn the Wi-Fi off.

**Tests:** `python -m unittest discover`

## Limits

We'd rather be honest about these:

- **Cheap sensors use fixed codes.** The doorbell and motion sensor send the same code every time, so anyone with a radio like a Flipper could record and replay it. That could fake a doorbell press or trigger the alarm. It can't disarm anything, because disarming needs the passphrase. A real product should use rolling-code sensors.
- **The outlets work the same way.** Someone nearby could replay the "light on" code.
- **Our cheap doorbell is flaky.** It has to be held for a second or two, because a quick tap never registers. Late in testing, even holds only got through about 1 time in 3, with or without our code. We're swapping in a new doorbell. **[TBD: new doorbell results]** Until then, the dashboard has a "Ring doorbell" button that sends the same event, and the log marks it as coming from the dashboard.
- **The radio goes deaf for a moment.** It can't hear sensors for about 1.2 seconds while it switches an outlet, and about 2.5 seconds during the alarm buzz.
- **Small models make mistakes.** Before tuning, the 4B picked the right action on its own only 61% of the time. It kept trying to switch to Home mode when you said "I'm heading out". We built a benchmark (`python -m brain.bench`) and tuned the tools and prompts with it. After tuning it was right on its own 97% of the time (136 of 140 runs), including reworded sentences we never tuned on. Code fixed most of the rest, for 99% final. Nothing unsafe got through in any run. Full numbers are in [docs/bench/](docs/bench/README.md). It's still a small model, so code stays the safety net.
- **It's not instant.** On a laptop running the 4B model, a Bonsai call took 2.5 seconds on average and 5.7 seconds at worst in the benchmark. So lights and photos never wait for the AI, and Faraday says "One moment, please" to visitors while it thinks. **[TBD: timings on the Mac mini with the 9B]**
- **The passphrase is spoken out loud.** Anyone who hears you say it could use it. We also accept a typed passphrase on the dashboard, and wrong guesses get locked out for 30 seconds after 5 tries.
- **One house, one door, one camera.** It's a demo setup, not a full security system. It doesn't call anyone or send alerts off the box.

## Repo map

| Folder | What's in it |
|---|---|
| `brain/` | Bonsai client, prompt, checks, fallback rules, summary, the main pipeline |
| `modes/` | Home/Away/Night, alert levels, passphrase check |
| `rules/` | The mode table in code, plus rules you add by voice |
| `log/` | Event log in `logs/events.jsonl`, with the passphrase blanked out |
| `dashboard/` | The offline web page |
| `sim/` | Keyboard simulator for testing without hardware |
| `hardware/`, `integration/` | Flipper, camera, speech, and wiring it all together |
| `shared/` | Message formats every part agrees on |
| `docs/` | Plan, formats, team notes, submission material |

Building on it? Read [CLAUDE.md](CLAUDE.md), then [docs/PLAN.md](docs/PLAN.md) and [docs/FORMATS.md](docs/FORMATS.md).
