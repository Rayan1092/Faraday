# shared/

**Owner: both laptops. Change only by agreement**, in the same PR as [docs/FORMATS.md](../docs/FORMATS.md).

These are Python definitions of every event and action: dataclasses, `make_event` / `make_action` (which build and validate) and `validate_event` / `validate_action`. Stdlib only, Python 3.9+.

```python
from shared import make_event, make_action, DoorbellData, EventType, ActionType, Source
ev = make_event(EventType.DOORBELL, DoorbellData(key="0x00DEC608", packets=9, duration_ms=1040), Source.FLIPPER)
act = make_action(ActionType.TAKE_PHOTO, {"reason": "doorbell"}, Source.BRAIN, caused_by=ev.id)
print(ev.to_json())
```
