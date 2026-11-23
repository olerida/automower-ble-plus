"""Verify indexed work areas and Unicode names over BLE."""

from unittest.mock import AsyncMock

import pytest

from custom_components.automower_ble_plus.features import U32
from custom_components.automower_ble_plus.transport import CommandRejected
from custom_components.automower_ble_plus.wire import Command
from custom_components.automower_ble_plus.work_areas import (
    async_get_work_areas,
    async_update_work_area,
)


@pytest.mark.asyncio
async def test_unavailable_work_areas_do_not_read_list():
    client = AsyncMock()
    client.command.return_value = b"\x00"
    assert await async_get_work_areas(client, 0, 10) == {
        "total": 0,
        "offset": 0,
        "work_areas": [],
    }
    assert client.command.await_count == 1


@pytest.mark.asyncio
async def test_one_based_area_and_utf16_name():
    async def reply(command, payload=b""):
        if command.operation == 20:
            return b"\x01"
        if command.operation == 2:
            return U32.pack(1)
        if command.operation == 3:
            if U32.unpack(payload)[0] == 0:
                raise CommandRejected(command, 3)
            return U32.pack(12) + "Área árbol\0".encode("utf-16-le")
        return b"\x01"

    client = AsyncMock()
    client.command.side_effect = reply
    assert (await async_get_work_areas(client, 0, 1))["work_areas"] == [
        {
            "id": 12,
            "name": "Área árbol",
            "type": 1,
            "enabled": True,
            "schedulable": True,
        }
    ]


@pytest.mark.asyncio
async def test_area_name_is_written_and_verified():
    client = AsyncMock()
    old = "Antes\0".encode("utf-16-le")
    new = "Área árbol\0".encode("utf-16-le")
    client.command.side_effect = [b"\x01", old, b"", new]
    await async_update_work_area(client, 12, name="Área árbol", enabled=None)
    client.command.assert_any_await(Command(5604, 8), U32.pack(12) + new)
    assert client.command.await_count == 4


@pytest.mark.asyncio
async def test_area_setting_not_supported_by_mower_is_never_written():
    client = AsyncMock()
    client.command.side_effect = [b"\x01", CommandRejected(Command(5604, 31), 7)]
    with pytest.raises(CommandRejected):
        await async_update_work_area(client, 12, name=None, enabled=False)
    assert [call.args[0].operation for call in client.command.await_args_list] == [
        20,
        31,
    ]
