"""Verify that Home Assistant exposes only controls confirmed over BLE."""

import asyncio
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from struct import pack
from types import MappingProxyType
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo

import pytest
from automower_ble.protocol import MowerActivity, MowerState, ResponseResult
from homeassistant import auth, loader, setup
from homeassistant.config_entries import (
    SOURCE_USER,
    ConfigEntries,
    ConfigEntry,
    ConfigEntryState,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import (
    area_registry,
    device_registry,
    entity_registry,
    label_registry,
)

from custom_components.automower_ble_plus import (
    binary_sensor,
    button,
    calendar,
    diagnostics,
    mower_clock,
    number,
    select,
    sensor,
    switch,
    text,
    update,
)
from custom_components.automower_ble_plus import (
    coordinator as coordinator_module,
)
from custom_components.automower_ble_plus.const import DOMAIN
from custom_components.automower_ble_plus.coordinator import MowerCoordinator
from custom_components.automower_ble_plus.features import I16, I32, U16, U32
from custom_components.automower_ble_plus.firmware import get_firmware_profile
from custom_components.automower_ble_plus.schedule import ScheduleTask
from custom_components.automower_ble_plus.transport import (
    CommandRejected,
    MowerConnectionError,
)
from custom_components.automower_ble_plus.wire import Command, Event


class FakeAutomower420:
    def __init__(self):
        self.calls = []
        self.values = {
            (Command(4698, 9), b""): b"\x05\x00",
            (Command(4698, 22), b""): b"Firmware\0",
            (Command(4106, 20), b""): b"\x64",
            (Command(4586, 2), b""): b"\x07",
            (Command(4586, 3), b""): b"\x05",
            (Command(4586, 1), b""): b"\x00",
            (Command(4692, 6), b""): b"\x01",
            (Command(4422, 5), b""): b"\x00",
            (Command(4422, 2), b""): b"\x04",
            (Command(4712, 0), b""): U16.pack(310),
            (Command(4716, 0), b""): U16.pack(600),
            (Command(4716, 2), b""): U16.pack(90),
            (Command(4716, 4), b""): U16.pack(210),
            (Command(4706, 10), b"\x01"): U16.pack(150),
        }

    def is_connected(self):
        return True

    async def main_node_version(self):
        return "App-G3_devicetype-5.0"

    async def command(self, command, payload=b""):
        self.calls.append((command, payload))
        if command == Command(4712, 1):
            self.values[Command(4712, 0), b""] = payload
            return b""
        if command in (Command(4716, 1), Command(4716, 3)):
            self.values[Command(4716, command.operation - 1), b""] = payload
            return b""
        if command == Command(4706, 3):
            guide, width = payload
            self.values[Command(4706, 2), bytes([guide])] = bytes([width])
            return b""
        if command == Command(4706, 1):
            assert len(payload) == 2
            self.values[Command(4706, 0), b""] = payload
            return b""
        if command == Command(4706, 25):
            point, enabled = payload
            self.values[Command(4706, 24), bytes([point])] = bytes([enabled])
            return b""
        try:
            return self.values[command, payload]
        except KeyError as err:
            raise CommandRejected(command, 7) from err

    async def disconnect(self):
        pass

    async def keep_alive(self):
        pass


def _entry(pin=None):
    return ConfigEntry(
        domain=DOMAIN,
        title="Automower 420",
        version=1,
        minor_version=1,
        source=SOURCE_USER,
        data={"address": "00:11:22:33:44:55", "pin": pin},
        discovery_keys=MappingProxyType({}),
        options={},
        pref_disable_new_entities=False,
        pref_disable_polling=False,
        subentries_data=(),
        unique_id="00:11:22:33:44:55",
    )


@pytest.mark.asyncio
async def test_hidden_controls_and_model_specific_write(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    entry = _entry()
    client = FakeAutomower420()
    client.values.update(
        {
            (Command(4706, 14), b""): b"\x01",
            (Command(4706, 2), b"\x02"): b"\x09",
            (Command(4706, 2), b"\x03"): b"\x09",
            (Command(4706, 0), b""): b"\x02\x06",
            (Command(4706, 24), b"\x01"): b"\0",
        }
    )
    coordinator = MowerCoordinator(hass, entry)
    coordinator.client = client
    entry.runtime_data = coordinator
    await coordinator._async_setup()
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    assert (Command(4730, 4), b"") not in client.calls
    entities = []
    for platform in (switch, number):
        await platform.async_setup_entry(hass, entry, entities.extend)
    assert not entities

    await coordinator.async_refresh_optional()
    detected = {entity.key: entity for entity in entities}
    assert set(detected) == {
        "eco_mode",
        "drive_past_wire",
        "boundary_corridor",
        "guide_1_corridor",
        "starting_point_1_passage_cutting",
        "starting_point_1_distance",
        "reversing_distance",
        "sector_1_start_angle",
        "sector_1_end_angle",
    }
    assert (Command(4422, 2), b"") not in client.calls
    assert (Command(6170, 2), b"") not in client.calls
    assert (Command(6170, 8), b"") not in client.calls
    other_entities = []
    for platform in (button, select):
        await platform.async_setup_entry(hass, entry, other_entities.extend)
    assert not {entity.key for entity in other_entities} & {
        "scan_epos_reference_stations",
        "stop_epos_reference_scan",
        "epos_reference_station_id",
        "start_epos_docking_installation",
        "stop_epos_docking_installation",
    }
    assert not any(command.family == 5650 for command, _ in client.calls)
    assert (Command(4706, 2), b"\x03") not in client.calls
    assert (Command(4706, 24), b"") not in client.calls
    assert (Command(4706, 26), b"\x01") not in client.calls
    assert coordinator.device_info["model"] == "Automower 420"
    assert detected["starting_point_1_distance"].native_value == 150
    wire = detected["drive_past_wire"]
    assert (wire.native_min_value, wire.native_max_value, wire.native_step) == (
        200,
        500,
        10,
    )
    await wire.async_set_native_value(320)
    assert wire.native_value == 320
    assert (Command(4712, 1), U16.pack(320)) in client.calls
    guide = detected["guide_1_corridor"]
    assert (guide.native_min_value, guide.native_max_value) == (0, 9)
    await guide.async_set_native_value(0)
    assert guide.native_value == 0
    assert (Command(4706, 3), b"\x02\0") in client.calls
    with pytest.raises(HomeAssistantError, match="between 1 and 9"):
        await detected["boundary_corridor"].async_set_native_value(0)
    with pytest.raises(HomeAssistantError, match="stored minimum"):
        await detected["boundary_corridor"].async_set_native_value(1)
    await detected["boundary_corridor"].async_set_native_value(7)
    assert detected["boundary_corridor"].native_value == 7
    assert (Command(4706, 1), b"\x02\x07") in client.calls
    await detected["starting_point_1_passage_cutting"].async_turn_on()
    assert detected["starting_point_1_passage_cutting"].is_on
    assert (Command(4706, 25), b"\x01\x01") in client.calls
    reverse = detected["reversing_distance"]
    assert (
        reverse.native_min_value,
        reverse.native_max_value,
        reverse.native_step,
    ) == (150, 3000, 10)
    await reverse.async_set_native_value(650)
    assert reverse.native_value == 650
    sector = detected["sector_1_start_angle"]
    with pytest.raises(HomeAssistantError, match="at or below"):
        await sector.async_set_native_value(215)
    assert (Command(4716, 3), U16.pack(215)) not in client.calls
    await sector.async_set_native_value(175)
    assert sector.native_value == 175
    await coordinator.async_shutdown()


@pytest.mark.asyncio
async def test_calendar_resubscribes_and_refreshes_after_reconnect(
    tmp_path, monkeypatch
):
    entry = _entry()
    hass = HomeAssistant(str(tmp_path))
    hass.config.time_zone = "UTC"
    coordinator = MowerCoordinator(hass, entry)
    client = FakeAutomower420()
    coordinator.client = client
    entry.runtime_data = coordinator
    subscribe = (Command(4690, 0), b"")
    task = (Command(4690, 5), U32.pack(0))
    original = ScheduleTask.from_action(time(9), 60, ["mon"])
    changed = ScheduleTask.from_action(time(15), 45, ["mon"])
    client.values.update(
        {
            subscribe: b"",
            (Command(4690, 4), b""): U32.pack(1),
            (Command(4690, 14), b""): U32.pack(5),
            (Command(4690, 15), b""): U32.pack(5),
            task: original.encode()[1],
        }
    )
    connected = True

    async def connect(_device):
        nonlocal connected
        connected = True
        return ResponseResult.OK

    monkeypatch.setattr(client, "is_connected", lambda: connected)
    monkeypatch.setattr(client, "connect", connect, raising=False)
    monkeypatch.setattr(
        "custom_components.automower_ble_plus.coordinator.bluetooth.async_ble_device_from_address",
        lambda *args, **kwargs: object(),
    )
    await coordinator._async_setup()
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    await coordinator.async_refresh_optional()
    assert coordinator.data["weekly_schedule"].tasks == (original,)
    assert client.calls.count(subscribe) == 1

    client.values[task] = changed.encode()[1]
    connected = False
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    assert coordinator.data["weekly_schedule"].tasks == (changed,)
    assert client.calls.count(subscribe) == 2
    await coordinator.async_shutdown()


@pytest.mark.asyncio
async def test_calendar_expands_and_edits_the_mower_schedule(tmp_path, monkeypatch):
    now = datetime(2026, 11, 23, 8, tzinfo=UTC)
    monkeypatch.setattr(calendar.dt_util, "utcnow", lambda: now)
    entry = _entry()
    integrations = Path(__file__).resolve().parents[1] / "custom_components"
    (tmp_path / "custom_components").symlink_to(integrations, target_is_directory=True)
    hass = HomeAssistant(str(tmp_path))
    loader.async_setup(hass)
    hass.config.time_zone = "UTC"
    coordinator = MowerCoordinator(hass, entry)
    client = FakeAutomower420()
    coordinator.client = client
    entry.runtime_data = coordinator
    original = ScheduleTask.from_action(time(22, 30), 240, ["mon"], 17)
    stored = [original.encode()[1]]
    staged = []
    transactions = []
    send = client.command

    async def command(operation, payload=b""):
        if operation.family != 4690:
            return await send(operation, payload)
        if operation.operation == 4:
            return U32.pack(len(stored))
        if operation.operation == 5:
            return stored[U32.unpack(payload)[0]]
        if operation.operation in (14, 15):
            return U32.pack(5)
        if operation.operation == 10:
            transactions.append(operation)
            staged[:] = stored
        elif operation.operation == 9:
            staged.clear()
        elif operation.operation in (7, 16):
            staged.append(payload)
        elif operation.operation == 11:
            stored[:] = staged
        else:
            return await send(operation, payload)
        return b""

    monkeypatch.setattr(client, "command", command)
    await coordinator._async_setup()
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    entities = []
    await calendar.async_setup_entry(hass, entry, entities.extend)
    assert not entities
    await coordinator.async_refresh_optional()
    (entity,) = entities
    assert entity.available
    night = datetime(2026, 11, 24, 1, tzinfo=UTC)
    (overnight,) = await entity.async_get_events(
        hass, night, night + timedelta(hours=2)
    )
    assert overnight.start == datetime(2026, 11, 23, 22, 30, tzinfo=UTC)
    assert overnight.end == datetime(2026, 11, 24, 2, 30, tzinfo=UTC)
    assert overnight.rrule == "FREQ=WEEKLY;BYDAY=MO"
    assert overnight.uid == entity.event.uid

    early = datetime(2026, 11, 23, 14, tzinfo=UTC)
    event = {
        calendar.EVENT_START: early,
        calendar.EVENT_END: early + timedelta(hours=1),
        calendar.EVENT_RRULE: "FREQ=WEEKLY;INTERVAL=1;BYDAY=MO,WE",
    }
    with pytest.raises(ServiceValidationError, match="first upcoming occurrence"):
        await entity.async_create_event(
            **{
                **event,
                calendar.EVENT_START: early + timedelta(days=7),
                calendar.EVENT_END: early + timedelta(days=7, hours=1),
            }
        )
    assert not transactions
    await entity.async_create_event(**event)
    assert ScheduleTask.decode(stored[1]) == ScheduleTask.from_action(
        time(14), 60, ["mon", "wed"]
    )
    assert entity.event.start == early

    change = {
        **event,
        calendar.EVENT_START: early + timedelta(hours=7),
        calendar.EVENT_END: early + timedelta(hours=8),
        calendar.EVENT_RRULE: "FREQ=WEEKLY;BYDAY=MO",
    }
    with pytest.raises(ServiceValidationError, match="following"):
        await entity.async_update_event(overnight.uid, change, overnight.recurrence_id)
    assert len(transactions) == 1
    future = await entity.async_get_events(
        hass, now + timedelta(days=7), now + timedelta(days=8)
    )
    later = next(event for event in future if event.uid == overnight.uid)
    with pytest.raises(ServiceValidationError, match="next occurrence"):
        await entity.async_delete_event(later.uid, later.recurrence_id, "THISANDFUTURE")
    await entity.async_update_event(
        overnight.uid, change, overnight.recurrence_id, "THISANDFUTURE"
    )
    assert ScheduleTask.decode(stored[0]) == ScheduleTask.from_action(
        time(21), 60, ["mon"], 17
    )
    with pytest.raises(ServiceValidationError, match="changed"):
        await entity.async_delete_event(overnight.uid)
    events = await entity.async_get_events(hass, now, now + timedelta(days=3))
    updated = next(event for event in events if event.start.hour == 21)
    await entity.async_delete_event(updated.uid, updated.recurrence_id, "THISANDFUTURE")
    assert len(stored) == 1
    await coordinator.async_shutdown()


def test_calendar_clips_an_interval_at_the_start_of_daylight_saving():
    zone = ZoneInfo("Europe/Madrid")
    task = ScheduleTask.from_action(time(2, 30), 45, ["sun"])
    day = datetime(2026, 3, 29, tzinfo=UTC)
    [(start, end)] = calendar._occurrences(task, zone, day, day + timedelta(days=1))
    assert start == datetime(2026, 3, 29, 3, 0, tzinfo=zone)
    assert end == datetime(2026, 3, 29, 3, 15, tzinfo=zone)


@pytest.mark.asyncio
async def test_mower_name_uses_utf16_and_only_appears_after_valid_read(
    tmp_path, monkeypatch
):
    entry = _entry()
    hass = HomeAssistant(str(tmp_path))
    coordinator = MowerCoordinator(hass, entry)
    client = FakeAutomower420()
    coordinator.client = client
    entry.runtime_data = coordinator
    get_name = Command(4698, 3)
    set_name = Command(4698, 4)
    client.values[Command(4698, 10), b""] = U32.pack(1_900_012_345)
    client.values[get_name, b""] = b"\x01"
    send = client.command
    store_name = True
    writes = []

    async def command(operation, payload=b""):
        if operation == set_name:
            writes.append(payload)
            if store_name:
                client.values[get_name, b""] = payload
            return b""
        return await send(operation, payload)

    monkeypatch.setattr(client, "command", command)
    await coordinator._async_setup()
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    entities = []
    await text.async_setup_entry(hass, entry, entities.extend)
    await coordinator.async_refresh_optional()
    assert not entities
    client.values[get_name, b""] = "Robi".encode("utf-16-le").ljust(42, b"\0")
    await coordinator.async_refresh_optional()
    (entity,) = entities
    assert entity.native_value == "Robi"
    assert entity.device_info["serial_number"] == "1900012345"
    assert entity.native_max == 21

    await entity.async_set_value("Césped 420")
    assert writes == ["Césped 420".encode("utf-16-le").ljust(42, b"\0")]
    assert entity.native_value == "Césped 420"
    await entity.async_set_value("Césped 420")
    assert len(writes) == 1
    with pytest.raises(HomeAssistantError, match="21 UTF-16"):
        await entity.async_set_value("A" * 20 + "🌱")
    assert len(writes) == 1
    await entity.async_set_value("A" * 21)
    assert writes[-1] == b"A\0" * 21
    assert entity.native_value == "A" * 21

    store_name = False
    with pytest.raises(HomeAssistantError, match="did not confirm"):
        await entity.async_set_value("Jardín")
    assert entity.native_value == "A" * 21
    support = await diagnostics.async_get_config_entry_diagnostics(hass, entry)
    assert "serial_number" not in support
    assert "mower_name" not in support["telemetry"]
    await coordinator.async_shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("wire_format", "action", "fields", "expected"),
    (
        ("<BII", "forced_park", (1, 1_800_000_000, 3600), {"duration_seconds": 3600}),
        (
            "<BIII",
            "forced_mow",
            (2, 1_800_000_000, 7200, 17),
            {"duration_seconds": 7200, "mission_id": 17},
        ),
    ),
)
async def test_planner_override_exposes_requested_duration_and_optional_area(
    tmp_path, wire_format, action, fields, expected
):
    entry = _entry()
    coordinator = MowerCoordinator(HomeAssistant(str(tmp_path)), entry)
    client = FakeAutomower420()
    coordinator.client = client
    entry.runtime_data = coordinator
    get_override = Command(4658, 2)
    client.values[get_override, b""] = pack(wire_format, *fields)
    await coordinator._async_setup()
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    entities = []
    await sensor.async_setup_entry(coordinator.hass, entry, entities.extend)
    entity = next(entity for entity in entities if entity.key == "planner_override")
    assert entity.native_value == action
    assert entity.extra_state_attributes == expected

    client.values[get_override, b""] = pack(wire_format, 0, *fields[1:])
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    assert entity.native_value == "inactive"
    assert entity.extra_state_attributes == {}
    client.values[get_override, b""] = b"\x01"
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    assert not entity.available
    assert coordinator.data["battery"] == 100
    await coordinator.async_shutdown()


@pytest.mark.asyncio
async def test_recent_firmware_keeps_corridor_and_passage_cutting_separate(
    tmp_path, monkeypatch
):
    entry = _entry()
    coordinator = MowerCoordinator(HomeAssistant(str(tmp_path)), entry)
    client = FakeAutomower420()
    coordinator.client = client
    entry.runtime_data = coordinator
    monkeypatch.setattr(
        client, "main_node_version", AsyncMock(return_value="40.3_Main-App-P16_29.47")
    )
    client.values[Command(4698, 9), b""] = bytes([23, 1])
    client.values[Command(4706, 24), b""] = b"\x06"
    client.values[Command(4706, 26), b"\x01"] = b"\0"
    send = client.command
    writes = []

    async def command(operation, payload=b""):
        if operation == Command(4706, 25):
            writes.append((operation, payload))
            client.values[Command(4706, 24), b""] = payload
            return b""
        if operation == Command(4706, 27):
            writes.append((operation, payload))
            point, enabled = payload
            client.values[Command(4706, 26), bytes([point])] = bytes([enabled])
            return b""
        return await send(operation, payload)

    monkeypatch.setattr(client, "command", command)
    await coordinator._async_setup()
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    entities = []
    for platform in (number, switch):
        await platform.async_setup_entry(coordinator.hass, entry, entities.extend)
    await coordinator.async_refresh_optional()
    detected = {entity.key: entity for entity in entities}
    await detected["boundary_corridor"].async_set_native_value(7)
    await detected["starting_point_1_passage_cutting"].async_turn_on()
    assert writes == [(Command(4706, 25), b"\x07"), (Command(4706, 27), b"\x01\x01")]
    assert (Command(4706, 24), b"\x01") not in client.calls
    assert detected["boundary_corridor"].native_value == 7
    assert detected["starting_point_1_passage_cutting"].is_on
    await coordinator.async_shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", (True, False))
async def test_wire_tests_follow_the_firmware_start_stop_and_result_paths(
    tmp_path, monkeypatch, legacy
):
    entry = _entry()
    coordinator = MowerCoordinator(HomeAssistant(str(tmp_path)), entry)
    client = FakeAutomower420()
    coordinator.client = client
    entry.runtime_data = coordinator
    monkeypatch.setattr(coordinator, "async_request_refresh", AsyncMock())
    monkeypatch.setattr(coordinator, "_async_schedule_event_refresh", lambda: None)
    if not legacy:
        client.values[Command(4698, 9), b""] = bytes([23, 1])
        monkeypatch.setattr(
            client,
            "main_node_version",
            AsyncMock(return_value="40.3_Main-App-P16_29.47"),
        )
        client.values[Command(4706, 23), b""] = b"\0"
    state = Command(4586, 2)
    activity = Command(4586, 3)
    pause = Command(4586, 5)
    test_starting_point = Command(4706, 15)
    test_return = Command(4706, 17)
    test_abort = Command(4706, 18)
    client.values.update(
        {
            (Command(4708, 0), b"\x01"): b"\x01",
            (Command(4708, 1), b"\x01"): b"\0",
            (Command(4706, 16), b""): U16.pack(21),
        }
    )
    if not legacy:
        client.values[Command(4692, 27), b""] = b"\0"
    send = client.command

    async def command(operation, payload=b""):
        if operation in (pause, test_starting_point, test_return, test_abort):
            client.calls.append((operation, payload))
            return b""
        if operation == Command(4706, 11):
            client.calls.append((operation, payload))
            client.values[Command(4706, 10), payload[:1]] = payload[1:]
            return b""
        return await send(operation, payload)

    monkeypatch.setattr(client, "command", command)
    await coordinator._async_setup()
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    entities = {}

    def add_entities(new_entities):
        entities.update((entity.key, entity) for entity in new_entities)

    for platform in (button, sensor):
        await platform.async_setup_entry(coordinator.hass, entry, add_entities)
    await coordinator.async_refresh_optional()
    assert "test_starting_point_2" not in entities
    assert "test_return_guide_3" not in entities
    assert "test_return_boundary_right_0" not in entities
    start = entities["test_starting_point_1"]
    guide = entities["test_return_guide_2"]
    client.values[state, b""] = bytes(
        [MowerState.STOPPED if legacy else MowerState.PAUSED]
    )
    client.values[activity, b""] = bytes([MowerActivity.STOPPED_IN_GARDEN])
    with pytest.raises(
        HomeAssistantError, match="Put the mower in the charging station"
    ):
        await start.async_press()
    assert (test_starting_point, b"\x01\x01") not in client.calls
    if legacy:
        client.values[activity, b""] = bytes([MowerActivity.NONE])
    else:
        client.values[Command(4692, 27), b""] = b"\x01"
    client.calls.clear()
    await start.async_press()
    assert client.calls[-2:] == [(pause, b""), (test_starting_point, b"\x01\x01")]
    status = entities["wire_test_status"]
    stop = entities["stop_wire_test"]
    assert status.native_value == "pending_start"
    assert stop.available
    assert not guide.available
    with pytest.raises(HomeAssistantError, match="already active"):
        await guide.async_press()

    client.values[state, b""] = bytes([MowerState.IN_OPERATION])
    client.values[activity, b""] = bytes([MowerActivity.GOING_OUT])
    if not legacy:
        client.values[Command(4706, 23), b""] = b"\x02"
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    assert status.native_value == "in_operation"
    assert entities["wire_test_distance"].native_value == 21
    if legacy:
        coordinator._async_on_event(Event(4706, 4, b""))
        assert coordinator.wire_test_active
        coordinator._async_on_event(Event(4706, 2, b""))
    else:
        client.values[Command(4706, 23), b""] = b"\x03"
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    assert status.native_value == "done"
    assert not stop.available

    client.values[state, b""] = bytes(
        [MowerState.STOPPED if legacy else MowerState.PAUSED]
    )
    client.values[activity, b""] = bytes([MowerActivity.STOPPED_IN_GARDEN])
    if not legacy:
        client.values[Command(4692, 27), b""] = b"\0"
    client.calls.clear()
    await guide.async_press()
    assert client.calls[-2:] == [(pause, b""), (test_return, b"\x02")]
    assert not entities["wire_test_distance"].available
    await stop.async_press()
    assert client.calls[-1] == (pause if legacy else test_abort, b"")
    assert status.native_value == "aborted"
    save = entities["save_wire_test_distance"]
    assert not save.available
    with pytest.raises(HomeAssistantError, match="no starting-point distance"):
        await save.async_press()

    client.values[state, b""] = bytes(
        [MowerState.STOPPED if legacy else MowerState.PAUSED]
    )
    if legacy:
        client.values[activity, b""] = bytes([MowerActivity.PARKED])
    else:
        client.values[Command(4692, 27), b""] = b"\x01"
    await start.async_press()
    client.values[state, b""] = bytes([MowerState.IN_OPERATION])
    client.values[activity, b""] = bytes([MowerActivity.GOING_OUT])
    if not legacy:
        client.values[Command(4706, 23), b""] = b"\x02"
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    await stop.async_press()
    assert save.available
    await save.async_press()
    assert coordinator.data["starting_point_1_distance"] == 21
    assert (Command(4706, 11), b"\x01" + U16.pack(21)) in client.calls
    assert (Command(4586, 4), b"") not in client.calls
    await coordinator.async_shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("flexifence_family", (5926, 6050))
async def test_accessory_switches_require_mower_availability(
    tmp_path, monkeypatch, flexifence_family
):
    entry = _entry()
    coordinator = MowerCoordinator(HomeAssistant(str(tmp_path)), entry)
    client = FakeAutomower420()
    coordinator.client = client
    entry.runtime_data = coordinator
    ultrasonic = Command(4728, 1)
    edge_cut = Command(6690, 2)
    flexifence_available = Command(flexifence_family, 0)
    flexifence = Command(flexifence_family, 2)
    client.values.update(
        {
            (Command(4728, 0), b""): b"\x01",
            (ultrasonic, b""): b"\0",
            (Command(6690, 3), b""): b"\0",
            (edge_cut, b""): b"\0",
            (flexifence_available, b""): b"\0",
            (flexifence, b""): b"\0",
        }
    )
    send = client.command

    async def command(operation, payload=b""):
        if operation == Command(4728, 2) and payload == b"\x01":
            client.values[ultrasonic, b""] = payload
            return b""
        if operation == Command(flexifence_family, 3):
            assert payload == b"\x01"
            client.values[flexifence, b""] = payload
            return b""
        return await send(operation, payload)

    monkeypatch.setattr(client, "command", command)
    await coordinator._async_setup()
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    entities = []
    await switch.async_setup_entry(coordinator.hass, entry, entities.extend)
    await coordinator.async_refresh_optional()
    detected = {entity.key: entity for entity in entities}
    assert "edge_cut" not in detected
    assert (edge_cut, b"") not in client.calls
    assert "flexifence" not in detected
    assert (flexifence, b"") not in client.calls
    await detected["ultrasonic"].async_turn_on()
    assert detected["ultrasonic"].is_on
    client.values[flexifence_available, b""] = b"\x01"
    await coordinator.async_refresh_optional()
    flexifence_switch = next(
        entity for entity in entities if entity.key == "flexifence"
    )
    assert not flexifence_switch.is_on
    await flexifence_switch.async_turn_on()
    assert flexifence_switch.is_on
    await coordinator.async_shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("zone_response", "expected_offset"),
    ((None, 7200), (b"America/New_York\0", -14400), (bytes(40), 7200)),
)
async def test_synchronize_mower_clock_uses_local_calendar_time(
    tmp_path, monkeypatch, zone_response, expected_offset
):
    now = datetime(2026, 7, 14, 12, tzinfo=UTC)
    monkeypatch.setattr(
        mower_clock, "datetime", type("Clock", (), {"now": lambda _: now})
    )
    entry = _entry()
    hass = HomeAssistant(str(tmp_path))
    hass.config.time_zone = "Europe/Madrid"
    coordinator = MowerCoordinator(hass, entry)
    client = FakeAutomower420()
    coordinator.client = client
    entry.runtime_data = coordinator
    read_time = Command(4690, 2)
    set_time = Command(4690, 3)
    read_zone = Command(6746, 1)
    set_zone = Command(6746, 2)
    read_offset = Command(6746, 3)
    set_offset = Command(6746, 4)
    automatic = Command(6746, 7)
    client.values[read_time, b""] = U32.pack(1_700_000_000)
    if zone_response is not None:
        client.values[read_zone, b""] = zone_response
        client.values[read_offset, b""] = I32.pack(0)
        client.values[automatic, b""] = b"\0"
    send = client.command
    writes = []
    accept_time = True

    async def command(operation, payload=b""):
        if operation in (set_time, set_zone, set_offset):
            writes.append((operation, payload))
            if operation != set_time or accept_time:
                client.values[
                    Command(operation.family, operation.operation - 1), b""
                ] = payload
            return b""
        return await send(operation, payload)

    monkeypatch.setattr(client, "command", command)
    await coordinator._async_setup()
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    entities = {}

    def add_entities(new_entities):
        entities.update((entity.key, entity) for entity in new_entities)

    for platform in (button, sensor):
        await platform.async_setup_entry(hass, entry, add_entities)
    assert "sync_mower_clock" not in entities
    await coordinator.async_refresh_optional()
    await entities["sync_mower_clock"].async_press()
    expected_wall_time = int(now.timestamp()) + expected_offset
    assert writes[-1] == (set_time, U32.pack(expected_wall_time))
    assert len(writes) == 1 + (zone_response is not None) + (zone_response == bytes(40))
    if zone_response == bytes(40):
        assert writes[0] == (set_zone, b"Europe/Madrid".ljust(40, b"\0"))
    if zone_response is not None:
        assert writes[-2] == (set_offset, I32.pack(expected_offset))
    assert entities["mower_clock"].native_value == datetime.fromtimestamp(
        expected_wall_time, UTC
    ).replace(tzinfo=None).isoformat(" ")

    client.values[read_time, b""] = U32.pack(1_700_000_000)
    accept_time = False
    with pytest.raises(HomeAssistantError, match="did not confirm"):
        await entities["sync_mower_clock"].async_press()
    if zone_response is not None:
        accept_time = True
        client.values[automatic, b""] = b"\x01"
        writes.clear()
        await coordinator.async_refresh_optional()
        assert writes == [(set_time, U32.pack(expected_wall_time))]
        await coordinator.async_refresh_optional()
        assert len(writes) == 1
    await coordinator.async_shutdown()


