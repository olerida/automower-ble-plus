"""Diagnostic status from the mower's vision accessory and obstacle detection."""

from .features import (
    U8,
    VISION_ACCESSORY_AVAILABLE,
    VISION_OBSTACLE_AVOIDANCE_AVAILABLE,
    Reading,
)
from .transport import AutomowerClient, CommandRejected
from .wire import Command

_VISION_GROUPS = {
    VISION_ACCESSORY_AVAILABLE: {
        "vision_accessory_enabled": Command(7276, 2),
    },
    VISION_OBSTACLE_AVOIDANCE_AVAILABLE: {
        "vision_obstacle_avoidance_enabled": Command(7338, 4),
        "vision_obstacle_avoidance_active": Command(7338, 10),
        "vision_reduced_performance": Command(7338, 9),
        "vision_inactive_at_night": Command(7338, 11),
        "vision_dirty_lens": Command(7338, 12),
        "vision_wet_lens": Command(7338, 13),
    },
}
VISION_STATUS_KEYS = frozenset(
    key for group in _VISION_GROUPS.values() for key in group
)


async def async_read_vision_telemetry(client: AutomowerClient) -> dict[str, int]:
    """Return status fields supported by the available vision hardware."""
    data = {}
    for available, fields in _VISION_GROUPS.items():
        try:
            supported = Reading(available, U8).decode(await client.command(available))
        except CommandRejected as err:
            if err.unsupported:
                continue
            raise
        if supported != 1:
            continue
        for key, command in fields.items():
            try:
                result = Reading(command, U8).decode(await client.command(command))
            except CommandRejected as err:
                if err.unsupported:
                    continue
                raise
            if result in (0, 1):
                data[key] = result
    return data
