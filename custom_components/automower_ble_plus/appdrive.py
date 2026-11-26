"""Read appDrive state and eligibility for its built-in manoeuvres."""

from dataclasses import dataclass
from enum import IntEnum

from automower_ble.protocol import MowerState

from .features import U8, Reading
from .transport import AutomowerClient, CommandRejected
from .wire import Command

_FAMILY = 5358
APPDRIVE_EVENTS = Command(_FAMILY, 6)
APPDRIVE_ABORT = Command(_FAMILY, 7)
APPDRIVE_STATE = Reading(Command(_FAMILY, 0), U8)
APPDRIVE_SUPPORTED = Reading(Command(_FAMILY, 8), U8)
_APPDRIVE_ACTIVITY = Reading(Command(_FAMILY, 10), U8)
APPDRIVE_REMOTE_CONTROL = Reading(Command(5100, 12), U8)
APPDRIVE_MANOEUVRE_TIMEOUT = 90


class AppDriveState(IntEnum):
    """Remote driver states reported by the supported TIF profiles."""

    IDLE = 0
    IN_OPERATION = 1
    PERFORMING_ACTION = 2
    ERROR = 3
    PENDING_START = 4


class AppDriveManoeuvreStatus(IntEnum):
    """Outcome of a manoeuvre requested from Home Assistant."""

    IN_PROGRESS = 0
    COMPLETE = 1
    STOP_REQUESTED = 2
    FAILED = 3
    UNCONFIRMED = 4


@dataclass(frozen=True)
class AppDriveManoeuvre:
    """A firmware-controlled trip into or out of the charging station."""

    name: str
    command: Command
    possible: Reading
    ends_in_station: bool

    @property
    def status_key(self) -> str:
        return f"appdrive_{self.name}_status"

    @property
    def availability_key(self) -> str:
        return f"appdrive_{self.name}_possible"

    def allows_start(self, mower_state: int | None, driver_state: int | None) -> bool:
        """Allow a paused mower or an active appDrive session returning to dock."""
        return mower_state == MowerState.PAUSED or (
            self.ends_in_station
            and mower_state == MowerState.IN_OPERATION
            and driver_state == AppDriveState.IN_OPERATION
        )


APPDRIVE_DOCK = AppDriveManoeuvre(
    "dock", Command(_FAMILY, 2), Reading(Command(_FAMILY, 4), U8), True
)
APPDRIVE_UNDOCK = AppDriveManoeuvre(
    "undock", Command(_FAMILY, 3), Reading(Command(_FAMILY, 5), U8), False
)
APPDRIVE_MANOEUVRES = (APPDRIVE_DOCK, APPDRIVE_UNDOCK)

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
    **{
        manoeuvre.status_key: tuple(
            status.name.lower() for status in AppDriveManoeuvreStatus
        )
        for manoeuvre in APPDRIVE_MANOEUVRES
    },
}
APPDRIVE_BINARY_KEYS = frozenset(
    (
        "appdrive_remote_control",
        *(manoeuvre.availability_key for manoeuvre in APPDRIVE_MANOEUVRES),
    )
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
        **{
            manoeuvre.availability_key: manoeuvre.possible
            for manoeuvre in APPDRIVE_MANOEUVRES
        },
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
