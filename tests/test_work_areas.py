"""Verify indexed work areas and Unicode names over BLE."""

from unittest.mock import AsyncMock

import pytest

from custom_components.automower_ble_plus.features import U32
from custom_components.automower_ble_plus.transport import CommandRejected
from custom_components.automower_ble_plus.work_areas import async_get_work_areas


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
