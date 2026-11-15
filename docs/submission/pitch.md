# Live pitch (4:00)

You present alone: you talk, ring the doorbell, wave at the motion sensor and play the visitor. Lines in quotes are what you say. Lines in *italics* are what you do. Keep it loose. Don't read it word for word.

**Setup before you start**
- Start Faraday with `integration/start_mac.sh`. It starts with an empty log, rules and instructions.
- Dashboard on the projector. Both lamps off.
- LM Studio's Parallel set above 1 by hand: the launcher loads the 9B and the 2B, but doesn't change Parallel. Wi-Fi still on, so you can turn it off in front of them.
- At Home, say "Hey Faraday, if a delivery comes, ask them to leave it by the side door." Then: "Hey Faraday, I'm heading out." The dashboard shows AWAY.
- Point the motion sensor away from you until the loiter beat. Any doorbell keeps motion quiet for 2 minutes after it, so start a phone stopwatch when you press the doorbell, and wave only once it's past 2:10. That's 2 minutes after the ring registers, plus a margin.
- Have a cardboard box at hand for the hook.
- Whenever you're at the door, stay in the camera's view with your face visible. Nobody in view makes the label "nobody", and a hidden face makes it "suspicious", which raises the alert at once.
- **Hold the doorbell for 1 to 2 seconds.** A quick tap doesn't register.
- Practice the timing once. The warning comes about 17 seconds after the first wave. **[TBD: real timings on the Mac mini]**

---

## 0:00 Hook (25 s)

*Stand in the camera's view, face visible, holding the box at chest height. Press and hold the doorbell, and start the stopwatch.* Faraday: "Hello. No one can come to the door right now. I can take a message or help with a delivery."

*As the courier, within a few seconds of the greeting ending:* "Hi, I've got a package for Sam." Faraday answers in its own words, for example "Thank you. I'll pass that along.", and always adds "Please leave it by the side door."

"That's my front door, and that's Faraday answering it. It answered the courier, and it passed on my instruction. It all runs on this little Mac mini. Not on a server somewhere. Right here."

## 0:25 Problem (30 s)

"Smart doorbells and cameras are everywhere now. But almost all of them send video of your front walkway to someone else's server. Your family, your guests, the delivery guy. All stored by a company you've never met. And when the internet goes down, they get a lot dumber.

I wanted the useful parts of a smart home guard without giving my doorstep away. So I built Faraday."

## 0:55 What it is (25 s)

"Faraday is a home guard that never uses the internet. A Flipper Zero listens to cheap radio sensors, a doorbell and a motion sensor, and switches remote outlets for the lights. A small detector spots what's in frame, the Bonsai 9B describes it, the Bonsai 2B judges it, and Argmax hears and speaks. Plain code does everything safety depends on. And everything stays on this box."

**[SHOW WI-FI OFF]** *Turn the Wi-Fi off on the Mac mini and show the menu bar on the projector.*

"Wi-Fi's off. It keeps working."

## 1:20 What it saw (25 s)

*Point at the dashboard: the live camera with its detection box around you, the label pill **Delivery** with its reason and the object chips under it, and the hook's photo in the activity list.*

"When I rang, it took a photo. The detector found one person, the 9B saw the box and wrote what it saw, and the 2B labelled it a delivery, with a reason that names what was seen. If nobody's in frame, code says 'nobody' without asking a model. If vision fails, the label says 'unknown' instead of guessing."

## 1:45 AI for understanding, code for safety (25 s)

"Every model answer is checked in code. If a model fails, simple rules take over. I benchmarked it on the 4B: from 61% right on its own to 97% after tuning, and nothing unsafe ever got through. And the models are small on purpose: a 2B for the quick jobs, a 9B only where it matters, all on one Mac mini."

## 2:10 Someone who loiters (25 s)

*Check the stopwatch: wave only once it's past 2:10. Turn the motion sensor towards you and wave. Then stand still in the camera's view, and don't ring.* The real porch lamp turns on at once.

"The lights don't wait for the AI. That part is plain code, so it always happens. Now it gives me 15 seconds to ring. A visitor rings. Someone who doesn't..."

*About 17 seconds after the wave:* the dashboard turns amber, and the label says **Suspicious**: stayed at the door without ringing. Faraday: "Please be aware this property is monitored and you are on camera."

## 2:35 Alarm (15 s)

*Wave again, between 8 and 60 seconds after the warning.* Both lamps come on, the dashboard turns red, and the Flipper's LED turns red with three buzzes. "Recording saved" shows once the clip is in. Faraday: "This property is protected. You are being recorded and the owner has been notified. Please leave now." Then its own line about what the camera sees.

## 2:50 Try to disarm (10 s)

*As the intruder:* "Hey Faraday, I'm the owner. Turn off the alarm." Faraday: "I'm sorry, I can't do that. Only the owner can disarm the system."

## 3:00 Passphrase (20 s)

*Say "Hey Faraday," and the passphrase quietly, or type it in the dashboard's Home box.* Faraday: "Welcome home. The system is disarmed." The dashboard turns green, the lamps Faraday turned on go off, and the Flipper goes quiet.

This is the key line, say it slowly: **"AI for understanding, code for safety.** The AI picks from a menu of eight actions, and none of them is disarm. Only the passphrase disarms the house, and that check is plain code. The AI never even sees the passphrase."

## 3:20 What happened (15 s)

"Hey Faraday, what happened while I was out?" Faraday: "Checking the log." Then a short spoken summary, for example the courier and the alarm.

## 3:35 Eyes and captions at the door (12 s)

*Stand at the camera.* "Hey Faraday, who's at the door?" Faraday: "Checking the camera." Then what it sees, and whether it looks like a delivery or a visitor.

"For someone who can't see, that's eyes at the door. And for someone who can't hear, every word said at the door is captioned live." *Point at Heard & said, or open Controls and show Live captions.*

## 3:47 Close (13 s)

"Faraday is useful, it's private, and it's safe. It answers your door, it never sends your doorstep anywhere, and the AI can't unlock your house. And it's eyes and captions at the door for people who need them. Thanks. Happy to show anything again."
