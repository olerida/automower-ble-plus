"""Read geographic and installation-frame mower coordinates over Bluetooth."""

from struct import Struct

from .features import (
    GET_POSITION,
    GET_POSITION_AVAILABLE,
    POSITION,
    POSITION_SCALE,
    U8,
    Reading,
)
from .transport import AutomowerClient, CommandRejected
from .wire import Command

GPS_POSITION_EVENTS = Command(5264, 1)
GPS_POSITION_REPORT = 0
GPS_COORDINATE_KEYS = frozenset({"latitude", "longitude"})
LOCAL_POSITION_KEYS = frozenset(
    {"local_position_accuracy", "local_heading", "local_heading_accuracy"}
)

_GPS_AVAILABLE = Reading(GET_POSITION_AVAILABLE, U8)
_GET_LOCAL_POSE = Command(5242, 5)
_GET_LOCAL_ORIGIN = Command(5242, 11)
_LOCAL_POSE = Struct("<iiihiih")
_MAX_POSITION_ACCURACY = (1 << 31) - 1


def decode_gps_position(payload: bytes) -> dict[str, int]:
    """Decode the mower's signed 0.1-microdegree coordinates."""
    if len(payload) != POSITION.size:
        raise ValueError("Unexpected GPS position length")
    longitude, latitude = POSITION.unpack(payload)
    if (
        (longitude or latitude)
        and -180 * POSITION_SCALE <= longitude <= 180 * POSITION_SCALE
        and -90 * POSITION_SCALE <= latitude <= 90 * POSITION_SCALE
    ):
        return {"latitude": latitude, "longitude": longitude}
    return {}


async def async_read_gps_position(client: AutomowerClient) -> dict[str, int]:
    """Read GPS when available, including firmware without a capability query."""
    try:
        available = _GPS_AVAILABLE.decode(await client.command(_GPS_AVAILABLE.command))
    except CommandRejected as err:
        if not err.unsupported:
            raise
        # Earlier profiles offer Position.GetPosition without GetAvailable.
    else:
        if available != 1:
            return {}
    payload = await _async_read_optional(client, GET_POSITION)
    if payload is None:
        return {}
    return {"gps_position_supported": 1, **decode_gps_position(payload)}


async def async_read_local_pose(client: AutomowerClient) -> dict[str, int] | None:
    """Read the mower's position and accuracy in its local installation frame."""
    payload = await _async_read_optional(client, _GET_LOCAL_POSE)
    if payload is None:
        return None
    if len(payload) != _LOCAL_POSE.size:
        raise ValueError("Unexpected local position length")
    x, y, z, heading, xy_accuracy, z_accuracy, heading_accuracy = _LOCAL_POSE.unpack(
        payload
    )
    data = {}
    if 0 <= xy_accuracy < _MAX_POSITION_ACCURACY:
        data.update(x_mm=x, y_mm=y, local_position_accuracy=xy_accuracy)
    if 0 <= z_accuracy < _MAX_POSITION_ACCURACY:
        data.update(z_mm=z, z_accuracy_mm=z_accuracy)
    if 0 <= heading_accuracy <= 3600 and -3600 <= heading <= 3600:
        data.update(
            local_heading=heading % 3600, local_heading_accuracy=heading_accuracy
        )
    return data


async def async_read_local_origin(client: AutomowerClient) -> dict[str, int]:
    """Read the installation's geographic origin when the firmware provides one."""
    payload = await _async_read_optional(client, _GET_LOCAL_ORIGIN)
    return decode_gps_position(payload) if payload is not None else {}


def geographic_degrees(position: dict[str, int]) -> dict[str, float] | None:
    """Return geographic latitude and longitude in degrees for Home Assistant."""
    if GPS_COORDINATE_KEYS <= position.keys():
        return {
            key: position[key] / POSITION_SCALE for key in ("latitude", "longitude")
        }
    return None


async def async_describe_position(client: AutomowerClient) -> dict:
    """Return fresh geographic and installation-frame positioning."""
    gps = geographic_degrees(await async_read_gps_position(client))
    pose = await async_read_local_pose(client)
    if not pose:
        return {"gps": gps, "local": None}

    local: dict[str, int | float | dict[str, float]] = {
        key: pose[key]
        for key in ("x_mm", "y_mm", "z_mm", "z_accuracy_mm")
        if key in pose
    }
    if "local_position_accuracy" in pose:
        local["xy_accuracy_mm"] = pose["local_position_accuracy"]
        if (
            origin := geographic_degrees(await async_read_local_origin(client))
        ) is not None:
            local["origin"] = origin
    if "local_heading" in pose:
        local["heading_degrees"] = pose["local_heading"] / 10
        local["heading_accuracy_degrees"] = pose["local_heading_accuracy"] / 10
    return {"gps": gps, "local": local}


async def _async_read_optional(
    client: AutomowerClient, command: Command
) -> bytes | None:
    try:
        return await client.command(command)
    except CommandRejected as err:
        if not err.unsupported:
            raise
        return None
