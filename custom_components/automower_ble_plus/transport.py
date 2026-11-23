"""Transport for authenticated Automower HCP commands over BLE."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from secrets import randbelow

from automower_ble.protocol import BLEClient, ResponseResult
from bleak import BleakError
from bleak.backends.device import BLEDevice

from .wire import Command, Frame, FrameError, pop_frame

SERVICE_UUID = "98bd0001-0b0e-421a-84e5-ddbf75dc6de4"
_LOGIN = Command(4664, 4)
_KEEP_ALIVE = Command(4674, 2)
_GET_MAIN_NODE_VERSION = Command(96, 0)
_RESPONSE_TIMEOUT = 12
_GATT_WRITE_SIZE = 17


class MowerConnectionError(Exception):
    """Communication with the mower failed."""


class CommandRejected(Exception):
    """The mower received a command and returned an error result."""

    def __init__(self, command: Command, result: int) -> None:
        self.command = command
        self.result = result
        try:
            name = ResponseResult(result).name
        except ValueError:
            name = str(result)
        super().__init__(f"{command.family}:{command.operation}: {name}")

    @property
    def unsupported(self) -> bool:
        """Return whether the mower does not implement the operation."""
        return self.result in (ResponseResult.INVALID_GROUP, ResponseResult.INVALID_ID)


class AutomowerClient(BLEClient):
    """Use the existing GATT pairing and channel negotiation with HCP framing."""

    def __init__(self, address: str, pin: str | None) -> None:
        super().__init__(randbelow(0x7FFFFFFF) + 1, address, int(pin) if pin else None)
        self._notification_buffer = bytearray()

    async def connect(self, device: BLEDevice) -> ResponseResult:
        """Pair through the supplied HA Bluetooth adapter and open a channel."""
        result = ResponseResult.UNKNOWN_ERROR
        try:
            result = await super().connect(device)
        except (AttributeError, BleakError, FrameError, TimeoutError) as err:
            raise MowerConnectionError(
                "Could not open an Automower BLE channel"
            ) from err
        finally:
            if result != ResponseResult.OK:
                await self.disconnect()
        return result

    async def disconnect(self) -> None:
        """Release the GATT client, including a partially established connection."""
        client, self.client = self.client, None
        try:
            if client is not None and client.is_connected:
                with suppress(BleakError, TimeoutError):
                    await client.disconnect()
        finally:
            self._notification_buffer.clear()
            while not self.queue.empty():
                self.queue.get_nowait()

    async def command(
        self,
        command: Command,
        payload: bytes = b"",
        *,
        timeout: float = _RESPONSE_TIMEOUT,
    ) -> bytes:
        """Return the response payload or raise with the mower's result code."""
        request = command.request(self.channel_id, payload)
        try:
            async with self.lock, asyncio.timeout(timeout):
                async for frame in self._read_reply(request.encode()):
                    reply = command.response(self.channel_id, frame)
                    if reply is None:
                        continue
                    result, data = reply
                    if result:
                        raise CommandRejected(command, result)
                    return data
        except (BleakError, FrameError, TimeoutError) as err:
            await self.disconnect()
            raise MowerConnectionError("HCP exchange failed") from err
        except asyncio.CancelledError:
            await self.disconnect()
            raise
        raise MowerConnectionError("The mower closed the HCP channel")

    async def keep_alive(self) -> None:
        """Keep the authenticated link available between polls."""
        await self.command(_KEEP_ALIVE)

    async def main_node_version(self) -> str:
        """Read the linked node version used by Automower Connect to select a TIF."""
        payload = await self.command(_GET_MAIN_NODE_VERSION, timeout=4)
        return payload.partition(b"\0")[0].decode("ascii")

    async def _request_response(self, request_data: bytearray) -> bytes:
        """Read the response to the library's channel setup and login frames."""
        request = Frame.decode(bytes(request_data))
        async with self.lock, asyncio.timeout(_RESPONSE_TIMEOUT):
            async for frame in self._read_reply(bytes(request_data)):
                if frame.channel != request.channel or frame.linked != request.linked:
                    continue
                if frame.linked and _LOGIN.response(request.channel, frame) is None:
                    continue
                if not frame.linked and frame.payload[:1] != bytes(
                    (request.payload[0] + 1,)
                ):
                    continue
                return frame.encode()
        raise FrameError("The mower closed the HCP channel during setup")

    def get_response_result(self, response_data: bytes) -> ResponseResult:
        """Read the result of the operator PIN command during library setup."""
        response = _LOGIN.response(self.channel_id, Frame.decode(response_data))
        if response is None:
            raise FrameError("Missing operator login response")
        try:
            return ResponseResult(response[0])
        except ValueError as err:
            raise FrameError("Unknown operator login result") from err

    async def _read_reply(self, request: bytes) -> AsyncIterator[Frame]:
        """Write one request and yield the reassembled frames until it is answered."""
        client = self.client
        if client is None or not client.is_connected:
            raise BleakError("The mower GATT client is disconnected")
        for offset in range(0, len(request), _GATT_WRITE_SIZE):
            await client.write_gatt_char(
                self.write_char,
                request[offset : offset + _GATT_WRITE_SIZE],
                response=False,
            )
        while client.is_connected:
            if (frame := pop_frame(self._notification_buffer)) is not None:
                yield frame
            elif (chunk := await self.queue.get()) is None:
                return
            else:
                self._notification_buffer.extend(chunk)
