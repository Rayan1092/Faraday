# Dell: doorbell flow, one fixed greeting (2026-11-15 00:05)

Owner's list after the Mac test, item 4.

- **Away doorbell:** code speaks one fixed greeting at the ring, before the photo: "Hello. No one can come to the door right now. I can take a message or help with a delivery." (`rules/baseline.py` `DOORBELL_GREETING`). The owner's words for visitors ("tell them I'm in the back garden") follow right after it, from code. Then NV's listening window opens.
- **The photo:** it still gets its threat label (dashboard, summary, alert) but is never answered. `Brain.decide` returns nothing for any `photo_described`, and the fallback does the same. The only spoken photo answer left is "who's at the door?", which is plain code.
- **After that:** Bonsai only writes the reply after the visitor speaks. The delivery instruction is guaranteed there (item 3). If nobody answers, Faraday stays quiet.
- **"One moment, please."** is gone everywhere, along with the filler machinery in the pipeline.
- **Home doorbell:** unchanged ("Someone is at the door.", for the owner inside). **Night doorbell:** unchanged (quiet: porch light and photo).
- **Bench:** the `doorbell_greeting`, `i_delivery` and `i_visitor` cases are removed, because they measured the photo greeting Bonsai no longer writes. `i_speech_delivery` covers the delivery instruction.
- **Tests:** about 25 tests updated from "photo gets a greeting" to "greeting at the ring, photo never answered". New: no filler anywhere; a doorbell photo gets no second greeting even with Bonsai up; the owner's confirmation doesn't count as the visitor line.
- **For NV:** PLAN Q32. `speech_busy()` waits for any photo in progress, so the window opens only after vision finishes. Only `source: dashboard` photos need to count now.
