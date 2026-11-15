# Faraday

An AI home guard that runs on one Mac mini and never uses the internet.

It answers the door, tells you who's there, takes messages, passes on your instructions to couriers and visitors, warns off people who loiter, runs the lights, learns simple rules when you say them out loud, and tells you what happened while you were gone. Nothing leaves the box. And the AI can't unlock your house, ever.

I built it, solo, for the Cornell Local AI Hackathon.

> Status: more than 600 automated tests, and none of them need hardware: they replay real radio captures and use stand-in devices. **The whole app has run on real hardware**: first on a Windows laptop (real doorbell and motion sensors through the Flipper, the real lamps, the alarm), then a full-system test on the Mac mini with the 9B, the 2B, Argmax speech and the camera. Everything that test found is fixed, and the re-test on the Mac mini is next. Anything marked **[TBD]** hasn't been proven on the Mac mini yet.

## Why offline

Most smart doorbells and cameras send video of your front walkway to a company's server. Someone else stores footage of you, your family and everyone who visits, and if their servers go down or your internet drops, the camera gets a lot less smart.

Faraday does all of it on one computer in your house: the radio, the object detector, the AI, the speech and the dashboard. Nothing in it calls the internet. A real home has Wi-Fi, and Faraday simply never uses it; for the hackathon it runs with the Wi-Fi switched off. **[TBD: full run on the Mac mini with Wi-Fi off]**

## How it works

```
 doorbell / motion sensor  --433 MHz-->  Flipper Zero  --USB-->  Mac mini
 camera on the front walkway --> small object detector + Bonsai 9B -->  |
 mic --> Argmax: hears ("Hey Faraday", visitors), counts voices ---->   |
                                                                        v
          plain code: modes, alerts, lights, photos, passphrase (always happens)
          Bonsai 2B: judges each photo, picks the owner's request
          Bonsai 9B: talks to visitors, sums up the day
                                                                        |
 speaker <-- Argmax text-to-speech, one calm voice <--------------------+
 outlets <-- Flipper sends 433 MHz <------------------------------------+
 screen / phone <-- local dashboard (live camera) and Controls page <---+
```

### The AI pipeline

**A small detector spots what's in frame, the 9B describes, the 2B judges, Argmax hears and speaks, and code guarantees safety.** Each part does the job it's best at, and nothing about safety depends on any model.

- **Spots: a small object detector** (YOLOX-tiny, no LLM) runs on the live camera and on every photo. It counts people and objects and draws the boxes you see on the dashboard. A face detector counts visible faces.
- **Describes: Bonsai 9B** writes one or two factual sentences about each photo: how many people, what they hold, whether the face is visible, what they're doing. It gets the detector's counts as a hint. I checked that Bonsai really looks at images: given a test picture of a green triangle on black, it said exactly that, and asked the same thing with no image, it made something up.
- **Judges: Bonsai 2B** labels each photo delivery, visitor, suspicious or nobody, from the description, the detector, the mode and the doorbell and motion history. Code keeps it honest:
  - zero people detected is always "nobody";
  - if vision failed, the label is "unknown: couldn't analyse the photo", never a guess;
  - someone who walked up and rang with a plain face is a visitor or a delivery;
  - someone who stayed at the door without ringing while armed is suspicious;
  - the reason must name what was seen ("1 person, holding a box", "face covered") or the context ("stayed at the door without ringing while armed"), or code writes it from the facts.
- **Picks the owner's request: Bonsai 2B.** After "Hey Faraday", the 2B chooses one of a fixed set: arm Away or Night, a light on or off, "who's at the door?", "what happened?", a new rule or an instruction for visitors. Code checks the choice against the words. Anything else gets "Sorry, I didn't catch that." Disarming is never the AI's choice: only the passphrase does that, checked in plain code before any model sees the words.
- **Talks: Bonsai 9B** replies to what a visitor says, passes on your instructions, writes one line to an intruder about what it sees, and sums up the day.
- **Hears and speaks: Argmax.** Speech-to-text after the wake phrase and after the doorbell, voice counting for visitors, and text-to-speech in one calm, professional voice. Fixed lines are rendered once at startup, so they play instantly.
- **Guarantees safety: plain code.** Modes, alerts, lights, photos and the passphrase are all plain code, and so is the check on every model answer. Details below.

On the Mac mini the models are `ternary-bonsai-2-9b-mlx` and `ternary-bonsai-2-2b-mlx` in LM Studio. The 9B MLX build thinks before answering even when told not to, so Faraday checks every model once at startup and uses prompts that can't think when needed. **[TBD: speed of each on the Mac mini]**

### The radio

Cheap 433 MHz sensors (a doorbell button and a motion sensor on the front walkway) send short fixed codes. A Flipper Zero plugged in by USB listens with its normal firmware, and the driver reads every code it reports.
- **Codes.** Each sensor has its own. The driver matches on the sensor's serial and ignores the button bits, because the doorbell sometimes sends a slightly different code for the same press.
- **Motion.** One wave sends 17 copies of the code over about 2 seconds, merged into one event.
- **Lights.** The Flipper replays a saved code to a cheap remote outlet. Outlet 1 is the porch light and outlet 2 the living room lamp.
- **Listening and sending.** The Flipper can't do both at once, so the driver pauses listening, sends, and starts again about 1.2 seconds later.
- **LED.** The Flipper's LED stays off. On an alarm it turns red and buzzes three times.
- **Reconnect.** If the USB cable drops, the driver reconnects by itself in about 8 seconds.

