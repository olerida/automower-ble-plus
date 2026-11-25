"""Decode the active mowing or parking override reported by the Planner."""

from dataclasses import dataclass
from struct import Struct

from .transport import AutomowerClient
from .wire import Command

_GET_OVERRIDE = Command(4658, 2)
_OVERRIDE = Struct("<BII")
_OVERRIDE_WITH_MISSION = Struct("<BIII")
OVERRIDE_OPTIONS = ("inactive", "forced_park", "forced_mow")


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
