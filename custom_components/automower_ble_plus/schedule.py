"""Read the Calendar schedules carried by the mower's BLE API."""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import time
from struct import Struct

from automower_ble.protocol import ResponseResult

from .features import U32
from .transport import AutomowerClient, CommandRejected
from .wire import Command

_COUNT = Command(4690, 4)
_TASK = Command(4690, 5)
_ADD = Command(4690, 7)
_CLEAR = Command(4690, 9)
_BEGIN = Command(4690, 10)
_COMMIT = Command(4690, 11)
_MAX_TASKS = Command(4690, 14)
_MAX_PER_DAY = Command(4690, 15)
_ADD_WITH_MISSION = Command(4690, 16)
_BASIC_TASK = Struct("<II7B")
_MISSION_TASK = Struct("<II7BI")
_SECONDS_PER_DAY = 86400
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@dataclass(frozen=True, slots=True)
class ScheduleTask:
    """One recurring mower-local time interval."""

    start: int
    duration: int
    days: tuple[int, ...]
    mission_id: int = 0

    @classmethod
    def decode(cls, packet: bytes) -> "ScheduleTask":
        if len(packet) == _MISSION_TASK.size:
            start, duration, *days, mission = _MISSION_TASK.unpack(packet)
            return cls(start, duration, tuple(days), mission)
        start, duration, *days = _BASIC_TASK.unpack(packet)
        return cls(start, duration, tuple(days))

    @classmethod
    def from_action(
        cls,
        start: time,
        duration_minutes: int,
        weekdays: Sequence[str],
        mission_id: int = 0,
    ) -> "ScheduleTask":
        return cls(
            (start.hour * 60 + start.minute) * 60 + start.second,
            duration_minutes * 60,
            tuple(int(day in weekdays) for day in WEEKDAYS),
            mission_id,
        )

    def as_response(self) -> dict:
        hour, remainder = divmod(self.start, 3600)
        minute, second = divmod(remainder, 60)
        return {
            "start": f"{hour:02}:{minute:02}:{second:02}",
            "duration_minutes": self.duration / 60,
            "weekdays": [
                day for day, enabled in zip(WEEKDAYS, self.days, strict=True) if enabled
            ],
            "mission_id": self.mission_id,
        }

    def encode(self) -> tuple[Command, bytes]:
        if self.mission_id:
            return _ADD_WITH_MISSION, _MISSION_TASK.pack(
                self.start, self.duration, *self.days, self.mission_id
            )
        return _ADD, _BASIC_TASK.pack(self.start, self.duration, *self.days)


async def async_get_schedule(client: AutomowerClient) -> list[ScheduleTask]:
    """Read all intervals, including either index origin used by mower firmware."""
    count = U32.unpack(await client.command(_COUNT))[0]
    if count == 0:
        return []
    maximum, _ = await async_get_schedule_limits(client)
    if count > maximum:
        raise ValueError("Mower returned a schedule count above its reported maximum")
    try:
        first = await client.command(_TASK, U32.pack(0))
    except CommandRejected as err:
        if err.result not in (
            ResponseResult.INVALID_ID,
            ResponseResult.OUT_OF_RANGE,
            ResponseResult.INVALID_VALUE,
        ):
            raise
        first_index = 1
        first = await client.command(_TASK, U32.pack(first_index))
    else:
        first_index = 0
    tasks = [ScheduleTask.decode(first)]
    for index in range(first_index + 1, first_index + count):
        tasks.append(ScheduleTask.decode(await client.command(_TASK, U32.pack(index))))
    return tasks


async def async_get_schedule_limits(client: AutomowerClient) -> tuple[int, int]:
    """Read the limits for this model and firmware."""
    maximum = U32.unpack(await client.command(_MAX_TASKS))[0]
    per_day = U32.unpack(await client.command(_MAX_PER_DAY))[0]
    return maximum, per_day


async def async_replace_schedule(
    client: AutomowerClient, tasks: Sequence[ScheduleTask]
) -> None:
    """Write a complete schedule in the mower's Calendar transaction."""
    maximum, per_day = await async_get_schedule_limits(client)
    if len(tasks) > maximum:
        raise ValueError(f"This mower supports at most {maximum} schedule intervals")
    if any(
        sum(task.days[day] for task in tasks) > per_day for day in range(len(WEEKDAYS))
    ):
        raise ValueError(
            f"This mower supports at most {per_day} schedule intervals per day"
        )
    if any(
        not 0 <= task.start < _SECONDS_PER_DAY
        or not 0 < task.duration <= _SECONDS_PER_DAY
        or not any(task.days)
        for task in tasks
    ):
        raise ValueError(
            "Schedule intervals require a valid start, duration and weekdays"
        )
    # Serialize the complete batch before opening the transaction.
    commands = [task.encode() for task in tasks]
    await client.command(_BEGIN)
    committed = False
    try:
        await client.command(_CLEAR)
        for command, payload in commands:
            await client.command(command, payload)
        await client.command(_COMMIT)
        committed = True
    finally:
        if not committed:
            await client.disconnect()
    actual = await async_get_schedule(client)
    if Counter(actual) != Counter(tasks):
        raise ValueError("The readback differs from the schedule sent to the mower")
