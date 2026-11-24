"""Read and recenter the mower's GeoFence over BLE."""

from .features import POSITION, U8, U16
from .transport import AutomowerClient, CommandRejected
from .wire import Command

GEOFENCE_AVAILABLE = Command(4734, 0)
GEOFENCE_CENTER_SETTABLE = Command(4734, 5)
_CENTER = Command(4734, 4)
_RECENTER = Command(4734, 6)


async def async_get_geofence(client: AutomowerClient) -> dict:
    """Return the fence settings exposed by this mower."""
    try:
        available = U8.unpack(await client.command(GEOFENCE_AVAILABLE))[0] == 1
    except CommandRejected as err:
        if not err.unsupported:
            raise
        available = False
    data = {"available": available}
    if not available:
        return data
    for key, operation, codec in (
        ("enabled", Command(4734, 2), U8),
        ("center_settable", GEOFENCE_CENTER_SETTABLE, U8),
        ("radius_m", Command(4734, 10), U16),
        ("sensitivity", Command(4734, 7), U8),
        ("status", Command(4734, 9), U8),
    ):
        try:
            value = codec.unpack(await client.command(operation))[0]
        except CommandRejected as err:
            if err.unsupported:
                continue
            raise
        data[key] = value == 1 if key in ("enabled", "center_settable") else value
    try:
        longitude, latitude = POSITION.unpack(await client.command(_CENTER))
    except CommandRejected as err:
        if not err.unsupported:
            raise
    else:
        data["center_raw"] = {"longitude": longitude, "latitude": latitude}
    return data


async def async_recenter_geofence(client: AutomowerClient, pin: bytes) -> None:
    """Recenter at the mower's position using its operator PIN."""
    available = U8.unpack(await client.command(GEOFENCE_AVAILABLE))[0]
    if (
        available != 1
        or U8.unpack(await client.command(GEOFENCE_CENTER_SETTABLE))[0] != 1
    ):
        raise ValueError(
            "The mower does not currently allow moving the GeoFence center"
        )
    await client.command(_RECENTER, pin)
