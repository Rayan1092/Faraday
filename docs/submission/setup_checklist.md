# Setup checklist (demo day)

Print this or keep it open on a phone. Do it top to bottom. Anything marked **[TBD]** isn't proven yet. Fill it in after the first full run on the Mac mini.

## 1. Pack

- [ ] Mac mini, its power cable, keyboard and mouse
- [ ] HDMI cable (and an adapter for the projector, just in case)
- [ ] Flipper Zero, charged, with its USB-C cable (a data cable, not charge-only)
- [ ] Doorbell button (new one, with a fresh battery) and the motion sensor (with batteries)
- [ ] 2 remote outlets (outlet 1 = porch light, outlet 2 = living room lamp) and 2 small lamps
- [ ] USB camera, USB mic, speaker (with its cable)
- [ ] Power strip, plus an extension cord
- [ ] Spare batteries for the doorbell and motion sensor
- [ ] A laptop with the repo, as the backup for running the sim
- [ ] Tape, to hold the doorbell and the sensor still on the table

## 2. Plug in (in this order)

1. Power strip in. Mac mini on, logged in.
2. Projector or monitor on HDMI. Set the display to mirror, at 1920×1080 or 1280×720 (both tested).
3. Lamps into the remote outlets, and the outlets into the power strip. Both lamps switched ON at the lamp itself, so the outlet controls them.
4. **Close qFlipper** and make sure **no app is open on the Flipper** (it should be on the main screen). Then plug the Flipper into the Mac. The CLI fails if an app is open.
5. Camera, mic and speaker in. Set the speaker as the Mac's sound output and turn the volume up.
6. Doorbell and motion sensor on the table, close to the Flipper. Point the motion sensor where someone can wave at it.

## 3. Start things (in this order)

1. **LM Studio.** Load both Bonsai models: `ternary-bonsai-2-9b-mlx` (smart: sees and talks) and `ternary-bonsai-2-2b-mlx` (fast: the threat label and quick commands). Set **Parallel to 2 or more**. Start the local server. Check it: `curl http://localhost:1234/v1/models` should list both.
2. **Argmax speech-to-text.** In its own terminal: `.build/release/argmax-cli serve` (port 50060). Say one test sentence, because the first one is slow (warm-up).
3. **Faraday.** Run `integration/start_mac.sh`. It refuses to start if LM Studio or Argmax don't answer, and it warns about the Wi-Fi, a missing Flipper or the example passphrase. Then it starts the app, the dashboard at http://127.0.0.1:8080 and the camera and intercom server on port 8081. Say "Hey Faraday" or press **Listen** on the dashboard to talk to it (or `r` in the app console).
4. **Dashboard.** Open http://127.0.0.1:8080 in the browser and go full screen (Cmd+Ctrl+F). It should say HOME in green.
5. **Settings check.** In `config/settings.json`, the passphrase is set (not the example one) and `llm.profile` is `mac`.
6. **Controls page.** Click **Controls** in the dashboard's bottom row, on the Mac itself. The live camera should show. Chrome asks for the microphone the first time you hold **Hold to talk to visitor**: click Allow.
   - **From another computer or a phone** (only with a network, so not once the Wi-Fi is off): open `chrome://flags/#unsafely-treat-insecure-origin-as-secure`, add the dashboard address NV gives plus the same address on port 8081 (for example `http://<mac-ip>:8080,http://<mac-ip>:8081`; the page lists both if the mic is blocked), set it to **Enabled** and click **Relaunch**.

## 4. Quick test (5 minutes, before judges arrive)

- [ ] **Hold** the doorbell for 1 to 2 seconds. Taps don't register. The dashboard shows "Doorbell pressed".
- [ ] Wave at the motion sensor. You should see one motion event (about 17 packets).
- [ ] Click Away on the dashboard and wave. The porch light turns on and the dashboard turns amber. The Flipper's LED stays off, because it only lights up on an alarm.
- [ ] Say "Faraday, disarm". It refuses.
- [ ] Say "Hey Faraday, who's at the door?" It says "Let me look." and then what the camera sees.
- [ ] Open **Controls**: hold Talk and say "hello". It plays on the door speaker. Press **Snapshot**: a photo and its description appear.
- [ ] Type the passphrase in the dashboard's Home box. It goes green.
- [ ] Clear the rules and any instructions on the dashboard (✕), so the demo starts fresh.
- [ ] Back to HOME, lamps off, so the demo starts clean.

## 5. Wi-Fi off

- [ ] Turn Wi-Fi off on the Mac mini (menu bar → Wi-Fi → off). Unplug Ethernet if there is any.
- [ ] Check the dashboard badge says **● OFFLINE** in green. It turns red ("ONLINE: turn Wi-Fi off") while any network is up. It checks this without sending anything.
- [ ] Say one sentence to Faraday, to make sure everything still works offline.

## 6. If something fails on stage

Keep talking. Say what happened in one sentence, switch, and move on.

| What fails | What you see | What to do |
|---|---|---|
| Doorbell doesn't register | No "Doorbell pressed" banner | Hold it longer and closer to the Flipper. Still nothing: press **Ring doorbell** on the dashboard (bottom row) and say "the sensor's flaky, so here's the same event from the dashboard". The log marks it as coming from the dashboard |
| Motion sensor | No motion event | Wave closer. Or press **Motion** on the dashboard, same line as above |
| Flipper unplugged or frozen | No events arrive | Replug it. The driver reconnects by itself in about 8 s. Close any app on the Flipper first. Still dead: switch to the backup demo (last row) |
| Outlet doesn't switch | Dashboard says ON, lamp stays off | Check the lamp's own switch and plug. The dashboard shows what Faraday sent, so point at that |
| Second wave doesn't raise the alarm | Stays amber after waving twice | The sensor may have gone quiet after its first trigger. Wave again within the minute. Otherwise keep going with the warning |
| Talk says the microphone is blocked | Red box on the Controls page | Follow the steps in the box. On the Mac itself, click the icon left of the address bar and allow the microphone |
| Mic or speech-to-text | Nothing heard | Type the sentence in the dashboard's text box. It sends the exact same event |
| Speaker or text-to-speech | No sound | Turn up the volume or check the output device. Everything Faraday says still shows under "Heard & said" on the dashboard |
| Camera | No photo | The flow keeps going with "No description available". Say so and move on |
| Bonsai slow or LM Studio crashed | Long pauses, "One moment, please" | Keep going. The lights, photos and alarm are plain code, and the fallback rules answer. Say "that's the safety net". Restart LM Studio between demos |
| Dashboard frozen | Badge says "app not responding" | Reload the page. If Faraday crashed, restart it with `integration/start_mac.sh` |
| Everything | Hardware won't cooperate | **Backup demo:** stop Faraday, run `python -m sim --demo --dashboard` (add `--manual` to step it yourself with Enter). It plays the whole PLAN.md script through the real software on the same dashboard. Say out loud that the sensors are simulated |

## 7. After each demo

- [ ] Clear the rules and instructions (✕ on the dashboard)
- [ ] Back to Home with the passphrase, lamps off
- [ ] Check the badge still says OFFLINE
