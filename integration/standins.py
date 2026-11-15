"""Stand-ins for the automated tests only (the product start command has none)."""
from __future__ import annotations

from typing import Optional

from shared import Action, Event, EventType, PhotoDescribedData, Source, make_event

STANDIN_PHOTO = "captures/photos/sim.jpg"
STANDIN_DESCRIPTION = "A person in a dark jacket holding a cardboard box at the front door."


class StandInCamera:
    """Answers take_photo with a fixed description (source "sim")."""

    def __init__(self, description: str = STANDIN_DESCRIPTION):
        self.description = description

    def take(self, action: Action) -> Optional[Event]:
        return make_event(EventType.PHOTO_DESCRIBED,
                          PhotoDescribedData(photo_path=STANDIN_PHOTO, description=self.description),
                          Source.SIM, caused_by=action.id)
