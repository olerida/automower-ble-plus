"""Check listing and editing of mower installation profiles."""

from unittest.mock import AsyncMock, call

import pytest

from custom_components.automower_ble_plus.features import U32
from custom_components.automower_ble_plus.sites import (
    async_activate_installation,
    async_create_installation,
    async_delete_installation,
    async_get_installations,
    async_rename_installation,
)
from custom_components.automower_ble_plus.transport import CommandRejected
from custom_components.automower_ble_plus.wire import Command, Event


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


@pytest.mark.asyncio
async def test_activation_checks_the_current_and_target_installations():
    client = AsyncMock()
    client.command.side_effect = [b"\x01", U32.pack(7)]
    with pytest.raises(ValueError, match="active installation changed"):
        await async_activate_installation(client, 12, 8)
    assert client.command.await_args_list == [
        call(Command(5900, 17)),
        call(Command(5900, 12)),
    ]

    client.command.reset_mock()
    client.command.side_effect = [
        b"\x01",
        U32.pack(7),
        "Jardín\0".encode("utf-16-le"),
        b"",
        U32.pack(12),
    ]
    await async_activate_installation(client, 12, 7)
    assert client.command.await_args_list[2:] == [
        call(Command(5900, 5), U32.pack(12)),
        call(Command(5900, 14), U32.pack(12)),
        call(Command(5900, 12)),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(("copy_current", "operation"), [(False, 24), (True, 4)])
async def test_creation_returns_the_assigned_identifier(copy_current, operation):
    client = AsyncMock()
    responses = [b"\x01"]
    if copy_current:
        responses.append(U32.pack(2))
    client.command.side_effect = [*responses, U32.pack(7)]
    assert await async_create_installation(client, copy_current) == {"site_id": 7}
    client.command.assert_awaited_with(Command(5900, operation))


@pytest.mark.asyncio
async def test_deletion_requires_an_inactive_installation_with_the_expected_name():
    client = AsyncMock()
    client.command.side_effect = [b"\x01", U32.pack(7)]
    with pytest.raises(ValueError, match="Switch to another installation"):
        await async_delete_installation(client, 7, "Jardín")
    client.command_and_wait_for_event.assert_not_awaited()

    client.command.side_effect = [b"\x01", U32.pack(3), "Otro\0".encode("utf-16-le")]
    with pytest.raises(ValueError, match="name has changed"):
        await async_delete_installation(client, 7, "Jardín")
    client.command_and_wait_for_event.assert_not_awaited()

    client.command.side_effect = [
        b"\x01",
        U32.pack(3),
        "Jardín\0".encode("utf-16-le"),
        b"",
    ]
    client.command_and_wait_for_event.return_value = Event(5900, 1, U32.pack(7))
    await async_delete_installation(client, 7, "Jardín")
    client.command.assert_awaited_with(Command(5900, 0))
    client.command_and_wait_for_event.assert_awaited_once_with(
        Command(5900, 15), (1,), U32.pack(7), timeout=15
    )
