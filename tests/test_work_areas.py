"""Verify indexed work areas and Unicode names over BLE."""

from unittest.mock import AsyncMock

import pytest

from custom_components.automower_ble_plus.features import I16, U32
from custom_components.automower_ble_plus.transport import CommandRejected
from custom_components.automower_ble_plus.wire import Command
from custom_components.automower_ble_plus.work_areas import (
    async_get_area_mowing_settings,
    async_get_work_areas,
    async_set_area_mowing_setting,
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


@pytest.mark.asyncio
async def test_mowing_settings_include_only_successfully_read_area_controls():
    client = AsyncMock()
    identity = U32.pack(12)
    responses = {
        (Command(5604, 20), b""): b"\x01",
        (Command(5604, 13), identity): b"\x02",
        (Command(5604, 9), identity): b"\x05",
        (Command(5604, 23), identity): b"\0",
        (Command(5582, 0), b""): b"\x01",
        (Command(5582, 3), identity): I16.pack(-455),
        (Command(5582, 7), identity): I16.pack(905),
    }

    async def reply(command, payload=b""):
        if (command, payload) not in responses:
            raise CommandRejected(command, 7)
        return responses[command, payload]

    client.command.side_effect = reply
    result = await async_get_area_mowing_settings(client, 12, height_max=9)
    assert result["mission_id"] == 12
    assert result["settings"] == {
        "cutting_height": 5,
        "cutting_height_unit": "level",
        "use_global_cutting_height": False,
        "mowing_pattern": "systematic",
        "orientation": -45.5,
        "current_orientation": 90.5,
    }
    assert set(result["editable"]) == {
        "cutting_height",
        "use_global_cutting_height",
        "mowing_pattern",
        "orientation",
    }


@pytest.mark.asyncio
async def test_orientation_write_uses_signed_tenths_and_checks_the_result():
    client = AsyncMock()
    client.command.side_effect = [
        b"\x01",
        b"\x02",
        b"\x01",
        I16.pack(200),
        b"",
        I16.pack(-125),
    ]
    await async_set_area_mowing_setting(client, 12, 0, {"orientation": -12.5})
    identity = U32.pack(12)
    client.command.assert_any_await(Command(5582, 4), identity + I16.pack(-125))
    client.command.assert_awaited_with(Command(5582, 3), identity)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("height_max", "change", "responses", "message"),
    [
        (0, {"boundary_mowing": True}, [b"\x01", b"\x01", b"\x01"], "does not support"),
        (9, {"cutting_height": 40}, [b"\x01", b"\x01", b"\x00"], "firmware range"),
    ],
)
async def test_mowing_settings_reject_inapplicable_values_before_the_setter(
    height_max, change, responses, message
):
    client = AsyncMock()
    client.command.side_effect = responses
    with pytest.raises(ValueError, match=message):
        await async_set_area_mowing_setting(client, 12, height_max, change)
    assert [call.args[0] for call in client.command.await_args_list] == [
        Command(5604, 20),
        Command(5604, 13),
        Command(5582, 0),
    ]


@pytest.mark.asyncio
async def test_area_assistance_uses_available_hardware_and_a_specific_mission():
    client = AsyncMock()
    identity = U32.pack(12)
    daylight = Command(5604, 50)
    responses = {
        (Command(5604, 20), b""): b"\x01",
        (Command(5604, 13), identity): b"\x01",
        (Command(5472, 2), b""): b"\0",
        (Command(6170, 14), b""): b"\0",
        (Command(7338, 2), b""): b"\x01",
        (daylight, identity): b"\0",
        (Command(5604, 58), identity): U32.pack(2),
    }

    async def reply(command, payload=b""):
        if command == Command(5604, 49):
            assert payload == identity + b"\x01"
            responses[daylight, identity] = b"\x01"
            return b""
        if (command, payload) not in responses:
            raise CommandRejected(command, 7)
        return responses[command, payload]

    client.command.side_effect = reply
    result = await async_get_area_mowing_settings(client, 12, height_max=0)
    assert result["settings"] == {
        "daylight_only": False,
        "vision_obstacle_profile": "residential_garden",
    }
    await async_set_area_mowing_setting(client, 12, 0, {"daylight_only": True})
    with pytest.raises(ValueError, match="does not support"):
        await async_set_area_mowing_setting(client, 12, 0, {"vision_positioning": True})
    assert responses[daylight, identity] == b"\x01"
