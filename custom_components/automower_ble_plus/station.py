"""Read the physical loop installation from the mower over BLE."""

from struct import Struct

from automower_ble.protocol import MowerActivity

from .features import GET_ACTIVITY, U8
from .transport import AutomowerClient, CommandRejected
from .wire import Command

_INSTALLED_LOOPS = Command(4462, 29)
_LOOP_SIGNALS = Command(4462, 13)
_SIGNAL_QUALITY = Command(4462, 14)
_LOOPS = ("A", "F", "N", "G1", "G2", "G3")
_INSTALLED = Struct("<6B")
_SIGNALS = Struct("<6h")
_CHARGING_STATION = 4692
_IN_CHARGING_STATION = Command(_CHARGING_STATION, 27)
_PAIR_CHARGING_STATION = Command(_CHARGING_STATION, 2)
_PAIRING_CHANNEL = b"\x01"
_PAIRING_FAILED = 1
_PAIRING_SUCCESS = 2


async def async_get_loop_signals(
    client: AutomowerClient, sensor_positions: str
) -> dict:
    """Measure the installed loops at each supported sensor position."""
    if not sensor_positions:
        return {"available": False}
    try:
        flags = _INSTALLED.unpack(await client.command(_INSTALLED_LOOPS))
    except CommandRejected as err:
        if err.unsupported:
            return {"available": False}
        raise

    installed = [name for name, flag in zip(_LOOPS, flags, strict=True) if flag == 1]
    sensors = {}
    if installed:
        for index, position in enumerate(sensor_positions.split()):
            argument = U8.pack(index)
            try:
                signals = _SIGNALS.unpack(await client.command(_LOOP_SIGNALS, argument))
            except CommandRejected as err:
                if err.unsupported:
                    continue
                raise
            reading = {
                "signals": {
                    name: level
                    for name, level in zip(_LOOPS, signals, strict=True)
                    if name in installed
                }
            }
            try:
                quality = U8.unpack(await client.command(_SIGNAL_QUALITY, argument))[0]
            except CommandRejected as err:
                if not err.unsupported:
                    raise
            else:
                if quality <= 100:
                    reading["signal_quality"] = quality
            sensors[position] = reading
    return {"available": True, "installed_loops": installed, "sensors": sensors}


async def async_pair_charging_station(
    client: AutomowerClient, subscription: int
) -> None:
    """Create a new loop signal and wait for the mower's pairing result."""
    try:
        docked = U8.unpack(await client.command(_IN_CHARGING_STATION))[0] == 1
    except CommandRejected as err:
        if not err.unsupported:
            raise
        activity = U8.unpack(await client.command(GET_ACTIVITY))[0]
        docked = activity in (MowerActivity.PARKED, MowerActivity.CHARGING)
    if not docked:
        raise ValueError("Put the mower in its charging station before pairing")

    await client.command(Command(_CHARGING_STATION, subscription), _PAIRING_CHANNEL)
    outcome = await client.command_and_wait_for_event(
        _PAIR_CHARGING_STATION, (_PAIRING_FAILED, _PAIRING_SUCCESS), timeout=180
    )
    if outcome.event_id != _PAIRING_SUCCESS or outcome.payload:
        raise ValueError("The mower did not confirm successful station pairing")
