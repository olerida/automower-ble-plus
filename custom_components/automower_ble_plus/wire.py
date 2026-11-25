"""Encode and validate linked Automower HCP frames."""

from dataclasses import dataclass
from struct import Struct

_PREFIX = b"\x02\xfd"
_HEADER = Struct("<2sHIBB")
_REQUEST = Struct("<BBHHH")
_RESPONSE = Struct("<BBHHBH")
_EVENT = Struct("<BHHH")
_MIN_FRAME_SIZE = _HEADER.size + 2


class FrameError(ValueError):
    """The mower sent an invalid or unexpected HCP frame."""


def _checksum(data: bytes) -> int:
    """Return the CRC-8/MAXIM-DOW of this part of a frame."""
    value = 0
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0x8C if value & 1 else 0)
    return value


@dataclass(frozen=True, slots=True)
class Frame:
    """An HCP envelope carrying a link-management or application packet."""

    channel: int
    linked: bool
    payload: bytes

    def encode(self) -> bytes:
        """Encode the frame for the Bluetooth write characteristic."""
        length = _MIN_FRAME_SIZE + len(self.payload) - 4
        header = _HEADER.pack(_PREFIX, length, self.channel, self.linked, 0)[:-1]
        body = header + bytes((_checksum(header[1:]),)) + self.payload
        return body + bytes((_checksum(body[1:]), 3))

    @classmethod
    def decode(cls, packet: bytes) -> "Frame":
        """Validate the complete envelope before interpreting the payload."""
        if len(packet) < _MIN_FRAME_SIZE:
            raise FrameError("Truncated HCP frame")
        prefix, length, channel, linked, header_checksum = _HEADER.unpack_from(packet)
        if prefix != _PREFIX or packet[-1] != 3 or length != len(packet) - 4:
            raise FrameError("Invalid HCP frame boundaries")
        if linked not in (0, 1) or header_checksum != _checksum(packet[1:9]):
            raise FrameError("Invalid HCP header")
        if packet[-2] != _checksum(packet[1:-2]):
            raise FrameError("Invalid HCP frame checksum")
        return cls(channel, bool(linked), packet[_HEADER.size : -2])


def pop_frame(buffer: bytearray) -> Frame | None:
    """Consume one complete HCP frame from a stream of notifications."""
    if len(buffer) < 4:
        return None
    if buffer[:2] != _PREFIX:
        raise FrameError("Invalid HCP frame prefix")
    size = int.from_bytes(buffer[2:4], "little") + 4
    if size < _MIN_FRAME_SIZE:
        raise FrameError("Invalid HCP frame size")
    if len(buffer) < size:
        return None
    packet = bytes(buffer[:size])
    del buffer[:size]
    return Frame.decode(packet)


@dataclass(frozen=True, slots=True)
class Command:
    """An application command and its two 16-bit identifiers."""

    family: int
    operation: int

    def request(self, channel: int, payload: bytes = b"") -> Frame:
        """Build the linked request for this command."""
        header = _REQUEST.pack(0, 0xAF, self.family, self.operation, len(payload))
        return Frame(channel, True, header + payload)

    def response(self, channel: int, frame: Frame) -> tuple[int, bytes] | None:
        """Decode a matching response while allowing events on the same link."""
        if not frame.linked or frame.channel != channel:
            raise FrameError("Unexpected HCP response channel")
        if frame.payload.startswith(b"\x02"):
            return None
        if len(frame.payload) < _RESPONSE.size:
            raise FrameError("Truncated HCP response")
        kind, marker, family, operation, result, size = _RESPONSE.unpack_from(
            frame.payload
        )
        if (kind, marker, family, operation) != (1, 0xAF, self.family, self.operation):
            raise FrameError("HCP response does not match the request")
        payload = frame.payload[_RESPONSE.size :]
        if len(payload) != size:
            raise FrameError("Invalid HCP response payload length")
        return result, payload


@dataclass(frozen=True, slots=True)
class Event:
    """A notification emitted by the mower on a linked channel."""

    family: int
    event_id: int
    payload: bytes

    @classmethod
    def decode(cls, frame: Frame) -> "Event":
        """Decode the event header and verify the declared payload length."""
        if not frame.linked or len(frame.payload) < _EVENT.size:
            raise FrameError("Truncated or unlinked HCP event")
        kind, family, event_id, size = _EVENT.unpack_from(frame.payload)
        payload = frame.payload[_EVENT.size :]
        if kind != 2 or size != len(payload):
            raise FrameError("Invalid HCP event")
        return cls(family, event_id, payload)
