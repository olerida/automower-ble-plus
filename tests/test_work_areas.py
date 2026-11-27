"""Verify indexed work areas and Unicode names over BLE."""

from unittest.mock import AsyncMock

import pytest

from custom_components.automower_ble_plus.features import I16, U32
from custom_components.automower_ble_plus.mapfiles import MapKind
from custom_components.automower_ble_plus.schedule import ScheduleTask
from custom_components.automower_ble_plus.transport import CommandRejected
from custom_components.automower_ble_plus.wire import Command
from custom_components.automower_ble_plus.work_areas import (
    async_create_work_area,
    async_delete_work_area,
    async_get_area_mowing_settings,
    async_get_work_area_status,
    async_get_work_areas,
    async_reset_work_area_progress,
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


@pytest.mark.asyncio
@pytest.mark.parametrize("rejected", [False, True])
async def test_work_area_creation_links_the_verified_polygon_and_cleans_up_failures(
    monkeypatch, rejected
):
    client = AsyncMock()
    client.is_connected = lambda: True
    verify = AsyncMock()
    monkeypatch.setattr(
        "custom_components.automower_ble_plus.work_areas.async_verify_mowing_area",
        verify,
    )
    encoded_name = "Jardín\0".encode("utf-16-le")
    name = "Jardín"
    identity = U32.pack(12)
    checksum = "01" * 16
    client.command.side_effect = [
        b"\x01",
        U32.pack(0),
        identity,
        b"",
        *(
            [CommandRejected(Command(5604, 19), 5), b"", U32.pack(0)]
            if rejected
            else [b"", encoded_name, b"lawn\0", b"\x01"]
        ),
    ]
    if rejected:
        with pytest.raises(ValueError, match="reverted"):
            await async_create_work_area(client, name, "lawn", checksum)
        client.command.assert_any_await(Command(5604, 5), identity)
    else:
        assert await async_create_work_area(client, name, "lawn", checksum) == {
            "mission_id": 12,
            "name": name,
            "mowing_area_id": "lawn",
        }
        client.command.assert_any_await(Command(5604, 19), identity + b"\x01")
    verify.assert_awaited_once_with(client, "lawn", checksum, kind=MapKind.SITE)
    client.command.assert_any_await(Command(5604, 12), identity + b"lawn\0")


@pytest.mark.asyncio
async def test_interrupted_aim_creation_does_not_remove_a_mission_in_another_profile(
    monkeypatch,
):
    client = AsyncMock()
    client.is_connected = lambda: True
    monkeypatch.setattr(
        "custom_components.automower_ble_plus.work_areas.async_verify_mowing_area",
        AsyncMock(),
    )
    confirm = AsyncMock(
        side_effect=[
            None,
            ValueError("Installation changed"),
            ValueError("Installation changed"),
        ]
    )
    client.command.side_effect = [b"\1", U32.pack(0), U32.pack(12)]
    with pytest.raises(ValueError, match="Check work area 12"):
        await async_create_work_area(
            client,
            "Huerto",
            "polygon",
            "a" * 64,
            kind=MapKind.ZONES,
            confirm_context=confirm,
        )
    assert [call.args[0].operation for call in client.command.await_args_list] == [
        20,
        2,
        1,
    ]


@pytest.mark.asyncio
async def test_work_area_status_and_progress_reset_use_the_stored_mission():
    client = AsyncMock()
    identity = U32.pack(12)
    client.command.side_effect = [
        b"\x01",
        identity,
        "Jardín\0".encode("utf-16-le"),
        b"\x23",
        U32.pack(60),
        U32.pack(0),
        CommandRejected(Command(5604, 44), 7),
    ]
    assert await async_get_work_area_status(client, None) == {
        "available": True,
        "current_id": 12,
        "mission_id": 12,
        "name": "Jardín",
        "progress_percent": 35,
        "last_completed_epoch_seconds": 60,
    }
    client.command.side_effect = [b"\x01", b"\x23", b"", b"\0"]
    await async_reset_work_area_progress(client, 12)
    client.command.assert_any_await(Command(5604, 28), identity)


@pytest.mark.asyncio
async def test_delete_work_area_checks_identity_and_the_calendar(monkeypatch):
    client = AsyncMock()
    schedule = AsyncMock(
        return_value=[ScheduleTask(3600, 3600, (1, 0, 0, 0, 0, 0, 0), 12)]
    )
    monkeypatch.setattr(
        "custom_components.automower_ble_plus.work_areas.async_get_schedule", schedule
    )
    name = "Jardín\0".encode("utf-16-le")
    client.command.side_effect = [b"\x01", name]
    with pytest.raises(ValueError, match="schedule intervals"):
        await async_delete_work_area(client, 12, "Jardín")
    assert client.command.await_count == 2
    schedule.return_value = []
    client.command.side_effect = [b"\x01", name, b"", U32.pack(0)]
    await async_delete_work_area(client, 12, "Jardín")
    client.command.assert_any_await(Command(5604, 5), U32.pack(12))
