"""Read the mower's Automower Connect module over Bluetooth."""

from struct import Struct
from struct import error as StructError

from automower_ble.protocol import ResponseResult

from .features import CONNECT_ENABLED_KEY, CONNECT_FAMILY, U8, Reading
from .transport import AutomowerClient, CommandRejected
from .wire import Command, Event

CONNECT_EVENTS = (Command(CONNECT_FAMILY, 12), Command(CONNECT_FAMILY, 13))
CONNECT_STATE_KEY = "automower_connect_state"
CONNECT_SIGNAL_KEY = "automower_connect_signal_strength"
CONNECT_STATUS_KEYS = frozenset(
    {CONNECT_ENABLED_KEY, CONNECT_STATE_KEY, CONNECT_SIGNAL_KEY}
)
CONNECT_STATE_OPTIONS = (
    "not_applicable",
    "connected",
    "disconnected",
    "sim_card_error",
    "no_sim_card",
)

CONNECT_AVAILABLE = Reading(Command(CONNECT_FAMILY, 0), U8)
REMOVE_CONNECT_PAIRINGS = Command(CONNECT_FAMILY, 4)
_ENABLED = Reading(Command(CONNECT_FAMILY, 2), U8)
_STATE = Reading(Command(CONNECT_FAMILY, 7), U8)
_SIGNAL = Reading(Command(CONNECT_FAMILY, 8), U8)
_GET_STATUS = Command(CONNECT_FAMILY, 10)
_STATUS = Struct("<BB")


def is_connect_update(event: Event) -> bool:
    """Return whether a status or settings notification has a known wire layout."""
    return (event.event_id == 2 and len(event.payload) == 2) or (
        event.event_id == 3 and len(event.payload) in (2, 3)
    )


async def _async_read(client: AutomowerClient, reading: Reading) -> int | None:
    try:
        return reading.decode(await client.command(reading.command))
    except CommandRejected as err:
        if not err.unsupported and err.result != ResponseResult.NOT_AVAILABLE:
            raise
    except ValueError:
        pass
    return None


async def async_read_connect_module(client: AutomowerClient) -> dict[str, int]:
    """Detect the module before reading enabled state, connection and signal."""
    if await _async_read(client, CONNECT_AVAILABLE) != 1:
        return {}
    enabled = await _async_read(client, _ENABLED)
    try:
        state, signal = _STATUS.unpack(await client.command(_GET_STATUS))
    except CommandRejected as err:
        if not err.unsupported and err.result != ResponseResult.NOT_AVAILABLE:
            raise
        state = signal = None
    except StructError:
        state = signal = None
    if state is None:
        state = await _async_read(client, _STATE)
    if signal is None:
        signal = await _async_read(client, _SIGNAL)
    result = {}
    if enabled in (0, 1):
        result[CONNECT_ENABLED_KEY] = enabled
    if state is not None and state < len(CONNECT_STATE_OPTIONS):
        result[CONNECT_STATE_KEY] = state
    if signal is not None and signal <= 100:
        result[CONNECT_SIGNAL_KEY] = signal
    return result
