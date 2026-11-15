# Faraday

A home guard that runs on one Mac mini with the Wi-Fi turned off.

It answers the door, tells you who's there, takes messages, turns on lights, learns simple rules and instructions when you say them out loud, and tells you what happened while you were gone. Nothing leaves the box. And the AI can't unlock your house, ever.

We built it for the Cornell Local AI Hackathon.

> Status: 423 automated tests pass, and none of them need hardware: 283 for the core software and 140 for the hardware drivers and the app wiring, which replay real radio captures and use fake devices. **The whole app has run on real hardware on a Windows laptop (Join 1 passed).** Real doorbell and motion sensors go through the Flipper into our software, Bonsai decides, the real lamps switch, and a second wave within a minute sets off the alarm. The full run on the Mac mini, with the 9B, the 2B and Argmax speech, is next. Anything marked **[TBD]** hasn't been proven on the Mac mini yet.

## Why offline

Most smart doorbells and cameras send video of your front door to a company's server. That means someone else stores footage of you, your family and everyone who visits. If their servers go down or your internet drops, the camera gets a lot less smart.

Faraday does all of it on one computer in your house. The radio, the AI, the speech and the dashboard all run locally. Nothing in it calls the internet, so it should keep working with no network at all. We'll show that by turning the Wi-Fi off at the start of the demo. **[TBD: full run on the Mac mini with Wi-Fi off]**

## How it works

```
 doorbell / motion sensor  --433 MHz-->  Flipper Zero  --USB-->  Mac mini
 mic --> Argmax: hears, counts voices ----------------------->   |
 camera --> Bonsai 9B: sees ---------------------------------->   |
                                                                  v
          plain code: modes, alerts, lights, photos, passphrase (always happens)
          Bonsai 2B: judges each photo (delivery / visitor / suspicious / nobody)
          Bonsai 9B: talks to visitors, passes on your instructions, sums up the day
                                                                  |
 speaker <-- Argmax text-to-speech <------------------------------+
 outlets <-- Flipper sends 433 MHz <------------------------------+
 projector / phone <-- local dashboard and Controls page <--------+
```

### The AI pipeline

**The 9B sees, the 2B judges, Argmax hears and counts voices, the 9B talks, and code guarantees safety.** Each model does the job it's best at, and nothing about safety depends on any of them.

- **Hears: Argmax speech-to-text** on the Mac turns what's said into text. If the passphrase is in it, code blanks it out before anything is logged, shown or sent to a model.
- **Counts voices: Argmax diarization** says how many people spoke in a visitor's recording. One voice at the door is different from three. It never delays the reply.
- **Sees: Bonsai 9B** describes each photo in a sentence or two. We checked that Bonsai really looks at images: it correctly described a test picture of a green triangle, and made something up when there was no image. Big photos are slow, so we shrink them to 640 px first.
- **Judges: Bonsai 2B** labels each photo as a delivery, a visitor, suspicious or nobody, using the description, the mode, the doorbell and motion history and the voice count. It's small and fast, so the label is ready before Faraday speaks. Code checks the answer, and simple rules decide if the 2B can't. The label shapes what's said: a courier hears where to leave the package, a visitor is offered to leave a message, and nobody gets greeted when nobody's there. "Suspicious" while armed can raise the alert and bring the warning line forward. It can never lower anything.
- **Talks: Bonsai 9B** handles the conversation with visitors, passes on your instructions, writes one line to an intruder about what it sees, and sums up what happened. Quick owner commands at home ("turn on the porch light") go to the 2B, because they're easy and speed matters more there.
- **Speaks: Argmax text-to-speech** is Faraday's voice.
- **Guarantees safety: plain code.** The modes, alerts, lights, photos and the passphrase are all plain code. So is the check on every model answer. Details below.

On the Mac mini the models are `ternary-bonsai-2-9b-mlx` and `ternary-bonsai-2-2b-mlx` in LM Studio, both Bonsai 2. **[TBD: speed of each on the Mac mini]**

### The radio

Cheap 433 MHz sensors (a doorbell button and a motion sensor) send short fixed codes. A Flipper Zero plugged in by USB listens with its normal firmware, and our driver reads every code it reports.
- **Codes.** Each sensor has its own. We match on the sensor's serial and ignore the button bits, because our doorbell sometimes sends a slightly different code for the same press.
- **Motion.** One wave sends 17 copies of the code over about 2 seconds, and we merge them into one event.
- **Lights.** The Flipper replays a saved code to a cheap remote outlet. Outlet 1 is the porch light and outlet 2 the living room lamp.
- **Listening and sending.** The Flipper can't do both at once. So the driver pauses listening, sends, and starts again about 1.2 seconds later.
- **LED.** The Flipper's LED stays off. On an alarm it turns red and buzzes three times.
- **Reconnect.** If the USB cable drops, the driver reconnects by itself in about 8 seconds.

