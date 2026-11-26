"""Verify intelligent mapping on firmware with and without scan progress."""

from struct import pack
from unittest.mock import AsyncMock

import pytest
from automower_ble.protocol import ResponseResult

from custom_components.automower_ble_plus.aim import (
    async_read_aim_status,
    is_aim_update,
)
from custom_components.automower_ble_plus.transport import CommandRejected
from custom_components.automower_ble_plus.wire import Command, Event


@pytest.mark.asyncio
@pytest.mark.parametrize("progress", [None, b"\xff", bytes((45,))])
async def test_aim_progress_is_optional(progress):
    updated = 1795719999
    client = AsyncMock()
    client.command.side_effect = [
        b"\1",
        b"\1",
        b"\1",
        progress
        if progress is not None
        else CommandRejected(Command(5758, 10), ResponseResult.INVALID_ID),
        pack("<I", updated),
    ]
    status = {
        "aim_enabled": 1,
        "aim_map_state": 1,
        "aim_map_updated": updated,
    }
    if progress == bytes((45,)):
        status["aim_map_progress"] = 45
    assert await async_read_aim_status(client) == status
    assert is_aim_update(Event(5758, 0, pack("<BI", 1, updated)))
    assert is_aim_update(Event(5758, 0, pack("<BIB", 1, updated, 45)))
    assert not is_aim_update(Event(5758, 0, b"\1"))
