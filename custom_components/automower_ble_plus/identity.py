"""Read and write the name stored in the mower over Bluetooth."""

from .wire import Command

GET_MOWER_NAME = Command(4698, 3)
SET_MOWER_NAME = Command(4698, 4)
GET_SERIAL_NUMBER = Command(4698, 10)
MAX_MOWER_NAME_LENGTH = 21
_NAME_BYTES = 2 * MAX_MOWER_NAME_LENGTH


def decode_mower_name(payload: bytes) -> str:
    """Read the 21 UTF-16 units, ignoring any padding after the first null."""
    if len(payload) != _NAME_BYTES:
        raise ValueError("The mower reported an invalid name length")
    end = next(
        (
            index
            for index in range(0, _NAME_BYTES, 2)
            if payload[index : index + 2] == b"\0\0"
        ),
        _NAME_BYTES,
    )
    return payload[:end].decode("utf-16-le")


def encode_mower_name(name: str) -> bytes:
    """Serialize the mower's fixed-width little-endian Unicode name field."""
    if not name or "\0" in name:
        raise ValueError(
            "The mower name must be nonempty and contain no null character"
        )
    encoded = name.encode("utf-16-le")
    if len(encoded) > _NAME_BYTES:
        raise ValueError(
            f"The mower name supports at most {MAX_MOWER_NAME_LENGTH} UTF-16 units"
        )
    return encoded.ljust(_NAME_BYTES, b"\0")
