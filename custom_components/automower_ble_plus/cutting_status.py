"""Read the mower's effective cutting height and TargetHeight progress."""

from dataclasses import dataclass
from struct import Struct

from automower_ble.protocol import ResponseResult

from .features import U8
from .transport import AutomowerClient, CommandRejected
from .wire import Command

_ACTIVE = Command(4422, 12)
_CURRENT_PERCENTAGE = Command(4422, 19)
_CURRENT_LEVEL = Command(4422, 33)
_AVAILABLE = Command(4422, 5)
_MINIMUM_MM = Command(4422, 15)
_MAXIMUM_MM = Command(4422, 17)
_GET_GLOBAL_MM = Command(4422, 28)
_SET_GLOBAL_MM = Command(4422, 29)
GLOBAL_HEIGHT_MM = "global_cutting_height_mm"
GLOBAL_HEIGHT_MM_WRITABLE = 2
_TARGET_STATUS = Struct("<BII")
_UNSET = (0, 0xFFFFFFFF)
CUTTING_STATUS_KEYS = frozenset(
    {
        "target_height_active",
        "target_height_start_seconds",
        "target_height_duration_seconds",
        "current_cutting_height_percentage",
        "current_cutting_height_level",
    }
)


@dataclass(frozen=True, slots=True)
class GlobalCuttingHeight:
    """The mower-wide target height and range in millimetres."""

    value: int
    minimum: int
    maximum: int


async def _async_read_optional(
    client: AutomowerClient, command: Command
) -> bytes | None:
    try:
        return await client.command(command)
    except CommandRejected as err:
        if err.unsupported or err.result == ResponseResult.NOT_AVAILABLE:
            return None
        raise


async def async_read_cutting_status(
    client: AutomowerClient, *, has_cutting_height: bool, has_target_height: bool
) -> dict[str, int]:
    """Read the effective height when available and validate TargetHeight status."""
    status: dict[str, int] = {}
    if has_cutting_height:
        raw = await _async_read_optional(client, _CURRENT_PERCENTAGE)
        if raw is not None:
            if len(raw) == U8.size and raw[0] <= 100:
                status["current_cutting_height_percentage"] = raw[0]
        else:
            raw = await _async_read_optional(client, _CURRENT_LEVEL)
            if raw is not None and len(raw) == U8.size and 1 <= raw[0] <= 9:
                status["current_cutting_height_level"] = raw[0]
    if has_target_height:
        raw = await _async_read_optional(client, _ACTIVE)
        if raw is not None and len(raw) == _TARGET_STATUS.size:
            active, start, duration = _TARGET_STATUS.unpack(raw)
            if active in (0, 1):
                status["target_height_active"] = active
            if active == 1:
                if start not in _UNSET:
                    status["target_height_start_seconds"] = start
                if duration not in _UNSET:
                    status["target_height_duration_seconds"] = duration
    return status


async def async_read_global_height(
    client: AutomowerClient,
) -> GlobalCuttingHeight | None:
    """Read the global height only when the mower confirms electric adjustment."""
    if await _async_read_optional(client, _AVAILABLE) != b"\x01":
        return None
    limits = []
    for command in (_MINIMUM_MM, _MAXIMUM_MM, _GET_GLOBAL_MM):
        raw = await _async_read_optional(client, command)
        if raw is None or len(raw) != U8.size:
            return None
        limits.append(raw[0])
    minimum, maximum, value = limits
    if 0 < minimum < maximum < 255 and minimum <= value <= maximum:
        return GlobalCuttingHeight(value, minimum, maximum)
    return None


async def async_write_global_height(
    client: AutomowerClient, value: int
) -> GlobalCuttingHeight:
    """Check the live range, write a target and return the confirmed reading."""
    current = await async_read_global_height(client)
    if current is None:
        raise ValueError("The mower did not report its cutting height range")
    if not current.minimum <= value <= current.maximum:
        raise ValueError(
            f"Cutting height must be between {current.minimum} and {current.maximum} mm"
        )
    if current.value == value:
        return current
    await client.command(_SET_GLOBAL_MM, U8.pack(value))
    actual = await async_read_global_height(client)
    if actual is None:
        raise ValueError("The mower did not confirm its new cutting height")
    return actual