@pytest.mark.asyncio
async def test_vision_settings_and_telemetry_follow_hardware_availability(
    tmp_path, monkeypatch
):
    entry = _entry()
    coordinator = MowerCoordinator(HomeAssistant(str(tmp_path)), entry)
    client = FakeAutomower420()
    coordinator.client = client
    entry.runtime_data = coordinator
    monkeypatch.setattr(
        client, "main_node_version", AsyncMock(return_value="40.14_Main-App-P22_102.14")
    )
    available = Command(7338, 2)
    profile = Command(7338, 6)
    client.values.update(
        {
            (Command(7414, 2), b""): b"\x01",
            (Command(7414, 5), b""): b"\x01",
            (Command(6170, 14), b""): b"\0",
            (Command(6170, 15), b""): b"\x01",
            (available, b""): b"\x01",
            (Command(7338, 8), b""): b"\x01",
            (Command(7338, 10), b""): b"\x01",
            (Command(7338, 12), b""): b"\0",
            (profile, b""): U32.pack(0),
        }
    )
    send = client.command

    async def command(operation, payload=b""):
        if operation == Command(7338, 5):
            assert payload == U32.pack(1)
            client.values[profile, b""] = payload
            return b""
        return await send(operation, payload)

    monkeypatch.setattr(client, "command", command)
    await coordinator._async_setup()
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    entities = []
    for platform in (binary_sensor, switch, select):
        await platform.async_setup_entry(coordinator.hass, entry, entities.extend)
    assert not entities
    await coordinator.async_refresh_optional()
    detected = {entity.key: entity for entity in entities}
    assert "vision_positioning" not in detected
    assert detected["boundary_edge_detection"].is_on
    assert detected["vision_keep_running_reduced"].is_on
    assert detected["vision_obstacle_avoidance_active"].is_on
    assert not detected["vision_dirty_lens"].is_on
    setting = detected["vision_obstacle_profile"]
    await setting.async_select_option("people_and_wildlife")
    assert setting.current_option == "people_and_wildlife"
    client.values[available, b""] = b"\0"
    await coordinator.async_refresh_optional()
    assert not detected["vision_obstacle_avoidance_active"].available
    assert not setting.available
    await coordinator.async_shutdown()


