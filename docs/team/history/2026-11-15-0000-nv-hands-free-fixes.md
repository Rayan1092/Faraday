# NV: hands-free fixes from the review of #41 (2026-11-15 00:00)

The owner asked for a fresh reviewer agent on every PR before merging. #41 (hands-free 2) was merged before that rule, so it was reviewed afterwards. These are the fixes for every finding. Each bug has a test that recreates the reviewer's scenario; the HIGH one and the stale-audio one were checked to fail on the old code.

| # | Finding | Fix |
|---|---|---|
| 1 HIGH | A wake segment converted into a doorbell window after a long quiet wait closed at once ("nobody spoke"). The backstop timer counted from when the wake segment opened. | `Recorder._finish_window` restarts the backstop while waiting for speech and on every restart (`epoch`) |
| 2 | A visitor's reply could come out as `channel: owner` (ring, then the owner asks something, then the greeting ends in "?") | The greeting after a doorbell is always for the visitor. A question opens an *owner* window only if the speak action's `caused_by` is one of the owner's speech_heard events (Dell's brain sets `caused_by=event.id`, `brain/guard.py:160`). The 30 s owner timer is gone. `App` passes `caused_by` to `Voice.on_said` |
| 3 | "Hey Faraday … turn on the light" lost the request when STT was slower than the pause plus the request | Wake segments are checked one at a time, in order. After "Hey Faraday" alone, a segment that already ended is the request; otherwise the segment recording now becomes the request window |
| 4 | Audio from just before Faraday spoke started the next window as "already spoke", and that stale clip was sent as the reply | Muting clears the preroll and the open window's unfinished audio, detector and timers. A restart conversion starts with a fresh detector, fed only the post-mute preroll |
| 5 | The visitor's reply after the intercom was never listened for (`/api/listen` is gone) | `Intercom.on_end` → `Voice.on_intercom_end()` opens a visitor window |
| 6 | Conversion race: a wake segment that just finished could be taken as the visitor's reply | `adjust()` and the purpose change happen together under the voice lock (`_segment_done` takes the same lock); the chime plays only when a window really opens |
| 7 | When the mic can't open: a retry flood, requests stuck forever, and a dead stream kept | Back-off (up to 5 s), one console line, a requested window dropped after 20 s, and the stream kept only after `start()` succeeds |
| 8 | A short gap in `speech_busy` between `get()` and the busy flag | `speech_busy` uses each queue's `unfinished_tasks` with `task_done()` after the work |
| also | `on_said` dropped the doorbell window if an owner window was open | The doorbell stays pending and retries every 1 s, for up to 30 s |

Tests: `integration/tests/test_voice.py` and `hardware/tests/test_media.py` (`on_end`). Full suite 450 OK (3 opt-in skipped).

## Second review (fresh reviewer agent on this PR) and fixes

| # | Finding | Fix |
|---|---|---|
| 1 | After "Hey Faraday" alone was answered by a queued owner window, a leftover 15 s flag made the next unrelated speech an owner request | Wake segments are numbered. "The next words" means exactly segment N+1, armed only when that segment already ended or is ending. It is never armed when an owner window was opened instead |
| 2 | That next segment wasn't checked: "Hey Faraday. … Hey Faraday. … turn on the light" sent "Hey Faraday." and deleted the request | It is transcribed and wake-stripped. The phrase again means "still waiting", otherwise the words (minus any phrase) are the request |
| 3 | An open, silent owner window recorded the visitor's answer to the greeting as owner | A greeting or visitor question takes over an owner window that has heard nothing since Faraday spoke, and a pending owner request yields. No promotion while a visitor request waits. If the owner is talking, the doorbell waits and retries |
| 4 | My mute reset threw away a sentence in progress (a visitor talking when the owner presses the intercom) | On mute, a window with speech in progress ends with what was said; only a window with no speech is reset. A delayed conversion keeps a sentence in progress whole |
| 5 | The fallback chime played before any window existed | The chime plays when the owner window really opens |
| 6 | A test passed through the doorbell path, not the `caused_by` one | Added a test with no doorbell, and the visitor channel is asserted |
| 7 | Doorbell retries went on after stop, and queued wake audio stayed on disk | Both fixed |
| 8 | An exception in `on_said` would end the speech worker and leave `speech_busy` stuck | Caught and logged |

PLAN: the `channel` row now describes the `caused_by` rule, and Dell's Q31 is answered (fixed differently: no timer at all).
