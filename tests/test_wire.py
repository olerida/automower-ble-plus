"""Exercise frame compatibility and fragmented notifications."""

import pytest

from custom_components.automower_ble_plus.wire import (
    Command,
    Frame,
    FrameError,
    pop_frame,
)


def test_observed_model_request_and_response():
    """Match the HCP byte vectors published in AutoMower-BLE's tests."""
    command = Command(4698, 9)
    assert (
        command.request(1739453030).encode().hex()
        == "02fd100066f2ad6701d700af5a1209000000b703"
    )

    reply = Frame.decode(
        bytes.fromhex("02fd1300b63b604701e601af5a1209000002001701c803")
    )
    assert command.response(1197489078, reply) == (0, bytes((23, 1)))


def test_fragmented_long_and_coalesced_frames():
    frame = Frame(12345, True, bytes(300))
    packet = frame.encode()
    buffer = bytearray(packet[:40])
    assert pop_frame(buffer) is None
    buffer.extend(packet[40:] + packet)
    assert pop_frame(buffer) == frame
    assert pop_frame(buffer) == frame
    assert not buffer


def test_reply_honors_channel_operation_status_and_checksum():
    command = Command(4698, 9)
    reply = Frame(10, True, bytes.fromhex("01af5a120900050000"))
    assert command.response(10, reply) == (5, b"")
    assert command.response(10, Frame(10, True, b"\x02event")) is None
    with pytest.raises(FrameError, match="channel"):
        command.response(11, reply)
    with pytest.raises(FrameError, match="does not match"):
        Command(4698, 10).response(10, reply)
    corrupt = bytearray(reply.encode())
    corrupt[-2] ^= 1
    with pytest.raises(FrameError, match="checksum"):
        Frame.decode(bytes(corrupt))
