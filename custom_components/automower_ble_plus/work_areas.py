"""Read model-supported mowing areas from the Missions BLE family."""

from automower_ble.protocol import ResponseResult

from .features import U8, U32
from .transport import AutomowerClient, CommandRejected
from .wire import Command

_AVAILABLE = Command(5604, 20)
_COUNT = Command(5604, 2)
_ENTRY = Command(5604, 3)
_TYPE = Command(5604, 13)
_ENABLED = Command(5604, 31)
_SCHEDULABLE = Command(5604, 18)
_GET_NAME = Command(5604, 7)
_SET_NAME = Command(5604, 8)
_SET_ENABLED = Command(5604, 32)


async def _async_available(client: AutomowerClient) -> bool:
    try:
        return U8.unpack(await client.command(_AVAILABLE))[0] == 1
    except CommandRejected as err:
        if not err.unsupported:
            raise
        return True  # Older profiles expose the list without an availability command.


def _decode_name(raw: bytes) -> str:
    return raw.decode("utf-16-le").partition("\0")[0]


async def async_get_work_areas(
    client: AutomowerClient, offset: int, limit: int
) -> dict:
    """Return the names and identifiers needed for area-specific schedules."""
    entries: list[dict] = []
    result = {"total": 0, "offset": offset, "work_areas": entries}
    if not await _async_available(client):
        return result
    try:
        count = U32.unpack(await client.command(_COUNT))[0]
    except CommandRejected as err:
        if err.unsupported:
            return result
        raise
    result["total"] = count
    if offset >= count:
        return result

    try:
        first = await client.command(_ENTRY, U32.pack(0))
    except CommandRejected as err:
        if err.result not in (
            ResponseResult.OUT_OF_RANGE,
            ResponseResult.INVALID_VALUE,
            ResponseResult.INVALID_ID,
        ):
            raise
        origin = 1
        first = None
    else:
        origin = 0

    for index in range(offset, min(count, offset + limit)):
        raw = (
            first
            if index == 0 and first is not None
            else await client.command(_ENTRY, U32.pack(index + origin))
        )
        mission_id = U32.unpack_from(raw)[0]
        name = _decode_name(raw[U32.size :])
        area = {"id": mission_id, "name": name}
        for key, command in (
            ("type", _TYPE),
            ("enabled", _ENABLED),
            ("schedulable", _SCHEDULABLE),
        ):
            try:
                value = U8.unpack(await client.command(command, U32.pack(mission_id)))[
                    0
                ]
            except CommandRejected as err:
                if err.unsupported:
                    continue
                raise
            area[key] = value if key == "type" else value == 1
        entries.append(area)
    return result


async def async_update_work_area(
    client: AutomowerClient, mission_id: int, *, name: str | None, enabled: bool | None
) -> None:
    """Update one setting for a stored area, then read the value back."""
    if (name is None) == (enabled is None):
        raise ValueError("Provide either a name or an enabled state")
    if not await _async_available(client):
        raise ValueError("This mower reports that work areas are unavailable")
    identity = U32.pack(mission_id)
    if name is not None:
        encoded = name.encode("utf-16-le")
        if not name.strip() or "\0" in name or len(encoded) > 62:
            raise ValueError("Enter a name using 1 to 31 UTF-16 code units")
        getter, setter = _GET_NAME, _SET_NAME
        payload = encoded + b"\0\0"
        expected = name
        decode = _decode_name
    else:
        getter, setter = _ENABLED, _SET_ENABLED
        payload = U8.pack(enabled)
        expected = U8.unpack(payload)
        decode = U8.unpack
    if decode(await client.command(getter, identity)) == expected:
        return
    await client.command(setter, identity + payload)
    if decode(await client.command(getter, identity)) != expected:
        raise ValueError("The mower returned a different work area value after writing")
