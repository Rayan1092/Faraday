"""Stand-in for integration's `board.control_hook` (PLAN Q26), so the dashboard light
switches work in the sim without a Flipper.

Same contract as `integration.app.App.control`: log an outlet_on/outlet_off action with
source "dashboard" (the Lights panel reads the log) and return False when the outlet is
unknown or already in that state. On the real Mac, integration sets its own hook instead.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from shared import ActionType, Outlet, Source, make_action


def outlet_state(entries, outlet: str) -> Optional[str]:
    """The last outlet_on/outlet_off in the log for this outlet, or None."""
    state = None
    for e in entries:
        if (e.get("kind") == "action" and e.get("type") in (ActionType.OUTLET_ON, ActionType.OUTLET_OFF)
                and (e.get("args") or {}).get("outlet") == outlet):
            state = e["type"]
    return state


class SimControls:
    def __init__(self, pipeline: Any, on_action: Optional[Callable[[Any], None]] = None):
        self.pipeline = pipeline
        self.on_action = on_action

    def __call__(self, outlet: str, on: bool) -> bool:
        if outlet not in Outlet.ALL:
            return False
        kind = ActionType.OUTLET_ON if on else ActionType.OUTLET_OFF
        current = outlet_state(self.pipeline.log.recent(500), outlet) or ActionType.OUTLET_OFF
        if current == kind:
            return False
        action = make_action(kind, {"outlet": outlet}, Source.DASHBOARD)
        self.pipeline.log.write(action)
        if self.on_action is not None:
            self.on_action(action)
        return True
