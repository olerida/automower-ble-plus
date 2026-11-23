"""Read indexed records with the zero- and one-based BLE conventions."""

from collections.abc import AsyncIterator

from automower_ble.protocol import ResponseResult

from .features import U32
from .transport import AutomowerClient, CommandRejected
from .wire import Command


async def async_indexed_page(
    client: AutomowerClient, command: Command, count: int, offset: int, limit: int
) -> AsyncIterator[tuple[int, bytes]]:
    """Yield response packets by index relative to the beginning of the list."""
    if offset >= count:
        return
    try:
        first = await client.command(command, U32.pack(0))
    except CommandRejected as err:
        if err.result not in (
            ResponseResult.INVALID_ID,
            ResponseResult.OUT_OF_RANGE,
            ResponseResult.INVALID_VALUE,
        ):
            raise
        first = None
    origin = int(first is None)
    for index in range(offset, min(count, offset + limit)):
        packet = (
            first
            if index == 0 and first is not None
            else await client.command(command, U32.pack(index + origin))
        )
        yield index, packet


def decode_name(data: bytes) -> str:
    """Decode a BLE name stored as null-terminated UTF-16LE."""
    return data.decode("utf-16-le").partition("\0")[0]


def encode_name(name: str) -> bytes:
    """Encode a name within the mower's 32-unit field, including the terminator."""
    encoded = name.encode("utf-16-le")
    if not name.strip() or "\0" in name or len(encoded) > 62:
        raise ValueError("Enter a name using 1 to 31 UTF-16 code units")
    return encoded + b"\0\0"
