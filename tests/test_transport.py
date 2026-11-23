"""Connect through the actual released GATT client against simulated hardware."""

import asyncio
from struct import pack, unpack_from
from types import SimpleNamespace
from unittest.mock import AsyncMock

import automower_ble.protocol
import pytest
from automower_ble.protocol import ResponseResult

from custom_components.automower_ble_plus.transport import (
    AutomowerClient,
    CommandRejected,
)
from custom_components.automower_ble_plus.wire import Command, Frame, pop_frame


class FakeGatt:
    def __init__(self):
        write = SimpleNamespace(
            uuid="98bd0002-0b0e-421a-84e5-ddbf75dc6de4", properties=["write"]
        )
        read = SimpleNamespace(
            uuid="98bd0003-0b0e-421a-84e5-ddbf75dc6de4", properties=["notify"]
        )
        self.services = [SimpleNamespace(characteristics=[write, read])]
        self._backend = SimpleNamespace()
        self.is_connected = True
        self.pair = AsyncMock()
        self.pending = bytearray()

    async def start_notify(self, characteristic, callback):
        self.notify = callback

    async def disconnect(self):
        self.is_connected = False

    async def write_gatt_char(self, characteristic, data, *, response):
        assert not response
        self.pending.extend(data)
        frame = pop_frame(self.pending)
        if frame is None:
            return
        if not frame.linked:
            payload = bytes((frame.payload[0] + 1,))
        else:
            family, operation = unpack_from("<HH", frame.payload, 2)
            result, value = 0, b""
            if (family, operation) == (4664, 4):
                if frame.payload[8:] != pack("<H", 4567):
                    result = ResponseResult.INVALID_PIN
            elif (family, operation) == (4698, 9):
                value = b"\x05\x00"
            elif (family, operation) == (96, 0):
                value = b"App-G3_devicetype-5.0\x00"
            else:
                result = ResponseResult.INVALID_ID
            payload = (
                pack("<BBHHBH", 1, 0xAF, family, operation, result, len(value)) + value
            )
        reply = Frame(frame.channel, frame.linked, payload).encode()
        await self.notify(characteristic, reply[:8])
        await self.notify(characteristic, reply[8:])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pin", "result"),
    [("4567", ResponseResult.OK), ("9876", ResponseResult.INVALID_PIN)],
)
async def test_authenticated_gatt_exchange(monkeypatch, pin, result):
    gatt = FakeGatt()
    monkeypatch.setattr(
        automower_ble.protocol, "establish_connection", AsyncMock(return_value=gatt)
    )
    original_sleep = asyncio.sleep

    async def skip_pairing_wait(delay):
        await original_sleep(0 if delay == 5 else delay)

    monkeypatch.setattr(automower_ble.protocol.asyncio, "sleep", skip_pairing_wait)
    client = AutomowerClient("00:11:22:33:44:55", pin)
    assert await client.connect(SimpleNamespace(name="Automower 420")) == result
    if result == ResponseResult.OK:
        assert await client.main_node_version() == "App-G3_devicetype-5.0"
        assert await client.command(Command(4698, 9)) == b"\x05\x00"
        with pytest.raises(CommandRejected, match="INVALID_ID"):
            await client.command(Command(4722, 2))
        assert gatt.is_connected
        await client.disconnect()
    assert not gatt.is_connected
