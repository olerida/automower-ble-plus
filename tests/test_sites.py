"""Check listing and editing of mower installation profiles."""

from unittest.mock import AsyncMock

import pytest

from custom_components.automower_ble_plus.features import U32
from custom_components.automower_ble_plus.sites import (
    async_get_installations,
    async_rename_installation,
)
from custom_components.automower_ble_plus.transport import CommandRejected
from custom_components.automower_ble_plus.wire import Command


@pytest.mark.asyncio
async def test_installation_page_with_one_based_index_and_active_site():
    client = AsyncMock()

    async def reply(command, payload=b""):
        match (command.family, command.operation):
            case (5900, 17) | (5578, 40):
                return b"\x01"
            case (5900, 1):
                return U32.pack(2)
            case (5900, 12):
                return U32.pack(12)
            case (5900, 3):
                index = U32.unpack(payload)[0]
                if index == 0:
                    raise CommandRejected(command, 3)
                assert index == 2
                return U32.pack(12) + "Jardín\0".encode("utf-16-le")
        raise CommandRejected(command, 7)

    client.command.side_effect = reply
    assert await async_get_installations(client, 1, 1) == {
        "available": True,
        "total": 2,
        "offset": 1,
        "active_id": 12,
        "site_map_available": True,
        "installations": [{"id": 12, "name": "Jardín", "active": True}],
    }


@pytest.mark.asyncio
async def test_rename_changes_only_the_confirmed_active_installation():
    client = AsyncMock()
    old = "Antes\0".encode("utf-16-le")
    new = "Jardín\0".encode("utf-16-le")
    client.command.side_effect = [b"\x01", U32.pack(11)]
    with pytest.raises(ValueError, match="not active"):
        await async_rename_installation(client, 12, "Jardín")
    assert [call.args[0] for call in client.command.await_args_list] == [
        Command(5900, 17),
        Command(5900, 12),
    ]

    client.command.reset_mock()
    client.command.side_effect = [b"\x01", U32.pack(12), old, b"", new, U32.pack(12)]
    await async_rename_installation(client, 12, "Jardín")
    client.command.assert_any_await(Command(5896, 1), new)
