"""Decode the active mowing or parking override reported by the Planner."""

from dataclasses import dataclass
from struct import Struct

from .transport import AutomowerClient
from .wire import Command, Event

_GET_OVERRIDE = Command(4658, 2)
_OVERRIDE = Struct("<BII")
_OVERRIDE_WITH_MISSION = Struct("<BIII")
OVERRIDE_OPTIONS = ("inactive", "forced_park", "forced_mow")
PLANNER_EVENTS = Command(4658, 8)
_COMMON_RESTRICTIONS = (
    "none",
    "week_schedule",
    "park_override",
    "sensor",
    "daily_limit",
    "firmware_update",
    "frost_guard",
    "all_missions_completed",
    "external",
)
_REASON_9_OPTIONS = ("unknown", "mission_abandoned", "no_daylight")
RESTRICTION_OPTIONS = (*_COMMON_RESTRICTIONS, *_REASON_9_OPTIONS)


def restriction_state(code: int, reason_9: int) -> str:
    """Interpret the code shared by the mower app and the TIF enum."""
    if 0 <= code < len(_COMMON_RESTRICTIONS):
        return _COMMON_RESTRICTIONS[code]
    return _REASON_9_OPTIONS[reason_9] if code == 9 else "unknown"


def is_planner_update(event: Event) -> bool:
    """Recognize both override layouts and the two scalar Planner events."""
    return event.family == PLANNER_EVENTS.family and len(event.payload) in {
        0: (_OVERRIDE.size, _OVERRIDE_WITH_MISSION.size),
        1: (4,),
        2: (1,),
    }.get(event.event_id, ())


@dataclass(frozen=True, slots=True)
class PlannerOverride:
    """The requested operation and the optional duration and work area."""

    action: str
    duration_seconds: int | None = None
    mission_id: int | None = None


async def async_read_planner_override(client: AutomowerClient) -> PlannerOverride:
    """Read both Planner layouts and ignore unused fields for an inactive override."""
    payload = await client.command(_GET_OVERRIDE)
    if len(payload) == _OVERRIDE.size:
        action, _, duration = _OVERRIDE.unpack(payload)
        mission = 0
    elif len(payload) == _OVERRIDE_WITH_MISSION.size:
        action, _, duration, mission = _OVERRIDE_WITH_MISSION.unpack(payload)
    else:
        raise ValueError("The mower returned an invalid Planner override")
    if action >= len(OVERRIDE_OPTIONS):
        raise ValueError("The mower returned an unknown Planner override action")
    if action == 0:
        return PlannerOverride(OVERRIDE_OPTIONS[action])
    return PlannerOverride(
        OVERRIDE_OPTIONS[action],
        duration_seconds=duration if 0 < duration < 0xFFFFFFFF else None,
        mission_id=mission if action == 2 and 0 < mission < 0xFFFFFFFF else None,
    )