@pytest.mark.asyncio
async def test_epos_controls_follow_reported_hardware_and_correction_source(
    tmp_path, monkeypatch
):
    entry = _entry()
    coordinator = MowerCoordinator(HomeAssistant(str(tmp_path)), entry)
    client = FakeAutomower420()
    coordinator.client = client
    entry.runtime_data = coordinator
    enabled = Command(6170, 2)
    wire_assistance = Command(6170, 8)
    cloud = Command(6778, 4)
    client.values.update(
        {
            (Command(6170, 1), b""): b"\x01",
            (enabled, b""): b"\x01",
            (Command(6170, 7), b""): b"\x01",
            (wire_assistance, b""): b"\0",
            (Command(6170, 10), b""): b"\0",
            (Command(6170, 11), b""): b"\x01",
            (Command(4692, 23), b""): U32.pack(0),
            (Command(4692, 24), b""): U32.pack(1),
            (Command(5336, 3), b""): b"\x02",
            (Command(5650, 4), b""): b"\x50",
            (Command(5650, 17), b""): b"\x01",
            (Command(5242, 0), b""): pack("<iii", 3000, -4000, 40),
        }
    )
    send = client.command

    async def command(operation, payload=b""):
        if operation == Command(6170, 9):
            assert payload == b"\x01"
            client.values[wire_assistance, b""] = payload
            return b""
        return await send(operation, payload)

    monkeypatch.setattr(client, "command", command)
    await coordinator._async_setup()
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    entities = {}

    def add_entities(new_entities):
        entities.update((entity.key, entity) for entity in new_entities)

    for platform in (binary_sensor, sensor, switch):
        await platform.async_setup_entry(coordinator.hass, entry, add_entities)
    await coordinator.async_refresh_optional()
    assert entities["epos_enabled"].is_on
    assert "epos_dock_by_wire" not in entities
    assert (Command(6170, 11), b"") not in client.calls
    assert entities["epos_rtk_solution"].native_value == "fixed"
    assert entities["epos_installation_state"].native_value == "idle"
    assert entities["epos_installation_result"].native_value == "success"
    assert entities["epos_reference_station_channel"].native_value == "wifi"
    assert entities["epos_reference_station_signal"].native_value == 80
    assert entities["epos_reference_station_distance"].native_value == 5000
    assert "epos_cloud_signal" not in entities
    assert not entities["epos_wire_assistance"].is_on
    await entities["epos_wire_assistance"].async_turn_on()
    assert entities["epos_wire_assistance"].is_on

    client.values[cloud, b""] = b"\x01"
    client.values[Command(6778, 11), b""] = b"\x42"
    client.values[Command(4692, 23), b""] = b"\x00"
    client.calls.clear()
    await coordinator.async_refresh_optional()
    assert (Command(5650, 4), b"") not in client.calls
    assert not entities["epos_reference_station_signal"].available
    assert not entities["epos_installation_state"].available
    assert entities["epos_cloud_signal"].native_value == 66

    client.values[enabled, b""] = b"\0"
    await coordinator.async_refresh_optional()
    assert not entities["epos_enabled"].is_on
    assert not entities["epos_cloud_signal"].available
    assert not entities["epos_rtk_solution"].available
    await coordinator.async_shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("can_stop", [False, True])
