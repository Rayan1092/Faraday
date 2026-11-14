# sim/

**Owner: Dell laptop.**

Fake hardware for development. It sends any FORMATS.md event with `source: "sim"`, and other code must treat sim events exactly like real ones. It's useful on Windows laptops without the Flipper and as the stage backup. Sim runs never sign off a step; only real-hardware tests do.
