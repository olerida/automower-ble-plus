"""Transport for authenticated Automower HCP commands over BLE."""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from secrets import randbelow

from automower_ble.protocol import BLEClient, ResponseResult
from bleak import BleakError
from bleak.backends.device import BLEDevice

from .wire import Command, Event, Frame, FrameError, pop_frame

SERVICE_UUID = "98bd0001-0b0e-421a-84e5-ddbf75dc6de4"
_LOGIN = Command(4664, 4)
_KEEP_ALIVE = Command(4674, 2)
_GET_MAIN_NODE_VERSION = Command(96, 0)
_RESPONSE_TIMEOUT = 12
_GATT_WRITE_SIZE = 17
_LOGGER = logging.getLogger(__name__)


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
        self._reader: asyncio.Task[None] | None = None
        self._pending: asyncio.Queue[Frame | Exception] | None = None
        self._expected_events: tuple[int, tuple[int, ...]] | None = None
        self.event_callback: Callable[[Event], None] | None = None

    async def connect(self, device: BLEDevice) -> ResponseResult:
        """Pair through the supplied HA Bluetooth adapter and open a channel."""
        result = ResponseResult.UNKNOWN_ERROR
        try:
            if self.client is not None:
                await self.disconnect()
            result = await super().connect(device)
        except (AttributeError, BleakError, FrameError, TimeoutError) as err:
            raise MowerConnectionError(
                "Could not open an Automower BLE channel"
            ) from err
        finally:
            if result != ResponseResult.OK:
                await self.disconnect()
        if result == ResponseResult.OK:
            self._reader = asyncio.create_task(self._read_notifications())
        return result

    async def disconnect(self) -> None:
        """Release the GATT client, including a partially established connection."""
        reader, self._reader = self._reader, None
        client, self.client = self.client, None
        try:
            if reader is not None:
                reader.cancel()
                with suppress(asyncio.CancelledError):
                    await reader
            if client is not None and client.is_connected:
                with suppress(BleakError, TimeoutError):
                    await client.disconnect()
        finally:
            self._notification_buffer.clear()
            while not self.queue.empty():
                self.queue.get_nowait()

    def is_connected(self) -> bool:
        """Return whether the authenticated connection can receive replies."""
        return (
            super().is_connected()
            and self._reader is not None
            and not self._reader.done()
        )

    async def command(
        self,
        command: Command,
        payload: bytes = b"",
        *,
        timeout: float = _RESPONSE_TIMEOUT,
    ) -> bytes:
        """Return the response payload or raise with the mower's result code."""
        response, _ = await self._exchange(command, payload, timeout, ())
        return response

    async def command_and_wait_for_event(
        self,
        command: Command,
        event_ids: tuple[int, ...],
        payload: bytes = b"",
        *,
        timeout: float,
    ) -> Event:
        """Send a command and wait for both its response and its result event."""
        _, event = await self._exchange(command, payload, timeout, event_ids)
        if event is None:
            raise MowerConnectionError("The mower did not report the command outcome")
        return event

    async def _exchange(
        self,
        command: Command,
        payload: bytes,
        timeout: float,
        event_ids: tuple[int, ...],
    ) -> tuple[bytes, Event | None]:
        request = command.request(self.channel_id, payload)
        response = None
        event = None
        try:
            async with self.lock, asyncio.timeout(timeout):
                if not self.is_connected():
                    raise MowerConnectionError("The mower closed the HCP channel")
                pending: asyncio.Queue[Frame | Exception] = asyncio.Queue()
                self._pending = pending
                self._expected_events = (
                    (command.family, event_ids) if event_ids else None
                )
                try:
                    await self._write_request(request.encode())
                    while response is None or (event_ids and event is None):
                        received = await pending.get()
                        if isinstance(received, Exception):
                            raise received
                        reply = command.response(self.channel_id, received)
                        if reply is None:
                            event = Event.decode(received)
                        else:
                            result, response = reply
                            if result:
                                raise CommandRejected(command, result)
                finally:
                    self._pending = self._expected_events = None
        except (BleakError, FrameError, MowerConnectionError, TimeoutError) as err:
            await self.disconnect()
            if event_ids and response is not None and event is None:
                raise MowerConnectionError(
                    "The mower accepted the command, but its result event was not received"
                ) from err
            raise MowerConnectionError("HCP exchange failed") from err
        except asyncio.CancelledError:
            await self.disconnect()
            raise
        return response, event

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
            await self._write_request(bytes(request_data))
            async for frame in self._read_frames():
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

    async def _write_request(self, request: bytes) -> None:
        client = self.client
        if client is None or not client.is_connected:
            raise BleakError("The mower GATT client is disconnected")
        for offset in range(0, len(request), _GATT_WRITE_SIZE):
            await client.write_gatt_char(
                self.write_char,
                request[offset : offset + _GATT_WRITE_SIZE],
                response=False,
            )

    async def _read_frames(self) -> AsyncIterator[Frame]:
        """Reassemble frames from the BLE notifications."""
        client = self.client
        if client is None:
            return
        while client.is_connected:
            if (frame := pop_frame(self._notification_buffer)) is not None:
                yield frame
            elif (chunk := await self.queue.get()) is None:
                return
            else:
                self._notification_buffer.extend(chunk)

    async def _read_notifications(self) -> None:
        """Dispatch unsolicited events separately from in-flight command replies."""
        failure = MowerConnectionError("The mower closed the HCP channel")
        try:
            async for frame in self._read_frames():
                if (
                    frame.linked
                    and frame.channel == self.channel_id
                    and frame.payload.startswith(b"\x02")
                ):
                    try:
                        event = Event.decode(frame)
                    except FrameError:
                        _LOGGER.debug("Ignoring malformed mower event")
                        continue
                    if self.event_callback is not None:
                        try:
                            self.event_callback(event)
                        except Exception:
                            _LOGGER.exception("Mower event callback failed")
                    if self._pending is not None and self._expected_events is not None:
                        family, event_ids = self._expected_events
                        if event.family == family and event.event_id in event_ids:
                            self._pending.put_nowait(frame)
                elif self._pending is not None:
                    self._pending.put_nowait(frame)
        except FrameError as err:
            failure = err
        finally:
            if self._pending is not None:
                self._pending.put_nowait(failure)
