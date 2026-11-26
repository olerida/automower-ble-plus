"""Read EPOS positioning and charging station installation status over BLE."""

from math import hypot
from struct import Struct

from automower_ble.protocol import ResponseResult

from .features import U8, U32, Reading
from .reference_station import async_get_reference_station_id
from .transport import AutomowerClient, CommandRejected
from .wire import Command, Event

EPOS_EVENTS = Command(6170, 0)
EPOS_CLOUD_EVENTS = (Command(6778, 1), Command(6778, 2))
EPOS_AVAILABLE = Reading(Command(6170, 1), U8)
_EPOS_ENABLED = Reading(Command(6170, 2), U8)
_CLOUD_ENABLED = Reading(Command(6778, 4), U8)
_LOCAL_POSITION = Command(5242, 0)
_LOCAL_COORDINATES = Struct("<iii")
_DOCK_BY_WIRE_AVAILABLE = Reading(Command(6170, 10), U8)
_DOCK_BY_WIRE_ENABLED = Reading(Command(6170, 11), U8)
_SET_DOCK_BY_WIRE = Command(6170, 12)
_WIRE_ASSISTANCE_AVAILABLE = Reading(Command(6170, 7), U8)
_WIRE_ASSISTANCE_ENABLED = Reading(Command(6170, 8), U8)
RUN_DOCKING_PATTERN = Command(4692, 21)
STOP_DOCKING_PATTERN = Command(4692, 35)
EPOS_DOCK_METHOD_OPTIONS = ("virtual", "guide_wire")

EPOS_ENUM_OPTIONS = {
    "epos_rtk_solution": ("no_fix", "float_fix", "fixed"),
    "epos_installation_state": ("idle", "in_operation"),
    "epos_installation_result": ("none", "success", "error", "stopped"),
    "epos_reference_station_channel": ("radio", "wifi"),
    "epos_boundary_mode": ("boundary_wire", "virtual", "virtual_assisted_by_wire"),
    "epos_correction_source": ("local_reference_station", "husqvarna_cloud"),
    "epos_cloud_connection_state": (
        "not_connected",
        "no_communication_channel",
        "connecting",
        "connected",
    ),
    "epos_cloud_service_state": (
        "unknown",
        "ok",
        "invalid_ipr",
        "no_subscription",
        "no_position",
        "data_provider_unreachable",
        "tile_unavailable",
        "reference_station_not_connected",
    ),
    "epos_cloud_correction_format": ("spartn", "rtcm"),
    "site_map_installation_type": (
        "undefined",
        "site_map_only",
        "residential",
        "professional",
        "gardena",
    ),
}
_MAP_INSTALLATION_TYPE = Reading(Command(5578, 52), U8)
_RTK_SOLUTION = Reading(Command(5336, 3), U8)
_DOCKING_STATUS = {
    "epos_installation_state": Reading(Command(4692, 23), U32),
    "epos_installation_result": Reading(Command(4692, 24), U32),
}
_STATION_CHANNEL = Reading(Command(5650, 17), U8)
_CLOUD_STATUS = {
    "epos_cloud_has_origin": Reading(Command(6778, 15), U8),
    "epos_cloud_origin_valid": Reading(Command(6778, 16), U8),
    "epos_cloud_connection_state": Reading(Command(6778, 19), U8),
    "epos_cloud_service_state": Reading(Command(6778, 7), U8),
    "epos_cloud_correction_format": Reading(Command(6778, 29), U8),
}
_SIGNAL_QUALITY = {
    0: ("epos_reference_station_signal", Reading(Command(5650, 4), U8)),
    1: ("epos_cloud_signal", Reading(Command(6778, 11), U8)),
}
EPOS_BINARY_KEYS = frozenset(
    {
        "epos_enabled",
        "epos_dock_by_wire",
        "epos_cloud_has_origin",
        "epos_cloud_origin_valid",
    }
)
EPOS_STATUS_KEYS = (
    EPOS_BINARY_KEYS
    | EPOS_ENUM_OPTIONS.keys()
    | {
        "epos_reference_station_signal",
        "epos_cloud_signal",
        "epos_reference_station_distance",
        "epos_reference_station_id",
        "epos_cloud_enabled",
    }
)


def is_epos_update(event: Event) -> bool:
    """Recognize position settings or cloud status changes without decoding a partial snapshot."""
    if event.family == EPOS_EVENTS.family:
        return event.event_id == 0 and 2 <= len(event.payload) <= 7
    return (
        event.family == EPOS_CLOUD_EVENTS[0].family
        and event.event_id in (0, 1, 3, 4, 6, 7, 8, 12)
        and len(event.payload) == 1
    )


