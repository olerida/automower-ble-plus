"""Read appDrive state and eligibility for the mower's undock manoeuvre."""

from enum import IntEnum

from .features import U8, Reading
from .transport import AutomowerClient, CommandRejected
from .wire import Command

_FAMILY = 5358
APPDRIVE_EVENTS = Command(_FAMILY, 6)
APPDRIVE_ABORT = Command(_FAMILY, 7)
APPDRIVE_UNDOCK = Command(_FAMILY, 3)
APPDRIVE_STATE = Reading(Command(_FAMILY, 0), U8)
APPDRIVE_SUPPORTED = Reading(Command(_FAMILY, 8), U8)
APPDRIVE_UNDOCK_POSSIBLE = Reading(Command(_FAMILY, 5), U8)
_APPDRIVE_DOCK_POSSIBLE = Reading(Command(_FAMILY, 4), U8)
_APPDRIVE_ACTIVITY = Reading(Command(_FAMILY, 10), U8)
APPDRIVE_REMOTE_CONTROL = Reading(Command(5100, 12), U8)
APPDRIVE_UNDOCK_TIMEOUT = 90


class AppDriveState(IntEnum):
    """Remote driver states reported by the supported TIF profiles."""

    IDLE = 0
    IN_OPERATION = 1
    PERFORMING_ACTION = 2
    ERROR = 3
    PENDING_START = 4


class UndockStatus(IntEnum):
    """Outcome of an undock requested from Home Assistant."""

    IN_PROGRESS = 0
    COMPLETE = 1
    STOP_REQUESTED = 2
    FAILED = 3
    UNCONFIRMED = 4


APPDRIVE_ENUM_OPTIONS = {
    "appdrive_state": tuple(state.name.lower() for state in AppDriveState),
    "appdrive_activity": (
        "none",
        "undocking",
        "docking",
        "planning",
        "searching_for_satellites",
        "driving_to_point",
    ),
    "appdrive_undock_status": tuple(status.name.lower() for status in UndockStatus),
}
APPDRIVE_BINARY_KEYS = frozenset(
    {"appdrive_remote_control", "appdrive_dock_possible", "appdrive_undock_possible"}
)
APPDRIVE_STATUS_KEYS = APPDRIVE_BINARY_KEYS | {"appdrive_state", "appdrive_activity"}


async def async_read_appdrive_status(client: AutomowerClient) -> dict[str, int]:
    """Read appDrive state and optional telemetry only on a compatible mower."""
    if await _async_read_optional(client, APPDRIVE_SUPPORTED) != 1:
        return {}
    readings = {
        "appdrive_state": APPDRIVE_STATE,
        "appdrive_activity": _APPDRIVE_ACTIVITY,
        "appdrive_remote_control": APPDRIVE_REMOTE_CONTROL,
        "appdrive_dock_possible": _APPDRIVE_DOCK_POSSIBLE,
        "appdrive_undock_possible": APPDRIVE_UNDOCK_POSSIBLE,
    }
    data = {}
    for key, reading in readings.items():
        value = await _async_read_optional(client, reading)
        if value is not None and 0 <= value < len(
            APPDRIVE_ENUM_OPTIONS.get(key, (0, 1))
        ):
            data[key] = value
    return data


async def async_remote_control_available(client: AutomowerClient) -> int | None:
    """Return the optional HMI remote-control flag."""
    try:
        return APPDRIVE_REMOTE_CONTROL.decode(
            await client.command(APPDRIVE_REMOTE_CONTROL.command)
        )
    except CommandRejected as err:
        if not err.unsupported:
            raise
        return None


async def _async_read_optional(client: AutomowerClient, reading: Reading) -> int | None:
    try:
        return reading.decode(await client.command(reading.command))
    except CommandRejected as err:
        if not err.unsupported:
            raise
    except ValueError:
        pass
    return None
