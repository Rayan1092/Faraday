# Dell: the owner's delivery instruction always reaches the courier (2026-11-14 23:50)

Owner's list after the Mac test, item 3. On the Mac, the instruction "if a delivery comes, ask them to leave it by the side door" was stored, but a visitor with a package only got "Thank you. I saved your message."

**Code guarantee** (`Brain._pass_on_delivery`, in both the Bonsai and the fallback path):
- **When it applies:** visitor speech (armed, or `channel: "visitor"`) that mentions a delivery, or comes after a `delivery` label, while a delivery instruction is stored.
- **What it does:** the reply always includes the owner's words ("Please leave it by the side door."). A different drop-off spot from Bonsai is removed.
- **Delivery words:** `fb.mentions_delivery` covers package, parcel, delivery, courier, box, Amazon, FedEx, UPS, DHL, USPS, post, mail, signature.
- **Instruction lookup:** `instructions_for` now gives the delivery instruction to a visitor who mentions a package, even when the photo label said `visitor`.

**Bench** (`i_speech_delivery`, 4B, 5 runs): Bonsai alone passed it on 1 time in 5 (the Mac bug), and with code 5 out of 5. File: `docs/bench/2026-11-14-2329-...`.

**Tests:** `brain/tests/test_instruction_used.py` (7): package mentioned, untagged speech while armed, Bonsai failing, after a delivery label without the word, a wrong spot replaced, no duplicate, nothing for a plain visitor or with no instruction stored.