async def _read(client: AutomowerClient, reading: Reading) -> int | None:
    try:
        return reading.decode(await client.command(reading.command))
    except CommandRejected as err:
        if not err.unsupported and err.result != ResponseResult.NOT_AVAILABLE:
            raise
    except ValueError:
        # Discard malformed optional fields without hiding the others.
        pass
    return None


async def async_epos_enabled(client: AutomowerClient) -> bool:
    """Check whether the robot is operating with EPOS positioning enabled."""
    return await _read(client, _EPOS_ENABLED) == 1


async def async_set_dock_by_wire(client: AutomowerClient, enabled: bool) -> int:
    """Set the EPOS station's guidance method and verify the value over BLE."""
    if (
        not await async_epos_enabled(client)
        or await _read(client, _DOCK_BY_WIRE_AVAILABLE) != 1
    ):
        raise ValueError("The mower does not offer EPOS docking by wire")
    current = await _read(client, _DOCK_BY_WIRE_ENABLED)
    if current not in (0, 1):
        raise ValueError("The mower does not report its current docking method")
    if current != enabled:
        await client.command(_SET_DOCK_BY_WIRE, U8.pack(enabled))
        if await _read(client, _DOCK_BY_WIRE_ENABLED) != enabled:
            raise ValueError("The mower did not confirm the EPOS docking method")
    return int(enabled)


async def async_read_docking_status(
    client: AutomowerClient, *, check_epos: bool = False
) -> dict[str, int]:
    """Read the state and outcome of the station's docking-point pattern."""
    if check_epos:
        available = await _read(client, EPOS_AVAILABLE)
        if available == 0 or (
            available == 1 and await _read(client, _EPOS_ENABLED) != 1
        ):
            return {}
    data = {}
    for key, reading in _DOCKING_STATUS.items():
        value = await _read(client, reading)
        if value is not None and 0 <= value < len(EPOS_ENUM_OPTIONS[key]):
            data[key] = value
    return data


async def async_read_epos_status(
    client: AutomowerClient, *, docking_pattern: bool = False
) -> dict[str, int]:
    """Return status for the currently available positioning hardware and source."""
    available = await _read(client, EPOS_AVAILABLE)
    if available != 1:
        # Some EPOS firmware declares the station pattern without an EPOS node.
        if available is None and docking_pattern:
            return await async_read_docking_status(client)
        return {}
    enabled = await _read(client, _EPOS_ENABLED)
    if enabled not in (0, 1):
        return {}
    data = {"epos_enabled": enabled}
    if (station_id := await async_get_reference_station_id(client)) is not None:
        data["epos_reference_station_id"] = station_id
    if not enabled:
        data["epos_boundary_mode"] = 0
        return data

    if await _read(client, _WIRE_ASSISTANCE_AVAILABLE) == 1:
        wire_assistance = await _read(client, _WIRE_ASSISTANCE_ENABLED)
        if wire_assistance in (0, 1):
            data["epos_boundary_mode"] = 1 + wire_assistance
    else:
        data["epos_boundary_mode"] = 1
    installation_type = await _read(client, _MAP_INSTALLATION_TYPE)
    if installation_type is not None and 0 <= installation_type < len(
        EPOS_ENUM_OPTIONS["site_map_installation_type"]
    ):
        data["site_map_installation_type"] = installation_type

    data.update(await async_read_docking_status(client))
    solution = await _read(client, _RTK_SOLUTION)
    if solution in (0, 1, 2):
        data["epos_rtk_solution"] = solution

    if await _read(client, _DOCK_BY_WIRE_AVAILABLE) == 1:
        dock_by_wire = await _read(client, _DOCK_BY_WIRE_ENABLED)
        if dock_by_wire in (0, 1):
            data["epos_dock_by_wire"] = dock_by_wire

    cloud_enabled = await _read(client, _CLOUD_ENABLED)
    if cloud_enabled in (0, 1):
        data["epos_cloud_enabled"] = cloud_enabled
    # Older direct-station models have no EPOS Service node.
    source = 1 if cloud_enabled == 1 else 0
    key, reading = _SIGNAL_QUALITY[source]
    quality = await _read(client, reading)
    if quality is not None and 1 <= quality <= 100:
        data[key] = quality
    if source:
        data["epos_correction_source"] = 1
        for key, reading in _CLOUD_STATUS.items():
            value = await _read(client, reading)
            if value is not None and 0 <= value < len(
                EPOS_ENUM_OPTIONS.get(key, (0, 1))
            ):
                data[key] = value
    else:
        channel = await _read(client, _STATION_CHANNEL)
        if channel in (0, 1):
            data["epos_reference_station_channel"] = channel
        if station_id or (quality is not None and 1 <= quality <= 100):
            data["epos_correction_source"] = 0
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
