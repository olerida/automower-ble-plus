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


async def async_get_work_areas(
    client: AutomowerClient, offset: int, limit: int
) -> dict:
    """Return the names and identifiers needed for area-specific schedules."""
    try:
        available = U8.unpack(await client.command(_AVAILABLE))[0] == 1
    except CommandRejected as err:
        if not err.unsupported:
            raise
        available = (
            True  # Older profiles expose the list without an availability command.
        )
    entries: list[dict] = []
    result = {"total": 0, "offset": offset, "work_areas": entries}
    if not available:
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
        name = raw[U32.size :].decode("utf-16-le").split("\0", maxsplit=1)[0]
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