async def test_docking_pattern_checks_the_mower_then_reports_result(
    tmp_path, monkeypatch, can_stop
):
    entry = _entry()
    coordinator = MowerCoordinator(HomeAssistant(str(tmp_path)), entry)
    client = FakeAutomower420()
    coordinator.client = client
    entry.runtime_data = coordinator
    version = "40.14_Main-App-P22_102.14" if can_stop else "40.9_Main-App-P14.2_54.1"
    monkeypatch.setattr(client, "main_node_version", AsyncMock(return_value=version))
    docked = Command(4692, 27)
    mower_state = Command(4586, 2)
    pause = Command(4586, 5)
    begin_pattern = Command(4692, 21)
    stop_pattern = Command(4692, 35)
    pattern_state = Command(4692, 23)
    pattern_result = Command(4692, 24)
    client.values.update(
        {
            (docked, b""): b"\0",
            (pattern_state, b""): U32.pack(0),
            (pattern_result, b""): U32.pack(0),
            (pause, b""): b"",
            (begin_pattern, b""): b"",
        }
    )
    if can_stop:
        client.values[Command(6170, 1), b""] = b"\x01"
        client.values[Command(6170, 2), b""] = b"\0"
        client.values[stop_pattern, b""] = b""
    send = client.command
    accept_pause = False

    async def command(operation, payload=b""):
        reply = await send(operation, payload)
        if operation == pause and accept_pause:
            client.values[mower_state, b""] = b"\x05"
        elif operation == stop_pattern:
            client.values[pattern_state, b""] = U32.pack(0)
            client.values[pattern_result, b""] = U32.pack(3)
        return reply

    async def refresh():
        coordinator.async_set_updated_data(await coordinator._async_update_data())

    monkeypatch.setattr(client, "command", command)
    monkeypatch.setattr(coordinator, "async_request_refresh", refresh)
    await coordinator._async_setup()
    await refresh()
    entities = {}

    def add_entities(new_entities):
        entities.update((entity.key, entity) for entity in new_entities)

    for platform in (button, sensor):
        await platform.async_setup_entry(coordinator.hass, entry, add_entities)
    await coordinator.async_refresh_optional()
    if can_stop:
        assert "start_epos_docking_installation" not in entities
        assert "stop_epos_docking_installation" not in entities
        client.values[Command(6170, 2), b""] = b"\x01"
        await coordinator.async_refresh_optional()
    start = entities["start_epos_docking_installation"]
    stop = entities.get("stop_epos_docking_installation")
    assert start.available
    assert (stop is not None) == can_stop
    if stop:
        assert not stop.available
        client.values[Command(6170, 2), b""] = b"\0"
        with pytest.raises(HomeAssistantError, match="does not report"):
            await start.async_press()
        client.values[Command(6170, 2), b""] = b"\x01"
    with pytest.raises(HomeAssistantError, match="charging station first"):
        await start.async_press()
    assert (pause, b"") not in client.calls
    client.values[docked, b""] = b"\x01"
    with pytest.raises(HomeAssistantError, match="paused"):
        await start.async_press()
    assert (begin_pattern, b"") not in client.calls
    accept_pause = True
    await start.async_press()
    assert client.calls.count((begin_pattern, b"")) == 1
    assert client.calls.index((pause, b"")) < client.calls.index((begin_pattern, b""))
    assert not start.available
    with pytest.raises(HomeAssistantError, match="starting"):
        await start.async_press()

    client.values[pattern_state, b""] = U32.pack(1)
    await coordinator.async_keep_alive()
    assert entities["epos_installation_state"].native_value == "in_operation"
    if stop:
        assert stop.available
        await stop.async_press()
        assert client.calls.count((stop_pattern, b"")) == 1
        assert entities["epos_installation_result"].native_value == "stopped"
    else:
        with pytest.raises(HomeAssistantError, match="does not offer"):
            await coordinator.async_stop_epos_docking_installation()
        client.values[pattern_state, b""] = U32.pack(0)
        client.values[pattern_result, b""] = U32.pack(1)
        await coordinator.async_keep_alive()
        assert entities["epos_installation_result"].native_value == "success"
    assert start.available
    assert not coordinator.docking_installation_active
    await coordinator.async_shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("modern", [False, True])
