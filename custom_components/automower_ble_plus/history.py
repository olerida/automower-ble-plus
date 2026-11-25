"""Read the mower's paginated BLE message history."""

from datetime import UTC, datetime
from struct import Struct

from .faults import SEVERITY_NAMES, message_description
from .features import U32
from .firmware import get_message_key
from .indexed import async_indexed_page
from .transport import AutomowerClient
from .wire import Command

_COUNT = Command(4730, 0)
_MESSAGE = Command(4730, 1)
_BASIC_MESSAGE = Struct("<IIB")
_POSITION = Struct("<ii")


def _decode_message(index: int | None, raw: bytes) -> dict:
    if len(raw) not in (_BASIC_MESSAGE.size, _BASIC_MESSAGE.size + _POSITION.size):
        raise ValueError("Unknown mower message format")
    timestamp, code, severity = _BASIC_MESSAGE.unpack_from(raw)
    result = {
        "timestamp": datetime.fromtimestamp(timestamp, UTC).isoformat()
        if timestamp
        else None,
        "code": code,
        "severity": severity,
    }
    if index is not None:
        result["index"] = index
    if message := message_description(code):
        result["message"] = message
    if key := get_message_key(code):
        result["message_key"] = key
    if severity < len(SEVERITY_NAMES):
        result["severity_name"] = SEVERITY_NAMES[severity]
    if len(raw) > _BASIC_MESSAGE.size:
        latitude, longitude = _POSITION.unpack_from(raw, _BASIC_MESSAGE.size)
        if abs(latitude) <= 90000000 and abs(longitude) <= 180000000:
            result.update(latitude=latitude / 1000000, longitude=longitude / 1000000)
    return result


def decode_message_event(raw: bytes) -> dict:
    """Decode a newly added message, omitting coordinates from event state."""
    count = U32.unpack_from(raw)[0]
    message = _decode_message(None, raw[U32.size :])
    message.pop("latitude", None)
    message.pop("longitude", None)
    return {"message_count": count, **message}


async def async_get_message_history(
    client: AutomowerClient, offset: int, limit: int
) -> dict:
    """Read a page in the mower's index order."""
    count = U32.unpack(await client.command(_COUNT))[0]
    messages = [
        _decode_message(index, raw)
        async for index, raw in async_indexed_page(
            client, _MESSAGE, count, offset, limit
        )
    ]
    return {"total": count, "offset": offset, "messages": messages}