We tested the driver on its own with the real Flipper on a Windows laptop, and there are tests that replay real radio captures. Then we ran the whole app the same way: real sensors, Flipper, our software, Bonsai, real lamps, and the alarm. **[TBD: same check on the Mac mini]**

### The modes

Faraday is in Home, Away or Night. Each mode has a table of what always happens, written in plain code:

| | Home | Away | Night |
|---|---|---|---|
| Doorbell | says "Someone is at the door" | photo, then the AI greets the visitor and offers to take a message | porch light, photo, no sound |
| Motion (first time) | nothing | porch light, photo, "You're on camera" | porch light, photo |
| Motion again within a minute (alarm) | nothing | lamp too, alarm line, then one AI line about what it sees | lamp too, then one AI line about what it sees |

The lights and photos come from code, not the AI, so they happen even if the model is slow or wrong. When you come home and say the passphrase, the lights Faraday turned on go off again. Lights you switched on yourself stay on.

### What you can say

- **"I'm heading out." / "Goodnight."** arms the house in Away or Night.
- **"Who's at the door?"** At home or at night, Faraday says "Let me look.", takes a fresh photo and tells you what it sees, plus the label: "A courier holding a box at the front door. It looks like a delivery." If the camera or the vision model fails, it says "I can't see the door right now."
- **"If a delivery comes, ask them to leave it by the side door."** Instructions for visitors, set at home. Faraday confirms, remembers them after a restart, and passes them on: the next courier hears "Please leave it by the side door." Instructions only change what Faraday *says*. Anything about lights, modes, the alarm, locks or codes is refused. They're listed on the dashboard, where you can delete them, or say "forget the delivery instruction".
- **"When there's motion at night, turn on the living room lamp too."** becomes a rule.
- **"What happened while I was out?"** gets a short spoken summary.
- **"Turn on the porch light."**

### The camera

When something happens, Faraday takes a photo, shows it on the dashboard right away, and the 9B describes it. Then the 2B labels it. **[TBD: real camera photos on the Mac mini]**

### Safety: AI for understanding, code for safety

- The AI can arm the house but can never disarm it. It picks from a fixed menu of 8 actions (set the mode, turn an outlet on or off, speak, take a photo, save a message, summarize the log, add a rule), and there's no "disarm" on it. Disarming only happens when someone says or types the passphrase, and that check is plain code.
- The AI never sees the passphrase. It's blanked out of speech before anything gets logged or shown.
- Everything the AI picks goes through checks in code. Unknown actions, bad arguments and anything that would change the mode while armed get dropped.
- If the model errors, times out or says something useless, simple keyword rules take over, so Faraday never freezes.
- In Away mode whoever is talking is a visitor, so they can only leave a message. They can't add rules or instructions, switch lights or hear the summary.
- The threat label can only make Faraday more careful. Owner instructions are words to pass on, never actions.

### Accessibility

- **Eyes at the door, for a blind or low-vision owner.** "Who's at the door?" gets a spoken description of a fresh photo and the label. A doorbell at home is announced out loud. Everything else works by voice too.
- **Captions, for a deaf or hard-of-hearing owner.** Everything heard and said at the door is written out live: under "Heard & said" on the dashboard, and in big text in the **Live captions** card on the Controls page. Doorbells and alarms show as banners, and the label as a coloured pill.

### The dashboard and the Controls page

A big local web page for the projector shows:
- the mode, with its own colour for each mode and alert,
- live events and the latest photo with its label,
- what Faraday heard and said,
- the lights as real switches, the rules and instructions, and the messages.

A small green mic shows on the dashboard while Faraday is listening ("Hey Faraday" or after the doorbell).

The **Controls** button opens a phone-style page: the live camera, a **Snapshot** button that takes a photo and shows its description and label, a big **Hold to talk to visitor** button that plays your voice on the door speaker (the door mic is muted while you talk), the live captions, and all the light switches.

It loads nothing from the internet. No CDNs, no web fonts.

### Sustainability

One Mac mini, cheap sensors, and small models: a 2B for the quick jobs and a 9B only where it matters. No cloud servers, no subscription, no data center behind your doorbell. **[TBD: Mac mini power draw at idle and during a Bonsai call]**

## Run it

You need Python 3.9 or newer. The simulator and dashboard only use the standard library.

```
git clone https://github.com/Rayan1092/Faraday.git
cd Faraday
cp config/settings.example.json config/settings.json    # then set your own passphrase
```

**With the real hardware.** Plug in the Flipper (close any app on it first), the camera, the mic and the speaker.
- **On the Mac mini:** in LM Studio, load `ternary-bonsai-2-9b-mlx` and `ternary-bonsai-2-2b-mlx`, and raise Parallel above 1 so a background call doesn't wait in line. Start Argmax (`argmax-cli serve`). Then run `integration/start_mac.sh`. It checks that LM Studio and Argmax answer, warns you if the Wi-Fi is still on, and starts Faraday with the dashboard at http://127.0.0.1:8080 and the camera and intercom server next to it on port 8081.
- **On a Windows laptop:** `py -m integration`.

