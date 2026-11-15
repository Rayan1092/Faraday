# NV: the night of 2026-11-15, in one place (06:15)

**For the owner:** the ordered retest list is at the top of `docs/team/history/MAC_TEST.md`. It covers NV and Dell items, with the three settings lines first.

## Merged (each with a fresh reviewer, full suite green, rehearsal re-run after every merge, Dell's included)

| PR | What | Handoff |
|---|---|---|
| #64 | Vision without thinking. Prefill (an empty think block as an assistant turn) is the one method that stops Bonsai 2's thinking in LM Studio. 120 tokens, strategy probe and cache, photos never wait. `python3 -m hardware.check vision` times the 9B and the 4B and checks Dell's brain. | 2026-11-15-0430-nv-vision-nothink.md |
| #66 | Detector: objects are named only at `camera.detector.object_score` (default 0.6) and when steady in 3 of 5 frames, else "unknown object". People stay sensitive. A photo reports only what is in the photo. Weapons are never hidden. `/api/status` gives the frame size, and the reference page is uncropped. | 2026-11-15-0500-nv-detector-framing.md |
| #70 | TTS says "pass phrase". `python3 -m integration.fixed_lines --render --fresh` re-renders the voice cache. Dell's Q39 wording, Q40 rehearsal headroom, Q41 answered. QUESTIONS 19-25. | 2026-11-15-0530-nv-tts-say-as.md |
| #73 | `shared/events.py`: the `UNKNOWN` comment matches the rule (vision and the detector both failed), as Dell asked. Comment only. | this note |

Dell's patches for my tests are applied: Q38 (final TTS lines) and Q41 (the rehearsal's facts-only answer). The rehearsal's alarm-doorbell step had an old flake: the doorbell was pressed while the buzz paused the radio. It now waits for the radio.

## Not done, and why

- Mac-only checks (no hardware tonight). They are steps 3-15 of the retest list.
- QUESTIONS 19-25 have defaults in place. Nothing is blocked on them.
