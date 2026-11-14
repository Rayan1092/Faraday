# integration/

**Owner: NV laptop.**

Wires everything into one process: the event bus, the main loop and the hardware action executor.

- Feeds every event (real or sim) to `brain.handle_event()` and to `log/`.
- Runs the hardware actions (`outlet_on`, `outlet_off`, `speak`, `take_photo`) on `hardware/`.
- Sets the Flipper LED from `mode_changed` events.

See "Seams" in [docs/PLAN.md](../docs/PLAN.md).
