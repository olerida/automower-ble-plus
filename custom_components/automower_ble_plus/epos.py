"""Read EPOS positioning and charging station installation status over BLE."""

from math import hypot
from struct import Struct

from .features import U8, U32, Reading
from .reference_station import async_get_reference_station_id
from .transport import AutomowerClient, CommandRejected
from .wire import Command

EPOS_AVAILABLE = Reading(Command(6170, 1), U8)
_EPOS_ENABLED = Reading(Command(6170, 2), U8)
_CLOUD_ENABLED = Reading(Command(6778, 4), U8)
_LOCAL_POSITION = Command(5242, 0)
_LOCAL_COORDINATES = Struct("<iii")
_DOCK_BY_WIRE_AVAILABLE = Reading(Command(6170, 10), U8)
_DOCK_BY_WIRE_ENABLED = Reading(Command(6170, 11), U8)

EPOS_ENUM_OPTIONS = {
    "epos_rtk_solution": ("no_fix", "float_fix", "fixed"),
    "epos_installation_state": ("idle", "in_operation"),
    "epos_installation_result": ("none", "success", "error", "stopped"),
    "epos_reference_station_channel": ("radio", "wifi"),
}
_STATUS = {
    "epos_rtk_solution": Reading(Command(5336, 3), U8),
    "epos_installation_state": Reading(Command(4692, 23), U32),
    "epos_installation_result": Reading(Command(4692, 24), U32),
}
_STATION_CHANNEL = Reading(Command(5650, 17), U8)
_SIGNAL_QUALITY = {
    0: ("epos_reference_station_signal", Reading(Command(5650, 4), U8)),
    1: ("epos_cloud_signal", Reading(Command(6778, 11), U8)),
}
EPOS_BINARY_KEYS = frozenset({"epos_enabled", "epos_dock_by_wire"})
EPOS_STATUS_KEYS = (
    EPOS_BINARY_KEYS
    | EPOS_ENUM_OPTIONS.keys()
    | {
        "epos_reference_station_signal",
        "epos_cloud_signal",
        "epos_reference_station_distance",
        "epos_reference_station_id",
    }
)


async def _read(client: AutomowerClient, reading: Reading) -> int | None:
    try:
        return reading.decode(await client.command(reading.command))
    except CommandRejected as err:
        if not err.unsupported:
            raise
    except ValueError:
        # Discard malformed optional fields without hiding the others.
        pass
    return None


async def async_read_epos_status(client: AutomowerClient) -> dict[str, int]:
    """Return status for the currently available positioning hardware and source."""
    if await _read(client, EPOS_AVAILABLE) != 1:
        return {}
    enabled = await _read(client, _EPOS_ENABLED)
    if enabled not in (0, 1):
        return {}
    data = {"epos_enabled": enabled}
    if (station_id := await async_get_reference_station_id(client)) is not None:
        data["epos_reference_station_id"] = station_id
    if not enabled:
        return data

    for key, reading in _STATUS.items():
        value = await _read(client, reading)
        if value is not None and 0 <= value < len(EPOS_ENUM_OPTIONS[key]):
            data[key] = value

    if await _read(client, _DOCK_BY_WIRE_AVAILABLE) == 1:
        dock_by_wire = await _read(client, _DOCK_BY_WIRE_ENABLED)
        if dock_by_wire in (0, 1):
            data["epos_dock_by_wire"] = dock_by_wire

    cloud_enabled = await _read(client, _CLOUD_ENABLED)
    # Older direct-station models have no EPOS Service node.
    source = 1 if cloud_enabled == 1 else 0
    key, reading = _SIGNAL_QUALITY[source]
    quality = await _read(client, reading)
    if quality is not None and 1 <= quality <= 100:
        data[key] = quality
    if not source:
        channel = await _read(client, _STATION_CHANNEL)
        if channel in (0, 1):
            data["epos_reference_station_channel"] = channel
        try:
            position = await client.command(_LOCAL_POSITION)
        except CommandRejected as err:
            if not err.unsupported:
                raise
        else:
            if len(position) == _LOCAL_COORDINATES.size:
                east, north, accuracy = _LOCAL_COORDINATES.unpack(position)
                if accuracy >= 0:
                    data["epos_reference_station_distance"] = round(hypot(east, north))
    return data
