"""Check that schedule updates use the mower's transaction and limits."""

from datetime import time

import pytest

from custom_components.automower_ble_plus.features import U32
from custom_components.automower_ble_plus.schedule import (
    ScheduleTask,
    async_get_schedule,
    async_replace_schedule,
)
from custom_components.automower_ble_plus.transport import CommandRejected


class FakeCalendar:
    def __init__(self, *, origin=0, fail_operation=None):
        self.origin = origin
        self.fail_operation = fail_operation
        self.disconnected = False
        self.committed = []
        self.staged = None

    async def command(self, command, payload=b""):
        operation = command.operation
        if operation == self.fail_operation:
            raise CommandRejected(command, 5)
        if operation == 4:
            return U32.pack(len(self.committed))
        if operation == 5:
            index = U32.unpack(payload)[0] - self.origin
            if not 0 <= index < len(self.committed):
                raise CommandRejected(command, 3)
            return self.committed[index]
        if operation == 14:
            return U32.pack(3)
        if operation == 15:
            return U32.pack(2)
        if operation == 10:
            self.staged = self.committed.copy()
        if operation == 9:
            self.staged.clear()
        if operation in (7, 16):
            self.staged.append(payload)
        if operation == 11:
            self.committed = self.staged
            self.staged = None
        return b""

    async def disconnect(self):
        self.disconnected = True
        self.staged = None


@pytest.mark.asyncio
async def test_schedule_round_trip_and_index_origin():
    client = FakeCalendar(origin=1)
    tasks = [
        ScheduleTask.from_action(time(9), 120, ["mon", "wed"]),
        ScheduleTask.from_action(time(15, 30), 40, ["sat"], 4),
    ]
    await async_replace_schedule(client, tasks)
    assert await async_get_schedule(client) == tasks


@pytest.mark.asyncio
async def test_rejected_update_closes_transaction():
    client = FakeCalendar(fail_operation=7)
    original = ScheduleTask.from_action(time(9), 120, ["mon"])
    client.committed = [original.encode()[1]]
    change = ScheduleTask.from_action(time(10), 120, ["mon"])
    with pytest.raises(CommandRejected):
        await async_replace_schedule(client, [change])
    assert client.disconnected
    assert await async_get_schedule(client) == [original]


@pytest.mark.asyncio
async def test_mower_schedule_limit_is_checked_before_any_change():
    client = FakeCalendar()
    task = ScheduleTask.from_action(time(9), 30, ["mon"])
    with pytest.raises(ValueError, match="intervals per day"):
        await async_replace_schedule(client, [task, task, task])
    assert client.staged is None


@pytest.mark.asyncio
async def test_external_calendar_edit_is_preserved_before_opening_transaction():
    client = FakeCalendar(fail_operation=10)
    original = ScheduleTask.from_action(time(9), 120, ["mon"])
    remote_change = ScheduleTask.from_action(time(10), 60, ["tue"])
    client.committed = [remote_change.encode()[1]]
    with pytest.raises(ValueError, match="changed before saving"):
        await async_replace_schedule(client, [original], expected_tasks=[original])
    assert await async_get_schedule(client) == [remote_change]