async def test_scan_and_pair_epos_reference_station(tmp_path, monkeypatch, modern):
    entry = _entry()
    coordinator = MowerCoordinator(HomeAssistant(str(tmp_path)), entry)
    client = FakeAutomower420()
    coordinator.client = client
    entry.runtime_data = coordinator
    get_id = Command(5650, 1)
    set_id = Command(5650, 2)
    start_scan = Command(5650, 3)
    save_position = Command(5650, 7)
    stop_scan = Command(5650, 9)
    client.values.update(
        {
            (Command(6170, 1), b""): b"\x01",
            (Command(6170, 2), b""): b"\0",
            (get_id, b""): U32.pack(7 if modern else 0xFFFFFFFF),
            (Command(5650, 0), b""): b"",
            (start_scan, U32.pack(60)): b"",
            (save_position, b""): b"",
            (stop_scan, b""): b"",
        }
    )
    if modern:
        client.values[Command(5650, 15), b""] = b"\x01"

    send = client.command
    store_id = False

    async def command(operation, payload=b""):
        if operation == start_scan:
            # The mower can discover stations before acknowledging Scan.
            for station_id in (31, 14, 31, 0, 0xFFFFFFFF):
                coordinator._async_on_event(
                    Event(5650, 1, pack("<Iiii", station_id, 111, 222, 333))
                )
            coordinator._async_on_event(Event(5650, 1, U32.pack(99)))
            coordinator._async_on_event(Event(5650, 2, b"\x01"))
        elif operation == set_id:
            client.calls.append((operation, payload))
            if store_id:
                client.values[get_id, b""] = payload
            return b""
        return await send(operation, payload)

    monkeypatch.setattr(client, "command", command)
    await coordinator._async_setup()
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    entities = {}

    def add_entities(new_entities):
        entities.update((entity.key, entity) for entity in new_entities)

    for platform in (button, select, sensor):
        await platform.async_setup_entry(coordinator.hass, entry, add_entities)
    await coordinator.async_refresh_optional()
    start = entities["scan_epos_reference_stations"]
    stop = entities["stop_epos_reference_scan"]
    station = entities["epos_reference_station_id"]
    assert start.available
    assert not stop.available
    assert station.current_option == ("7" if modern else None)

    await start.async_press()
    status = entities["epos_reference_scan_status"]
    assert status.native_value == "scanning"
    assert not station.available
    assert not start.available
    assert stop.available
    if modern:
        coordinator._async_on_event(Event(5650, 2, b""))
    else:
        await stop.async_press()
        assert client.calls[-1] == (stop_scan, b"")
    await coordinator._reference_scan_task
    assert status.native_value == ("complete" if modern else "stopped")
    assert station.available
    assert station.options == (["7", "14", "31"] if modern else ["14", "31"])

    with pytest.raises(ServiceValidationError, match="not available"):
        await station.async_select_option("99")
    with pytest.raises(HomeAssistantError, match="did not store"):
        await station.async_select_option("14")
    assert (save_position, b"") not in client.calls
    assert station.current_option == ("7" if modern else None)
    store_id = True
    await station.async_select_option("31")
    assert client.calls[-3:] == [
        (set_id, U32.pack(31)),
        (get_id, b""),
        (save_position, b""),
    ]
    assert station.current_option == "31"

    if modern:
        # A failed position write still leaves the newly stored ID on the mower.
        del client.values[save_position, b""]
        with pytest.raises(HomeAssistantError, match="Could not pair"):
            await station.async_select_option("14")
        assert station.current_option == "14"
    else:
        monkeypatch.setattr(coordinator_module, "REFERENCE_SCAN_TIMEOUT", 0)
        await start.async_press()
        await coordinator._reference_scan_task
        assert status.native_value == "failed"
        assert station.options == ["31"]
        assert client.calls[-1] == (stop_scan, b"")
    await coordinator.async_shutdown()


