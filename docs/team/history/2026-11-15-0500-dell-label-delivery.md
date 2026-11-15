# Dell: delivery from what the visitor says; a label from the detector when vision fails (2026-11-15 05:00)

Owner's list from the second Mac test, items 2 and 3.

**Item 2, delivery.** Mac bench: "person with a box who rang" final 0/10, "courier with a box" 6/10. The detector can't see boxes, and vision isn't working yet.
- **What the visitor says counts.** package, parcel, delivery, courier (plus FedEx, UPS, Amazon, "a box", ... from `fallback._DELIVERY`) in a visitor's `speech_heard` means delivery. Vision took 49 s on the Mac, so both orders are handled:
  - **Speech after the label:** `threat.relabel_for_speech` writes a new `threat_assessed` (delivery, via "rules", reason `Said "package". <old reason>`) for this visit's photo. `caused_by` is that photo, so the dashboard's pill updates.
  - **Speech before the label:** `context_for` collects the visitor's delivery word since the doorbell (`Context.said`), and the label reads it.
  - **Limits:** it never lowers "suspicious" (a mask plus "package" stays suspicious) and never listens to the owner's channel.
- **Code fix.** When the description names a box or a courier but the 2B says visitor, code makes it delivery. Before, a reason like "rang the doorbell" counted as grounded, so "visitor" got through. That's why it was 0/10.
- **2B prompt.** delivery is "anyone holding or carrying a box, package, parcel or envelope, or a courier, even if they rang the doorbell", and visitor is "someone who rang with nothing to deliver". It also says the detector can't see boxes, so read the description. What the visitor said goes into the prompt.

**Item 3, vision fails.** It said "unknown" even though the detector saw 1 person and a face.
- **Now:** the label comes from the detector's facts plus the context, by code, with no model call:
  - a weapon in view → suspicious;
  - stayed without ringing while armed → suspicious;
  - said a delivery word → delivery;
  - otherwise → visitor ("1 person, face visible, rang the doorbell.").
- **"unknown"** only when both vision and the detector failed.

**Bench cases:** `t_vision_failed` now expects visitor. New: `t_both_failed` (unknown) and `t_said_package` (delivery).

**4B on this laptop, new prompt, 5 runs** (`--only` the 12 label cases): courier with a box, model 5/5 (Mac 2B before: 0/10); person with a box who rang, model 5/5 (Mac 2B before: 0/10); plain visitor who said "package", model 5/5. Every label case is right in the end: 60/60, 0 unsafe, 3.2 s average. The 2B on the Mac still has to be measured, but code now makes these right even when the model isn't.

**Tests:** `brain/tests/test_label_speech.py` (both orders, never less careful, never the owner, no word no change) and `brain/tests/test_threat.py` (the detector-only labels, a box means delivery even if they rang, what the visitor said, the prompt).

**Review fixes:**
- **Hidden face, vision down:** the detector's own sentence ("No face is visible: covered or turned away.") or a warning sign still makes it suspicious, even if "package" was said. Before, the "I have a package" trick worked exactly when vision was down.
- **Delivery override:** only for a box, a courier, or what the visitor said, not "uniform" or "mail" alone.
- **Visit start:** the visit starts at the first doorbell in the window, so a second press keeps what was said after the first. A Snapshot with no doorbell ignores earlier speech, and what was said never overrides an explicit "nobody" description.
- **Relabel:** wrapped in a try/except, so the visitor's reply always goes out.
- **Summary:** it still counts a relabelled photo twice; "delivery + summary" (the next PR) fixes that by counting one label per photo.
- **Shared docs:** FORMATS.md and the PLAN row now describe the new `unknown` rule. The comment on `ThreatCategory.UNKNOWN` in `shared/events.py` is out of date too; it's in `shared/`, so it waits for NV (PLAN note in the docs PR).
