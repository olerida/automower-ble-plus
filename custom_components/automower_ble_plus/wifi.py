"""Read and configure the mower's Wi-Fi interface using its BLE session."""

import asyncio
from dataclasses import dataclass, field
from struct import Struct

from .features import U8, U32, Reading
from .transport import AutomowerClient
from .wire import Command, Event

WIFI_FAMILY = 5916
WIFI_AVAILABLE = Reading(Command(WIFI_FAMILY, 0), U8)
WIFI_ENABLED = Reading(Command(WIFI_FAMILY, 2), U8)
SET_WIFI_ENABLED = Command(WIFI_FAMILY, 3)
_GET_SAVED_NETWORK = Command(WIFI_FAMILY, 5)
_GET_SAVED_COUNT = Reading(Command(WIFI_FAMILY, 7), U32)
TRY_ADD_WIFI = Command(WIFI_FAMILY, 11)
TRY_SAVED_WIFI = Command(WIFI_FAMILY, 13)
REMOVE_WIFI = Command(WIFI_FAMILY, 15)
_WIFI_CONNECTED = Reading(Command(WIFI_FAMILY, 17), U8)
_CURRENT_WIFI = Command(WIFI_FAMILY, 18)
_WIFI_RSSI = Reading(Command(WIFI_FAMILY, 20), Struct("<b"))
START_WIFI_SCAN = Command(WIFI_FAMILY, 27)
STOP_WIFI_SCAN = Command(WIFI_FAMILY, 28)
WIFI_STATUS_EVENTS = Command(WIFI_FAMILY, 30)
WIFI_TIMEOUT = 90
WIFI_STATUS_KEYS = frozenset({"wifi_connected", "wifi_ssid", "wifi_signal_strength"})

_SSID_SIZE = 33
_PASSWORD_SIZE = 64
_SCAN_RESULT = Struct("<33sbBB")
_WIFI_OUTCOME = Struct("<33sB")
_CONNECTABLE_AUTH_MODES = frozenset({0, 1, 2, 3, 4, 6, 7})
_AUTH_NAMES = (
    "Open",
    "WEP",
    "WPA",
    "WPA2",
    "WPA/WPA2",
    "WPA2 Enterprise",
    "WPA3",
    "WPA2/WPA3",
)


def encode_ssid(name: str) -> bytes:
    """Encode a Wi-Fi network name as the mower's null-padded field."""
    raw = name.encode()
    if not 1 <= len(raw) < _SSID_SIZE or b"\0" in raw:
        raise ValueError("Invalid Wi-Fi network name")
    return raw.ljust(_SSID_SIZE, b"\0")


def encode_wifi_password(password: str) -> bytes:
    """Encode the user-supplied password without retaining it in configuration."""
    raw = password.encode()
    if len(raw) > _PASSWORD_SIZE or b"\0" in raw:
        raise ValueError("Invalid Wi-Fi password")
    return raw.ljust(_PASSWORD_SIZE, b"\0")


def _decode_ssid(raw: bytes) -> str:
    if len(raw) != _SSID_SIZE:
        raise ValueError("Unexpected Wi-Fi network name size")
    return raw.partition(b"\0")[0].decode("utf-8")


@dataclass(frozen=True, slots=True)
class WifiNetwork:
    """One connectable access point detected by the mower."""

    name: str
    signal: int
    auth: int

    @property
    def label(self) -> str:
        return f"{self.name} ({_AUTH_NAMES[self.auth]}, {self.signal} dBm)"


@dataclass(slots=True)
class WifiScan:
    """Collect scan results until the firmware signals completion."""

    done: asyncio.Event = field(default_factory=asyncio.Event)
    networks: dict[tuple[str, int], WifiNetwork] = field(default_factory=dict)

    def handle_event(self, event: Event) -> None:
        if self.done.is_set():
            return
        if event.event_id == 0 and not event.payload:
            self.done.set()
        elif event.event_id == 1 and len(event.payload) == _SCAN_RESULT.size:
            raw, signal, _, auth = _SCAN_RESULT.unpack(event.payload)
            try:
                name = _decode_ssid(raw)
                encode_ssid(name)
            except ValueError:
                return
            if auth in _CONNECTABLE_AUTH_MODES:
                key = (name, auth)
                if (
                    previous := self.networks.get(key)
                ) is None or signal > previous.signal:
                    self.networks[key] = WifiNetwork(name, signal, auth)


@dataclass(slots=True)
class WifiJoin:
    """Receive the connection result for the network the user selected."""

    ssid: bytes
    done: asyncio.Event = field(default_factory=asyncio.Event)
    connected: bool = False

    def handle_event(self, event: Event) -> None:
        if (
            self.done.is_set()
            or event.event_id not in (4, 6)
            or len(event.payload) != _WIFI_OUTCOME.size
        ):
            return
        raw, _ = _WIFI_OUTCOME.unpack(event.payload)
        if raw.partition(b"\0")[0] == self.ssid.partition(b"\0")[0]:
            self.connected = event.event_id == 4
            self.done.set()


async def async_read_saved_wifi(client: AutomowerClient) -> tuple[str, ...]:
    """List saved SSIDs without requesting any passwords."""
    count = _GET_SAVED_COUNT.decode(await client.command(_GET_SAVED_COUNT.command))
    if count > 128:
        raise ValueError("Unexpected number of saved Wi-Fi networks")
    names = []
    for index in range(count):
        raw = await client.command(_GET_SAVED_NETWORK, U32.pack(index))
        try:
            name = _decode_ssid(raw)
            encode_ssid(name)
        except ValueError:
            continue
        if name not in names:
            names.append(name)
    return tuple(names)


async def async_read_wifi_status(client: AutomowerClient) -> dict[str, int | str]:
    """Read the mower's active Wi-Fi network and its signal strength."""
    connected = _WIFI_CONNECTED.decode(await client.command(_WIFI_CONNECTED.command))
    if connected not in (0, 1):
        raise ValueError("Unexpected Wi-Fi connection state")
    data: dict[str, int | str] = {"wifi_connected": connected}
    if connected:
        name = _decode_ssid(await client.command(_CURRENT_WIFI))
        if name:
            data["wifi_ssid"] = name
        signal = _WIFI_RSSI.decode(await client.command(_WIFI_RSSI.command))
        if -127 <= signal < 0:
            data["wifi_signal_strength"] = signal
    return data