I tested the driver on its own with the real Flipper first, then the whole app on a Windows laptop, then on the Mac mini. There are also tests that replay real radio captures.

### The modes

Faraday is in Home, Away or Night. What always happens is written in plain code:

| | Home | Away | Night |
|---|---|---|---|
| Doorbell | "Someone is at the front door." | one fixed greeting at once ("Hello. No one can come to the door right now. I can take a message or help with a delivery."), a photo, then a reply to whatever the visitor says | porch light, photo, no sound |
| Motion | nothing | porch light at once, then 15 seconds for a doorbell | same as Away |
| No doorbell within those 15 s | | warning line, photo, the label, alert to WARNING | same as Away |
| Motion again 8 to 60 s after the warning, nobody rang | | ALARM: both lights, photo, the alarm line, a line about what the camera sees, a recording | same as Away |

A visitor who walks up and rings is never warned or alarmed. When you come home and say the passphrase, the lights Faraday turned on go off again; lights you switched on yourself stay on.

### What you can say

Start with **"Hey Faraday"**:
- **"I'm heading out." / "Goodnight."** arms the house in Away or Night.
- **"Who's at the door?"** Faraday says "Checking the camera.", takes a fresh photo and tells you what it sees, plus the label: "A courier holding a box at the front door. It looks like a delivery." If the camera or vision fails, it says "I can't see the door right now."
- **"If a delivery comes, ask them to leave it by the side door."** An instruction for visitors. The next courier who mentions a package hears "Please leave it by the side door." Instructions only change what Faraday *says*; anything about lights, modes, the alarm, locks or codes is refused. They're listed on the dashboard, where you can delete them, or say "forget the delivery instruction".
- **"When there's motion at night, turn on the living room lamp too."** becomes a rule.
- **"What happened while I was out?"** gets a short spoken summary.
- **"Turn on the porch light."**

While the house is armed, a "Hey Faraday" request changes nothing: only the passphrase does. "Turn off the alarm" gets "I'm sorry, I can't do that. Only the owner can disarm the system."

### Safety: AI for understanding, code for safety

- The AI can arm the house but can never disarm it. It picks from a fixed menu of 8 actions (set the mode, turn an outlet on or off, speak, take a photo, save a message, summarize the log, add a rule), and there's no "disarm" on it. Disarming only happens when someone says or types the passphrase, and that check is plain code.
- The AI never sees the passphrase. It's blanked out of speech before anything is logged, shown or sent to a model.
- Everything the AI picks goes through checks in code. Unknown actions, bad arguments and anything that would change the mode while armed get dropped.
- If a model errors, times out or says something useless, simple keyword rules take over, so the house never stalls.
- A visitor can only talk and leave a message: no rules, instructions, lights or summary, in any mode.
- The threat label can only make Faraday more careful. Owner instructions are words to pass on, never actions.

### Accessibility

- **Eyes at the door, for a blind or low-vision owner.** "Hey Faraday, who's at the door?" gets a spoken description of a fresh photo and the label. A doorbell at home is announced out loud. Everything else works by voice too.
- **Captions, for a deaf or hard-of-hearing owner.** Everything heard and said at the door is written out live: under "Heard & said" on the dashboard, and in big text in the **Live captions** card on the Controls page. Doorbells and alarms show as banners, and the label as a coloured pill.

### The dashboard and the Controls page

The local dashboard shows, on one screen:
- the live camera in the centre, with the detector's boxes, and under it the latest label, the objects the detector saw as small chips, and "Recording saved" during an alarm;
- the mode, with its own colour for each mode and alert;
- the activity, with photo thumbnails, and what Faraday heard and said (owner requests show whether the voice matched the owner's);
- the lights as real switches, the rules and instructions, and the messages;
- a small green mic while Faraday is listening.

The **Controls** button opens a phone-style page: the live camera, a **Snapshot** button, a big **Hold to talk to visitor** button that plays your voice on the door speaker, the live captions, and all the light switches. Both pages load nothing from the internet: no CDNs, no web fonts.

### Sustainability

One Mac mini, cheap sensors, and small models: a 2B for the quick jobs and a 9B only where it matters. No cloud servers, no subscription, no data center behind your doorbell. **[TBD: Mac mini power draw at idle and during a Bonsai call]**

## Run it

On the Mac mini, with the Flipper (no app open on it), the camera, the mic and the speaker plugged in. You need Python 3.9 or newer, LM Studio with both Bonsai models downloaded, and Argmax's `argmax-cli`. The object detector's models are in the repo (`hardware/models/`).

```
git clone https://github.com/Rayan1092/Faraday.git
cd Faraday
pip3 install -r requirements.txt
integration/start_mac.sh
```

The launcher does the rest:
- creates `config/settings.json` from the example if it's missing (set your own passphrase there);
- warns if the Wi-Fi is still on;
- starts LM Studio's server and loads `ternary-bonsai-2-9b-mlx` and `ternary-bonsai-2-2b-mlx` (through LM Studio's `lms` tool);
- starts `argmax-cli serve`;
- checks the voice, the detector, the camera, the mic, the speaker and the Flipper;
- starts Faraday with a fresh log (`--no-fresh` keeps the old one).

