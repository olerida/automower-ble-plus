"""Read model-supported mowing areas from the Missions BLE family."""

from .features import U8, U32
from .indexed import async_indexed_page, decode_name, encode_name
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
    async for _, raw in async_indexed_page(client, _ENTRY, count, offset, limit):
        mission_id = U32.unpack_from(raw)[0]
        name = decode_name(raw[U32.size :])
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
        getter, setter = _GET_NAME, _SET_NAME
        payload = encode_name(name)
        expected = name
        decode = decode_name
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
