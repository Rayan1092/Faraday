# Discord submission post

Copy everything between the lines into the submission channel. Fill in the [TBD] parts first.

---

**Team:** [TBD: team name]
**Members:** [TBD: names / Discord handles]

**Faraday:** a home guard that answers the door, controls the lights and remembers your rules, all on one Mac mini with the Wi-Fi off, and the AI can never unlock the house.

**What it does**
- Doorbell rings, Faraday takes a photo, greets the visitor and saves their message
- Motion at night turns on the porch light. Motion again within a minute turns on the lamp too, and Faraday talks to whoever is out there
- Say "when there's motion at night, turn on the living room lamp too" and it becomes a rule
- Ask "what happened while I was out?" and get a short spoken summary
- A big offline dashboard shows everything live

**How it's built**
- Flipper Zero listens to cheap 433 MHz sensors and switches remote outlets
- Bonsai 2 in LM Studio picks actions from a fixed menu of 8. There's no disarm on the menu
- Argmax does speech-to-text and text-to-speech on the Mac
- Plain code handles the lights, photos, alarms and the passphrase. AI for understanding, code for safety

**Prizes we'd like to be considered for**
- Best Overall
- Judges' Choice
- Sustainability (as a mention): no cloud servers, no subscription, one small computer and cheap sensors

**GitHub:** https://github.com/Rayan1092/Faraday
**Video:** [TBD: video link]

---

Before posting, check:
- [ ] Team name and members filled in
- [ ] Video link works when you're logged out
- [ ] The repo is public and the README says what's proven on real hardware
- [ ] Posted before 11:30 Sunday (deadline is noon)