Then turn the Wi-Fi off. **[TBD: full run on the Mac mini]**

**Talking to visitors from the browser (Chrome trust step).** The Controls page uses the browser's microphone, and Chrome only allows that on a trusted address. On the Mac itself, `http://127.0.0.1:8080` already counts, so there's nothing to do. From another computer or a phone on the same network:
1. Open `chrome://flags/#unsafely-treat-insecure-origin-as-secure`.
2. Add the dashboard address and the controls address, separated by a comma, for example `http://<mac-ip>:8080,http://<mac-ip>:8081`. The Controls page shows the exact two if the mic is blocked.
3. Set it to **Enabled**.
4. Click **Relaunch**.

With the Wi-Fi off, other devices can't reach the Mac, so open the Controls page on the Mac itself.

**For developers: no hardware.** The simulator sends typed sensor events through the same pipeline and dashboard. It's a development and test tool, not part of the product. `python -m sim --offline --dashboard` uses simple rules instead of Bonsai. `python -m sim --dashboard` uses Bonsai through LM Studio's local server, with the server address and model names in `config/settings.json` under `llm` (`model` is the smart one, `fast_model` the small one). In the terminal, type a key and press Enter:
- `d` is the doorbell, and `m` is motion. `w` ends the 15-second wait for a doorbell after motion.
- `t <words>` is something someone said, for example `t Who's at the door?`.
- `1`, `2`, `3` switch to Home, Away and Night. Home asks for the passphrase.
- `q` quits.

On Windows use `py` instead of `python`.

**Tests:** `python -m unittest discover`

## Limits

We'd rather be honest about these:

- **Cheap sensors use fixed codes.** The doorbell and motion sensor send the same code every time, so anyone with a radio like a Flipper could record and replay it. That could fake a doorbell press or trigger the alarm. It can't disarm anything, because disarming needs the passphrase. A real product should use rolling-code sensors.
- **The outlets work the same way.** Someone nearby could replay the "light on" code.
- **Our cheap doorbell is flaky.** It has to be held for a second or two, because a quick tap never registers. Late in testing, even holds only got through about 1 time in 3, with or without our code. We're swapping in a new doorbell. **[TBD: new doorbell results]**
- **The radio goes deaf for a moment.** It can't hear sensors for about 1.2 seconds while it switches an outlet, and about 2.5 seconds during the alarm buzz.
- **Small models make mistakes.** Before tuning, the 4B picked the right action on its own only 61% of the time. It kept trying to switch to Home mode when you said "I'm heading out". We built a benchmark (`python -m brain.bench`) and tuned the tools and prompts with it. After tuning it was right on its own 97% of the time (136 of 140 runs), including reworded sentences we never tuned on. Code fixed most of the rest, for 99% final. Nothing unsafe got through in any run. On the threat label it was right in 15 of 15 runs, and it passed on the owner's instructions by itself in 6 of 6. Full numbers are in [docs/bench/](docs/bench/README.md). It's still a small model, so code stays the safety net.
- **It's not instant.** On a laptop running the 4B, a Bonsai call took 2.5 seconds on average and 5.7 seconds at worst in the benchmark. So lights and photos never wait for the AI, and Faraday says "One moment, please" to visitors while it thinks. **[TBD: timings on the Mac mini with the 9B and the 2B]**
- **The passphrase is spoken out loud.** Anyone who hears you say it could use it. We also accept a typed passphrase on the dashboard, and wrong guesses get locked out for 30 seconds after 5 tries.
- **Talking from another device needs the Chrome trust step**, and a network. On the Mac itself it just works.
- **One house, one door, one camera.** It's a small setup, not a full security system. It doesn't call anyone or send alerts off the box.

## Repo map

| Folder | What's in it |
|---|---|
| `brain/` | Bonsai client, prompts, checks, fallback rules, the threat label, "who's at the door?", the summary, the main pipeline |
| `modes/` | Home/Away/Night, alert levels, passphrase check |
| `rules/` | The mode table in code, rules you add by voice, and owner instructions for visitors |
| `log/` | Event log in `logs/events.jsonl`, with the passphrase blanked out |
| `dashboard/` | The offline web page and the Controls page |
| `sim/` | Keyboard simulator for testing without hardware |
| `hardware/`, `integration/` | Flipper, camera, speech, intercom, and wiring it all together |
| `shared/` | Message formats every part agrees on |
| `docs/` | Plan, formats, team notes, submission material |

Building on it? Read [CLAUDE.md](CLAUDE.md), then [docs/PLAN.md](docs/PLAN.md) and [docs/FORMATS.md](docs/FORMATS.md).
