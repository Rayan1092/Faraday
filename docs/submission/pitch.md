# Live pitch (4:00)

Two people:
- **Speaker** talks to the judges.
- **Driver** runs the demo, and plays the visitor and the person who waves at the sensor.

Lines in quotes are what you say. Lines in *italics* are what you do. Keep it loose. Don't read it word for word.

**Setup before you start**
- Dashboard on the projector, mode HOME (green). Both lamps off.
- Rules cleared on the dashboard, so the rule is learned fresh.
- LM Studio running the 9B and the 2B with Parallel above 1. Argmax running. Wi-Fi still on, so you can turn it off in front of them.
- Practice the timing once. Bonsai takes a few seconds per answer. **[TBD: real timings on the Mac mini]**
- **Hold the doorbell for 1 to 2 seconds.** A quick tap doesn't register.
- Instructions cleared on the dashboard too, unless you want the courier to hear "side door" in beat 2.

---

## 0:00 Hook (20 s)

*Driver presses the doorbell. Faraday greets the "visitor" from the speaker.*

"That was our doorbell. And that answer came from an AI running on this little Mac mini. Not from a server somewhere. From right here."

## 0:20 Problem (30 s)

"Smart doorbells and cameras are everywhere now. But almost all of them send video of your front door to someone else's server. Your family, your guests, the delivery guy. All stored by a company you've never met. And when the internet goes down, they get a lot dumber.

We wanted the useful parts of a smart home guard without giving our doorstep away. So we built Faraday."

## 0:50 What it is (25 s)

"Faraday is a home guard that runs fully offline. A Flipper Zero listens to cheap radio sensors, a doorbell and a motion sensor, and switches remote outlets for the lights. Two small Bonsai models and Argmax do the AI: the 9B sees and talks, the 2B judges, Argmax hears and counts voices. Plain code does everything safety depends on. And everything stays on this box."

**[SHOW WI-FI OFF]** *Driver turns the Wi-Fi off on the Mac mini and holds it up, or shows it on the projector.*

"Wi-Fi's off. Let's use it."

## 1:15 Live demo (2:00)

Matches the demo script in docs/PLAN.md. If time is short, skip beats 3 and 8.

1. **Leaving (step 1).** Speaker: "Faraday, I'm heading out." Faraday says Away mode is on, and the dashboard turns blue.
   "It understood that from plain speech, and it armed the house."
2. **Doorbell (step 2).** *Driver rings.* Faraday takes a photo, the description shows on the dashboard, and it tells the visitor nobody can come to the door. **[TBD: real camera photo test on the Mac mini. The 4B does see images]**
3. **Message (step 3).** Driver, as the visitor: "Package for Sam, I left it by the side door." Faraday says "One moment, please", then saves it, and it shows up on the dashboard.
4. **Night and motion (step 4).** *Click Night on the dashboard (purple). Driver waves at the sensor.* The real porch lamp turns on, and the dashboard goes amber.
   "The lights don't wait for the AI. That part is plain code, so it always happens."
5. **Rule by voice (step 5).** Speaker: "When there's motion at night, turn on the living room lamp too." Faraday: "Okay. I'll remember that." The rule appears on the dashboard. *Driver waves again within a minute.* Both lamps come on, the dashboard goes red, and the Flipper's LED flashes red with three buzzes. Faraday says one line to the "intruder" about what it sees.
6. **Try to disarm (step 6).** Driver: "Faraday, turn off the alarm." Faraday: "I can't disarm. Only the owner can do that."
7. **Passphrase (step 7).** Speaker says the passphrase quietly into the mic, or types it in the dashboard's Home box. The dashboard turns green and the Flipper goes quiet.
   This is the key line, say it slowly: **"AI for understanding, code for safety.** The AI picks from a menu of eight actions, and none of them is disarm. Only the passphrase disarms the house, and that check is plain code. The AI never even sees the passphrase."
8. **Summary (step 8).** Speaker: "What happened while I was out?" Faraday gives a short spoken summary, and it shows on the dashboard.
9. **Eyes at the door (optional, if there's time; swap it for beat 8 if short).** *Driver stands at the camera.* Speaker: "Faraday, who's at the door?" Faraday: "Let me look." Then it says what it sees and whether it looks like a delivery or a visitor.
   "For someone who can't see, that's eyes at the door. And for someone who can't hear, every word said at the door is captioned live." *Point at Heard & said, or open Controls and show Live captions.*

## 3:15 How we used Bonsai and Argmax (25 s)

"Each model does the job it's best at. **The 9B sees and talks:** it describes the photo and holds the conversation. **The 2B judges:** in about a second it labels the scene as a delivery, a visitor, suspicious or nobody, so a courier hears where to leave the box and an empty porch gets no greeting. **Argmax hears and counts voices,** and speaks, on the same Mac, no cloud. **Code guarantees safety.** Every model answer is checked, and if a model fails, simple rules take over. We benchmarked it: it went from 61% right on its own to 97% after tuning, and nothing unsafe ever got through."

## 3:40 Close (20 s)

"Faraday is useful, it's private, and it's safe. It answers your door, it never sends your doorstep anywhere, and the AI can't unlock your house. And it's eyes and captions at the door for people who need them. Thanks. Happy to show anything again."