@pytest.mark.asyncio
async def test_confirmable_fault_must_still_match_when_acknowledged(tmp_path):
    entry = _entry()
    coordinator = MowerCoordinator(HomeAssistant(str(tmp_path)), entry)
    client = FakeAutomower420()
    coordinator.client = client
    entry.runtime_data = coordinator
    code = Command(4586, 6)
    confirmable = Command(4586, 8)
    confirm = Command(4586, 9)
    client.values.update(
        {
            (code, b""): U32.pack(2),
            (confirmable, b""): b"\x01",
            (confirm, b""): b"",
        }
    )
    await coordinator._async_setup()
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    entities = []
    for platform in (sensor, button):
        await platform.async_setup_entry(coordinator.hass, entry, entities.extend)
    devices = {entity.key: entity for entity in entities}
    fault = devices["active_fault"]
    assert fault.native_value == "no_loop_signal"
    assert fault.extra_state_attributes == {"code": 2, "message_key": "no_loop_signal"}
    acknowledge = devices["confirm_fault"]
    client.values[code, b""] = U32.pack(3)
    with pytest.raises(HomeAssistantError, match="no longer reports"):
        await acknowledge.async_press()
    assert (confirm, b"") not in client.calls
    client.values[code, b""] = U32.pack(2)
    client.values[confirmable, b""] = b"\0"
    with pytest.raises(HomeAssistantError, match="no longer reports"):
        await acknowledge.async_press()
    assert (confirm, b"") not in client.calls
    client.values[confirmable, b""] = b"\x01"
    await acknowledge.async_press()
    assert (confirm, b"") in client.calls
    client.values[code, b""] = U32.pack(0)
    client.values[confirmable, b""] = b"\0"
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    assert fault.native_value == "none"
    assert not acknowledge.available
    await coordinator.async_shutdown()


@pytest.mark.asyncio
async def test_installation_select_is_detected_and_disambiguates_names(
    tmp_path, monkeypatch
):
    entry = _entry()
    coordinator = MowerCoordinator(HomeAssistant(str(tmp_path)), entry)
    client = FakeAutomower420()
    coordinator.client = client
    entry.runtime_data = coordinator
    active = Command(5900, 12)
    profile_name = "Jardín\0".encode("utf-16-le")
    client.values.update(
        {
            (Command(5900, 17), b""): b"\x01",
            (Command(5900, 1), b""): U32.pack(2),
            (Command(5900, 3), U32.pack(0)): U32.pack(4) + profile_name,
            (Command(5900, 3), U32.pack(1)): U32.pack(7) + profile_name,
            (Command(5900, 5), U32.pack(7)): profile_name,
            (active, b""): U32.pack(4),
        }
    )
    send = client.command

    async def command(operation, payload=b""):
        if operation == Command(5900, 14):
            client.values[active, b""] = payload
            return b""
        return await send(operation, payload)

    monkeypatch.setattr(client, "command", command)
    await coordinator._async_setup()
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    entities = []
    await select.async_setup_entry(coordinator.hass, entry, entities.extend)
    assert not entities
    await coordinator.async_refresh_optional()
    picker = next(
        entity for entity in entities if entity.key == "installation_selection"
    )
    assert picker.options == ["Jardín (4)", "Jardín (7)"]
    assert picker.current_option == "Jardín (4)"
    await picker.async_select_option("Jardín (7)")
    assert picker.current_option == "Jardín (7)"
    await coordinator.async_shutdown()


@pytest.mark.asyncio
async def test_legacy_420_reports_spiral_settings_without_manual_spot_trigger(
    tmp_path, monkeypatch
):
    entry = _entry()
    coordinator = MowerCoordinator(HomeAssistant(str(tmp_path)), entry)
    client = FakeAutomower420()
    coordinator.client = client
    entry.runtime_data = coordinator
    sensitivity = Command(4710, 3)
    client.values.update(
        {
            (Command(4710, 0), b""): b"\x01",
            (Command(4710, 1), b""): b"\x01",
            (sensitivity, b""): b"\x02",
        }
    )
    send = client.command

    async def command(operation, payload=b""):
        if operation == Command(4710, 4) and payload == b"\x04":
            client.values[sensitivity, b""] = payload
            return b""
        return await send(operation, payload)

    monkeypatch.setattr(client, "command", command)
    await coordinator._async_setup()
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    entities = []
    for platform in (button, switch, select):
        await platform.async_setup_entry(coordinator.hass, entry, entities.extend)
    await coordinator.async_refresh_optional()
    detected = {entity.key: entity for entity in entities}
    assert detected["spiral_cutting"].is_on
    assert "spot_cut_available" not in detected
    await detected["spiral_cutting_sensitivity"].async_select_option("very_high")
    assert detected["spiral_cutting_sensitivity"].current_option == "very_high"
    with pytest.raises(HomeAssistantError, match="Unsupported mower action"):
        await coordinator.async_mower_action("spot_cut")
    assert (Command(4710, 7), b"") not in client.calls
    await coordinator.async_shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("pin", [None, "0042"])
