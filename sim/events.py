"""Build fake events in the exact shared formats, with source "sim"."""
from __future__ import annotations

from typing import Optional

from shared import (DoorbellData, Event, EventType, MotionData, PhotoDescribedData,
                    Source, SpeechHeardData, make_event)

DOORBELL_KEY = "0x00DEC608"
MOTION_KEY = "0x00C5AFAE"
FAKE_PHOTO = "captures/photos/sim.jpg"
FAKE_DESCRIPTION = "A person in a dark jacket holding a cardboard box at the front door."


def doorbell() -> Event:
    return make_event(EventType.DOORBELL,
                      DoorbellData(key=DOORBELL_KEY, packets=9, duration_ms=1040), Source.SIM)


def motion() -> Event:
    """One merged motion event, like the Flipper client sends after its merge window."""
    return make_event(EventType.MOTION,
                      MotionData(key=MOTION_KEY, packets=17, duration_ms=1830), Source.SIM)


def speech(text: str) -> Event:
    return make_event(EventType.SPEECH_HEARD, SpeechHeardData(text=text), Source.SIM)


def photo_described(caused_by: Optional[str], description: str = FAKE_DESCRIPTION) -> Event:
    return make_event(EventType.PHOTO_DESCRIBED,
                      PhotoDescribedData(photo_path=FAKE_PHOTO, description=description),
                      Source.SIM, caused_by=caused_by)
