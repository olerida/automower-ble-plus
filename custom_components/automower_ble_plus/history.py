"""Read the mower's paginated BLE message history."""

from datetime import UTC, datetime
from struct import Struct

from automower_ble.error_codes import ErrorCodes
from automower_ble.protocol import ResponseResult

from .features import U32
from .transport import AutomowerClient, CommandRejected
from .wire import Command

_COUNT = Command(4730, 0)
_MESSAGE = Command(4730, 1)
_BASIC_MESSAGE = Struct("<IIB")
_POSITION = Struct("<ii")


def _decode_message(index: int, raw: bytes) -> dict:
    if len(raw) not in (_BASIC_MESSAGE.size, _BASIC_MESSAGE.size + _POSITION.size):
        raise ValueError("Unknown mower message format")
    timestamp, code, severity = _BASIC_MESSAGE.unpack_from(raw)
    result = {
        "index": index,
        "timestamp": datetime.fromtimestamp(timestamp, UTC).isoformat()
        if timestamp
        else None,
        "code": code,
        "severity": severity,
    }
    try:
        result["message"] = ErrorCodes(code).name.replace("_", " ").title()
    except ValueError:
        pass
    if len(raw) > _BASIC_MESSAGE.size:
        latitude, longitude = _POSITION.unpack_from(raw, _BASIC_MESSAGE.size)
        if abs(latitude) <= 90000000 and abs(longitude) <= 180000000:
            result.update(latitude=latitude / 1000000, longitude=longitude / 1000000)
    return result


async def async_get_message_history(
    client: AutomowerClient, offset: int, limit: int
) -> dict:
    """Read a page in the mower's index order."""
    count = U32.unpack(await client.command(_COUNT))[0]
    end = min(count, offset + limit)
    messages: list[dict] = []
    result = {"total": count, "offset": offset, "messages": messages}
    if offset >= end:
        return result
    try:
        first = await client.command(_MESSAGE, U32.pack(0))
    except CommandRejected as err:
        if err.result not in (
            ResponseResult.INVALID_ID,
            ResponseResult.OUT_OF_RANGE,
            ResponseResult.INVALID_VALUE,
        ):
            raise
        origin = 1
    else:
        origin = 0
        if offset == 0:
            messages.append(_decode_message(0, first))
    for index in range(offset + len(messages), end):
        raw = await client.command(_MESSAGE, U32.pack(index + origin))
        messages.append(_decode_message(index, raw))
    return result