async def test_theft_alarm_configuration_requires_the_operator_pin(
    tmp_path, pin, monkeypatch
):
    entry = _entry(pin)
    coordinator = MowerCoordinator(HomeAssistant(str(tmp_path)), entry)
    client = FakeAutomower420()
    coordinator.client = client
    entry.runtime_data = coordinator
    duration = Command(4736, 15)
    client.values.update(
        {
            (duration, b""): U32.pack(120),
            (Command(4736, 7), b""): b"\x01",
            (Command(4736, 13), b""): b"\x01",
            (Command(4736, 26), b""): b"\x02",
        }
    )
    send = client.command

    async def command(operation, payload=b""):
        if operation == Command(4736, 16) and payload == U32.pack(180):
            client.values[duration, b""] = payload
            return b""
        return await send(operation, payload)

    monkeypatch.setattr(client, "command", command)
    await coordinator._async_setup()
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    entities = []
    for platform in (binary_sensor, number, switch):
        await platform.async_setup_entry(coordinator.hass, entry, entities.extend)
    await coordinator.async_refresh_optional()
    detected = {entity.key: entity for entity in entities}
    assert detected["theft_stop_alarm"].is_on
    client.values[Command(4736, 26), b""] = b"\x04"
    await coordinator.async_keep_alive()
    assert not detected["theft_stop_alarm"].is_on
    assert "theft_alarm_on_motion" not in detected
    if pin:
        assert detected["theft_alarm_on_stop"].is_on
        alarm_duration = detected["theft_alarm_duration"]
        assert alarm_duration.native_value == 120
        await alarm_duration.async_set_native_value(180)
        assert alarm_duration.native_value == 180
    else:
        assert "theft_alarm_on_stop" not in detected
        assert "theft_alarm_duration" not in detected
        assert (duration, b"") not in client.calls
    await coordinator.async_shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key", "family", "state_id", "version_id", "check_id", "install_id", "installed"),
    [
        ("mower_firmware_update", 5296, 6, 9, 8, 10, Command(4698, 22)),
        ("station_firmware_update", 6244, 4, 7, 6, 8, Command(6552, 13)),
    ],
)
async def test_firmware_controls_require_model_support_and_a_ready_vendor_update(
    tmp_path, key, family, state_id, version_id, check_id, install_id, installed
):
    entry = _entry()
    coordinator = MowerCoordinator(HomeAssistant(str(tmp_path)), entry)
    client = FakeAutomower420()
    coordinator.client = client
    entry.runtime_data = coordinator
    state = Command(family, state_id)
    version = Command(family, version_id)
    check = Command(family, check_id)
    install = Command(family, install_id)
    activity = Command(4586, 3)
    client.values.update(
        {
            (Command(family, 1), b""): b"\x01",
            (state, b""): b"\x01",
            (installed, b""): b"1.0\0",
            (check, b""): b"",
            (install, b""): b"",
        }
    )

    await coordinator._async_setup()
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    buttons = []
    entities = []
    await button.async_setup_entry(coordinator.hass, entry, buttons.extend)
    await update.async_setup_entry(coordinator.hass, entry, entities.extend)
    assert not entities
    await coordinator.async_refresh_optional()
    assert [entity.key for entity in entities] == [key]
    updater = entities[0]
    assert updater.installed_version == updater.latest_version == "1.0"
    probe = next(entity for entity in buttons if entity.key == f"{key}_check")
    assert probe.available
    await probe.async_press()
    assert (check, b"") in client.calls

    client.values[state, b""] = b"\x04"
    client.values[version, b""] = b"2.0\0"
    await coordinator.async_refresh_optional()
    assert updater.has_update
    assert updater.latest_version == "2.0"
    assert not probe.available
    with pytest.raises(HomeAssistantError, match="changed"):
        await updater.async_install("1.9", False)
    client.values[activity, b""] = b"\x03"
    with pytest.raises(HomeAssistantError, match="charging station"):
        await updater.async_install(None, False)
    assert (install, b"") not in client.calls
    client.values[activity, b""] = b"\x05"
    await updater.async_install(None, False)
    assert (install, b"") in client.calls
    assert updater.in_progress
    client.values[state, b""] = b"\x01"
    client.values[installed, b""] = b"2.0\0"
    await coordinator.async_refresh_optional()
    assert updater.installed_version == updater.latest_version == "2.0"
    assert not updater.in_progress
    await coordinator.async_shutdown()


