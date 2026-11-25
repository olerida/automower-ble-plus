"""Read and synchronize the mower's local calendar clock."""

from datetime import UTC, datetime
from time import monotonic
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .features import I32, U32, Reading
from .transport import AutomowerClient, CommandRejected
from .wire import Command

MOWER_CLOCK = Reading(Command(4690, 2), U32)
_SET_TIME = Command(4690, 3)
_GET_TIME_ZONE = Command(6746, 1)
_SET_TIME_ZONE = Command(6746, 2)
_GET_OFFSET = Reading(Command(6746, 3), I32)
_SET_OFFSET = Command(6746, 4)
_GET_AUTOMATIC_SYNC = Command(6746, 7)
_SYNC_THRESHOLD = 60


async def _read_time_zone(client: AutomowerClient) -> tuple[str, int] | None:
    try:
        payload = await client.command(_GET_TIME_ZONE)
    except CommandRejected as err:
        if err.unsupported:
            return None
        raise
    name, _, padding = payload.partition(b"\0")
    if padding.strip(b"\0") or not 1 <= len(payload) <= 40:
        raise ValueError("The mower returned an invalid time zone")
    return name.decode("ascii"), len(payload)


async def async_sync_mower_clock(
    client: AutomowerClient,
    zone_name: str,
    *,
    automatic: bool = False,
    current: int | None = None,
) -> int:
    """Set the mower's local time using its configured zone or Home Assistant's."""
    # All known Calendar profiles pair GetTime with SetTime. Confirm the read first.
    if current is None:
        current = MOWER_CLOCK.decode(await client.command(MOWER_CLOCK.command))
    if automatic and await client.command(_GET_AUTOMATIC_SYNC) != b"\x01":
        return current
    time_zone = await _read_time_zone(client)
    configured_zone = time_zone[0] if time_zone else ""
    if automatic and not configured_zone:
        return current
    try:
        zone = ZoneInfo(configured_zone or zone_name)
    except ZoneInfoNotFoundError as err:
        raise ValueError(f"Unknown time zone: {configured_zone or zone_name}") from err
    new_zone = None
    previous_offset = None
    if time_zone is not None:
        if not configured_zone:
            new_zone = zone_name.encode("ascii") + b"\0"
            if len(new_zone) > 40 or time_zone[1] not in (1, 40):
                raise ValueError(
                    "The mower did not report a supported time zone format"
                )
            new_zone = new_zone.ljust(time_zone[1], b"\0")
        previous_offset = _GET_OFFSET.decode(await client.command(_GET_OFFSET.command))

    now = datetime.now(UTC).astimezone(zone)
    offset = now.utcoffset()
    if offset is None:
        raise ValueError("The time zone did not provide a UTC offset")
    offset_seconds = int(offset.total_seconds())
    sent_time = int(now.timestamp()) + offset_seconds
    if not 0 <= sent_time < 2**32:
        raise ValueError("The current time falls outside the mower's clock range")
    if (
        automatic
        and previous_offset == offset_seconds
        and abs(current - sent_time) <= _SYNC_THRESHOLD
    ):
        return current
    if new_zone is not None:
        await client.command(_SET_TIME_ZONE, new_zone)
        confirmed_zone = await _read_time_zone(client)
        if confirmed_zone is None or confirmed_zone[0] != zone_name:
            raise ValueError("The mower did not store the time zone")
    if previous_offset is not None and previous_offset != offset_seconds:
        await client.command(_SET_OFFSET, I32.pack(offset_seconds))
        if (
            _GET_OFFSET.decode(await client.command(_GET_OFFSET.command))
            != offset_seconds
        ):
            raise ValueError("The mower did not store the UTC offset")

    started = monotonic()
    await client.command(_SET_TIME, U32.pack(sent_time))
    actual = MOWER_CLOCK.decode(await client.command(MOWER_CLOCK.command))
    if abs(actual - sent_time) > monotonic() - started + 2:
        raise ValueError("The mower did not confirm the new local time")
    return actual
