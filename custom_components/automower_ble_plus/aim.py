"""Read the mower's intelligent mapping status over Bluetooth."""

from automower_ble.protocol import ResponseResult

from .features import U8, U32, Reading
from .transport import AutomowerClient, CommandRejected
from .wire import Command, Event

AIM_EVENTS = Command(5758, 0)
START_AIM_MAPPING = Command(5758, 6)
_AIM_AVAILABLE = Reading(Command(5758, 2), U8)
_AIM_ENABLED = Reading(Command(5758, 4), U8)
_AIM_MAP_UPDATED = Reading(Command(5758, 5), U32)
_AIM_STATE = Reading(Command(5758, 7), U8)
_AIM_PROGRESS = Reading(Command(5758, 10), U8)
AIM_STATES = ("disabled", "building", "ready", "not_on_site")
AIM_READY = 2
AIM_KEYS = frozenset(
    {"aim_enabled", "aim_map_state", "aim_map_progress", "aim_map_updated"}
)


def is_aim_update(event: Event) -> bool:
    """Recognize both status layouts and the availability notification."""
    return event.family == AIM_EVENTS.family and (
        (event.event_id == 0 and len(event.payload) in (5, 6))
        or (event.event_id == 1 and len(event.payload) == 2)
    )


async def _async_read(client: AutomowerClient, reading: Reading) -> int | None:
    try:
        return reading.decode(await client.command(reading.command))
    except CommandRejected as err:
        if not err.unsupported and err.result != ResponseResult.NOT_AVAILABLE:
            raise
    except ValueError:
        pass
    return None


async def async_read_aim_status(client: AutomowerClient) -> dict[str, int]:
    """Read mapping fields that the connected mower actually provides."""
    if await _async_read(client, _AIM_AVAILABLE) != 1:
        return {}
    status = {}
    enabled = await _async_read(client, _AIM_ENABLED)
    if enabled in (0, 1):
        status["aim_enabled"] = enabled
    state = await _async_read(client, _AIM_STATE)
    if state is not None and state < len(AIM_STATES):
        status["aim_map_state"] = state
    if enabled != 1:
        return status
    progress = await _async_read(client, _AIM_PROGRESS)
    if progress is not None and progress <= 100:
        status["aim_map_progress"] = progress
    updated = await _async_read(client, _AIM_MAP_UPDATED)
    if updated not in (None, 0, 0xFFFFFFFF):
        status["aim_map_updated"] = updated
    return status


async def async_aim_map_ready(client: AutomowerClient) -> bool:
    """Check the live mapping prerequisites for an edit."""
    return (
        await _async_read(client, _AIM_AVAILABLE) == 1
        and await _async_read(client, _AIM_ENABLED) == 1
        and await _async_read(client, _AIM_STATE) == AIM_READY
    )