@pytest.mark.asyncio
async def test_home_assistant_loads_entry_and_unloads_platforms(tmp_path, monkeypatch):
    integrations = Path(__file__).resolve().parents[1] / "custom_components"
    (tmp_path / "custom_components").symlink_to(integrations, target_is_directory=True)
    hass = HomeAssistant(str(tmp_path))
    loader.async_setup(hass)
    hass.config.skip_pip = True
    hass.config_entries = ConfigEntries(hass, {})
    await hass.config_entries.async_initialize()
    device_registry.async_setup(hass)
    await asyncio.gather(
        area_registry.async_load(hass),
        device_registry.async_load(hass),
        entity_registry.async_load(hass),
        label_registry.async_load(hass),
    )
    hass.auth = await auth.auth_manager_from_config(hass, [], [])
    hass.config.components.update({"bluetooth", "bluetooth_adapters"})
    client = FakeAutomower420()
    area = U32.pack(12)
    monkeypatch.setattr(
        client, "main_node_version", AsyncMock(return_value="40.1_Main-App-P25_26.19")
    )
    client.values.update(
        {
            (Command(5604, 20), b""): b"\x01",
            (Command(5604, 13), area): b"\x02",
            (Command(5582, 0), b""): b"\x01",
            (Command(5582, 3), area): I16.pack(450),
            (Command(4730, 4), b""): b"",
            (Command(4658, 1), b""): U32.pack(1_799_999_900),
            (Command(4690, 0), b""): b"",
            (Command(4690, 2), b""): U32.pack(1_799_999_900),
            (Command(4690, 4), b""): U32.pack(1),
            (Command(4690, 5), U32.pack(0)): ScheduleTask.from_action(
                time(9), 60, ["mon"]
            ).encode()[1],
            (Command(4690, 14), b""): U32.pack(5),
            (Command(4690, 15), b""): U32.pack(5),
        }
    )
    monkeypatch.setattr(
        "custom_components.automower_ble_plus.coordinator.AutomowerClient",
        lambda *args: client,
    )
    try:
        async with asyncio.timeout(15):
            assert await setup.async_setup_component(hass, DOMAIN, {})
            entry = _entry()
            await hass.config_entries.async_add(entry)
            await hass.async_block_till_done()
            assert entry.state == ConfigEntryState.LOADED
            assert hass.states.get("lawn_mower.automower_420").state == "docked"
            assert hass.services.has_service(DOMAIN, "get_schedule")
            await entry.runtime_data.async_refresh_optional()
            await hass.async_block_till_done()
            (calendar_id,) = hass.states.async_entity_ids("calendar")
            monday = datetime(2026, 11, 23, tzinfo=UTC)
            calendar_response = await hass.services.async_call(
                "calendar",
                "get_events",
                {
                    "entity_id": calendar_id,
                    "start_date_time": monday.isoformat(),
                    "end_date_time": (monday + timedelta(days=1)).isoformat(),
                },
                blocking=True,
                return_response=True,
            )
            schedule_events = calendar_response[calendar_id]["events"]
            assert [event["start"] for event in schedule_events] == [
                "2026-11-23T09:00:00+00:00"
            ]
            assert client.calls.count((Command(4690, 0), b"")) == 1
            client.values[Command(4690, 5), U32.pack(0)] = ScheduleTask.from_action(
                time(11), 45, ["mon"]
            ).encode()[1]
            entry.runtime_data._async_on_event(Event(4690, 1, b"\x01"))
            await hass.async_block_till_done()
            assert entry.runtime_data.data["weekly_schedule"].tasks[0].start == 9 * 3600
            entry.runtime_data._async_on_event(Event(4690, 1, U32.pack(1)))
            await entry.runtime_data._event_refresh_task
            await hass.async_block_till_done()
            schedule = entry.runtime_data.data["weekly_schedule"]
            assert [(task.start, task.duration) for task in schedule.tasks] == [
                (11 * 3600, 45 * 60)
            ]
            entry.runtime_data._debounced_refresh.async_cancel()
            client.values[Command(6746, 1), b""] = b"Europe/Madrid\0"
            local_start = monday + timedelta(hours=11)
            client.values[Command(4658, 1), b""] = U32.pack(
                int(local_start.timestamp())
            )
            entry.runtime_data._async_on_event(Event(4690, 0, U32.pack(1_800_000_000)))
            await entry.runtime_data._event_refresh_task
            assert entry.runtime_data.data["mower_clock"] == 1_800_000_000
            registry = entity_registry.async_get(hass)
            next_start_id = registry.async_get_entity_id(
                "sensor", DOMAIN, f"{entry.runtime_data.address}_next_start"
            )
            next_start = datetime.fromisoformat(hass.states.get(next_start_id).state)
            assert next_start == monday + timedelta(hours=10)
            assert client.calls.count((Command(4690, 0), b"")) == 1
            event_ids = hass.states.async_entity_ids("event")
            assert len(event_ids) == 1
            entry.runtime_data._async_on_event(
                Event(4730, 0, pack("<IIIBii", 12, 1795600000, 2, 3, 41500000, 2000000))
            )
            await hass.async_block_till_done()
            message = hass.states.get(event_ids[0]).attributes
            assert message["event_type"] == "warning"
            assert message["code"] == 2
            assert message["message_count"] == 12
            assert "latitude" not in message
            target = {"entity_id": "lawn_mower.automower_420", "mission_id": 12}
            response = await hass.services.async_call(
                DOMAIN,
                "get_work_area_mowing",
                target,
                blocking=True,
                return_response=True,
            )
            assert response[target["entity_id"]]["settings"]["orientation"] == 45
            await hass.services.async_call(
                DOMAIN,
                "set_work_area_mowing",
                {**target, "orientation": 45},
                blocking=True,
            )
            assert (Command(5582, 4), area + I16.pack(450)) not in client.calls
            reset = (Command(4698, 8), b"")
            client.values[reset] = b""
            flow = await hass.config_entries.options.async_init(entry.entry_id)
            assert flow["type"] == FlowResultType.FORM
            flow = await hass.config_entries.options.async_configure(
                flow["flow_id"], {"confirm_reset": False}
            )
            assert flow["errors"] == {"confirm_reset": "confirmation_required"}
            assert reset not in client.calls
            reload_entry = Mock()
            monkeypatch.setattr(
                hass.config_entries, "async_schedule_reload", reload_entry
            )
            flow = await hass.config_entries.options.async_configure(
                flow["flow_id"], {"confirm_reset": True}
            )
            assert flow["reason"] == "reset_requested"
            assert client.calls.count(reset) == 1
            reload_entry.assert_called_once_with(entry.entry_id)
            assert await hass.config_entries.async_unload(entry.entry_id)
            assert entry.state == ConfigEntryState.NOT_LOADED
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply", ["acknowledged", "unsupported", "disconnected", "rejected"]
)
async def test_reset_power_cycle_falls_back_only_when_unsupported(
    tmp_path, monkeypatch, reply
):
    hass = HomeAssistant(str(tmp_path))
    hass.config_entries = ConfigEntries(hass, {})
    reload_entry = Mock()
    monkeypatch.setattr(hass.config_entries, "async_schedule_reload", reload_entry)
    entry = _entry()
    coordinator = MowerCoordinator(hass, entry)
    client = FakeAutomower420()
    coordinator.client = client
    restart = Command(4698, 42)
    reset = Command(4698, 8)
    client.values[reset, b""] = b""
    send = client.command

    async def command(operation, payload=b""):
        if operation == restart:
            client.calls.append((operation, payload))
            if reply == "unsupported":
                raise CommandRejected(operation, ResponseResult.INVALID_ID)
            if reply == "rejected":
                raise CommandRejected(operation, ResponseResult.NOT_ALLOWED)
            if reply == "disconnected":
                raise MowerConnectionError("The mower restarted before it replied")
            return b""
        return await send(operation, payload)

    monkeypatch.setattr(client, "command", command)
    with pytest.raises(HomeAssistantError, match="cannot accept"):
        await coordinator.async_reset_user_settings()
    assert not client.calls
    coordinator.firmware_profile = get_firmware_profile(
        (5, 0), "40.14_Main-App-P22_102.14"
    )
    assert coordinator.can_reset_user_settings
    if reply == "rejected":
        with pytest.raises(HomeAssistantError, match="rejected the reset"):
            await coordinator.async_reset_user_settings()
        reload_entry.assert_not_called()
        assert coordinator.can_reset_user_settings
    else:
        assert await coordinator.async_reset_user_settings() == (
            reply != "disconnected"
        )
        reload_entry.assert_called_once_with(entry.entry_id)
        assert not coordinator.can_reset_user_settings
        with pytest.raises(HomeAssistantError, match="cannot accept"):
            await coordinator.async_reset_user_settings()
    assert client.calls == (
        [(restart, b""), (reset, b"")] if reply == "unsupported" else [(restart, b"")]
    )
    await coordinator.async_shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("duration", "mission_id"), [(None, None), (1800, None), (1800, 11)]
)
async def test_secondary_area_uses_the_apps_manual_start_sequence(
    tmp_path, duration, mission_id
):
    coordinator = MowerCoordinator(HomeAssistant(str(tmp_path)), _entry())
    client = FakeAutomower420()
    coordinator.client = client
    mode = (Command(4586, 0), b"\x01")
    start = (Command(4586, 4), b"")
    encoded_duration = U32.pack(duration or 0)
    verification = [(Command(4586, 1), b"")]
    if mission_id is None:
        override = (Command(4658, 3), encoded_duration)
    else:
        area = (Command(5604, 13), U32.pack(mission_id))
        client.values[area] = b"\x01"
        verification.append(area)
        override = (Command(4658, 9), encoded_duration + U32.pack(mission_id))
    for request in (mode, override, start):
        client.values[request] = b""

    await coordinator.async_mower_action(
        "mow_secondary_area", duration, mission_id=mission_id
    )

    assert client.calls[: len(verification) + 3] == [
        *verification,
        mode,
        override,
        start,
    ]
    await coordinator.async_shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("version", "read_id", "reset_id"),
    [
        ("App-G3_devicetype-5.0", 7, 8),
        ("40.3_Main-App-P16_29.47", 13, 14),
    ],
)
async def test_blade_reset_uses_the_builds_command_after_confirming_the_counter(
    tmp_path, version, read_id, reset_id, monkeypatch
):
    entry = _entry()
    coordinator = MowerCoordinator(HomeAssistant(str(tmp_path)), entry)
    client = FakeAutomower420()
    coordinator.client = client
    entry.runtime_data = coordinator
    await coordinator._async_setup()
    coordinator.firmware_profile = get_firmware_profile((5, 0), version)
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    read = Command(4726, read_id)
    reset = Command(4726, reset_id)
    client.values[read, b""] = U32.pack(1240)
    send = client.command

    async def command(operation, payload=b""):
        if operation == reset:
            client.values[read, b""] = U32.pack(0)
            return b""
        return await send(operation, payload)

    monkeypatch.setattr(client, "command", command)
    entities = []
    await button.async_setup_entry(coordinator.hass, entry, entities.extend)
    assert "reset_blade_usage" not in {entity.key for entity in entities}
    await coordinator.async_refresh_optional()
    reset_button = next(
        entity for entity in entities if entity.key == "reset_blade_usage"
    )
    assert coordinator.data["blade_usage_time"] == 1240
    await reset_button.async_press()
    assert coordinator.data["blade_usage_time"] == 0
    await coordinator.async_shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("version", "pin", "enable_operation"),
    [
        ("App-G3_devicetype-5.0", "0042", 3),
        ("40.1_Main-App-P25_other", None, 15),
    ],
)
async def test_geofence_controls_follow_firmware_and_use_the_operator_pin(
    tmp_path, version, pin, enable_operation, monkeypatch
):
    entry = _entry(pin)
    coordinator = MowerCoordinator(HomeAssistant(str(tmp_path)), entry)
    client = FakeAutomower420()
    coordinator.client = client
    entry.runtime_data = coordinator
    monkeypatch.setattr(client, "main_node_version", lambda: asyncio.sleep(0, version))
    enabled = Command(4734, 2)
    sensitivity = Command(4734, 7)
    radius = Command(4734, 10)
    recenter = Command(4734, 6)
    client.values.update(
        {
            (Command(4734, 0), b""): b"\x01",
            (enabled, b""): b"\0",
            (Command(4734, 5), b""): b"\x01",
            (sensitivity, b""): b"\x06",
            (radius, b""): U16.pack(250),
            (recenter, U16.pack(42)): b"",
        }
    )
    enable = Command(4734, enable_operation)
    pin_payload = U16.pack(42) if pin else b""
    send = client.command

    async def command(operation, payload=b""):
        if operation == enable and payload == pin_payload + b"\x01":
            client.values[enabled, b""] = b"\x01"
            return b""
        if operation == Command(4734, 8) and payload == U16.pack(42) + b"\x04":
            client.values[sensitivity, b""] = b"\x04"
            return b""
        return await send(operation, payload)

    monkeypatch.setattr(client, "command", command)
    await coordinator._async_setup()
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    entities = []
    for platform in (switch, number, select, button):
        await platform.async_setup_entry(coordinator.hass, entry, entities.extend)
    assert not any(entity.key.startswith("geofence") for entity in entities)

    await coordinator.async_refresh_optional()
    detected = {entity.key: entity for entity in entities}
    await detected["geofence_enabled"].async_turn_on()
    assert detected["geofence_enabled"].is_on
    fence = await coordinator.async_read_geofence()
    assert fence["enabled"] is True
    assert fence["radius_m"] == 250
    if pin:
        assert "geofence_radius" not in detected
        setting = detected["geofence_sensitivity"]
        assert setting.current_option is None
        await setting.async_select_option("high")
        assert setting.current_option == "high"
        await detected["geofence_center_settable"].async_press()
        assert (recenter, U16.pack(42)) in client.calls
    else:
        assert "geofence_sensitivity" not in detected
        assert "geofence_center_settable" not in detected
        control = detected["geofence_radius"]
        assert (control.native_min_value, control.native_max_value) == (100, 5000)
    await coordinator.async_shutdown()