The dashboard is at http://127.0.0.1:8080, and the camera and intercom server is next to it on port 8081. `FARADAY_DRY_RUN=1 integration/start_mac.sh` only runs the checks.

**Talking to visitors from another device (Chrome trust step).** The Controls page uses the browser's microphone, and Chrome only allows that on a trusted address. On the Mac itself, `http://127.0.0.1:8080` already counts. From another computer or a phone on the home network:
1. Open `chrome://flags/#unsafely-treat-insecure-origin-as-secure`.
2. Add the dashboard address and the controls address, separated by a comma, for example `http://<mac-ip>:8080,http://<mac-ip>:8081`. The Controls page shows the exact two if the mic is blocked.
3. Set it to **Enabled**, then click **Relaunch**.

With the Wi-Fi off, other devices can't reach the Mac, so open the Controls page on the Mac itself.

**For developers.**
- **Simulator.** `python -m sim --offline --dashboard` runs the same pipeline and dashboard with typed keys instead of sensors (`py` on Windows). Leave out `--offline` to use Bonsai through LM Studio, with the models set in `config/settings.json` under `llm`. It's a development and test tool, not part of the product. In its terminal: `d` doorbell, `m` motion, `w` ends the 15-second wait for a doorbell, `t <words>` speech, `1`/`2`/`3` Home/Away/Night, `q` quits.
- **Windows.** On a Windows laptop, `py -m integration` runs the app with stand-ins for Argmax.
- **Tests.** `python -m unittest discover`.
- **Benchmarks.** `python -m brain.bench`, and on the Mac with real photos, `python -m brain.bench_photos captures/bench`.

## Limits

Being honest about these:

- **Cheap sensors use fixed codes.** The doorbell and motion sensor send the same code every time, so anyone with a radio like a Flipper could record and replay it. That could fake a doorbell press or trigger the alarm. It can't disarm anything, because disarming needs the passphrase. A real product should use rolling-code sensors.
- **The outlets work the same way.** Someone nearby could replay the "light on" code.
- **The cheap doorbell is flaky.** It has to be held for a second or two; a quick tap never registers. A new doorbell is going in. **[TBD: new doorbell results]**
- **The radio goes deaf for a moment.** It can't hear sensors for about 1.2 seconds while it switches an outlet, and about 2.5 seconds during the alarm buzz.
- **Small models make mistakes.** Before tuning, the 4B picked the right action on its own only 61% of the time. After building a benchmark (`python -m brain.bench`) and tuning the tools and prompts with it, the 4B was right on its own 97% of the time (136 of 140 runs) and 99% after the code checks, with nothing unsafe getting through. Full numbers are in [docs/bench/](docs/bench/README.md). It's still a small model, so code stays the safety net. The latest runs on the 4B:
  - the owner's requests, 39 of 39 after the code checks;
  - the threat label, 30 of 30, with every reason naming what was seen;
  - with the model thinking before every answer, 46 of 46;
  - the owner's delivery instruction, passed on 5 times out of 5 with code, against 1 in 5 for the model alone.
- **It's not instant.** On a laptop running the 4B, a Bonsai call takes about 3 seconds. Lights, photos, the doorbell greeting and the alarm never wait for the AI. **[TBD: timings on the Mac mini with the 9B and the 2B]**
- **The passphrase is spoken out loud.** Anyone who hears you say it could use it. A typed passphrase works on the dashboard too, and wrong guesses get locked out for 30 seconds after 5 tries.
- **The owner's voice check is display only.** It shows whether a request sounded like the owner, but it never decides anything: only the passphrase disarms.
- **Talking from another device needs the Chrome trust step**, and a network. On the Mac itself it just works.
- **One house, one door, one camera.** It doesn't call anyone or send alerts off the box.

## Repo map

| Folder | What's in it |
|---|---|
| `brain/` | Bonsai client, prompts, the owner's request router, the threat label, "who's at the door?", the summary, every spoken line, the main pipeline, the benchmarks |
| `modes/` | Home/Away/Night, alert levels, passphrase check |
| `rules/` | The mode table in code, rules you add by voice, and owner instructions for visitors |
| `log/` | Event log in `logs/events.jsonl`, with the passphrase blanked out |
| `dashboard/` | The local dashboard and the Controls page |
| `hardware/`, `integration/` | Flipper, camera, object detector, speech, intercom, the Mac launcher, and wiring it all together |
| `sim/` | Keyboard simulator, for development and tests only |
| `shared/` | Message formats every part agrees on |
| `docs/` | Plan, formats, team notes, submission material |

Building on it? Read [CLAUDE.md](CLAUDE.md), then [docs/PLAN.md](docs/PLAN.md) and [docs/FORMATS.md](docs/FORMATS.md).
