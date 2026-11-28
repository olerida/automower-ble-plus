"""Verify that Home Assistant exposes only controls confirmed over BLE."""

import asyncio
import gzip
import json
from datetime import UTC, date, datetime, time, timedelta
from hashlib import md5
from pathlib import Path
from struct import pack, unpack_from
from types import MappingProxyType
from unittest.mock import AsyncMock, Mock
from zlib import crc32
from zoneinfo import ZoneInfo

import pytest
from automower_ble.protocol import MowerActivity, MowerState, ResponseResult
from homeassistant import auth, loader, setup
from homeassistant.components import frontend, websocket_api
from homeassistant.components.image import async_get_image
from homeassistant.components.image.const import DATA_COMPONENT as IMAGE_COMPONENT
from homeassistant.config_entries import (
    SOURCE_USER,
    ConfigEntries,
    ConfigEntry,
    ConfigEntryState,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import (
    area_registry,
    device_registry,
    entity_registry,
    label_registry,
)

from custom_components.automower_ble_plus import (
    appdrive_panel,
    binary_sensor,
    button,
    calendar,
    device_tracker,
    diagnostics,
    maps_panel,
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
from custom_components.automower_ble_plus.connect_module import (
    async_request_connect_pairing_code,
)
from custom_components.automower_ble_plus.const import DOMAIN
from custom_components.automower_ble_plus.coordinator import MowerCoordinator
from custom_components.automower_ble_plus.features import I16, I32, U16, U32
from custom_components.automower_ble_plus.firmware import get_firmware_profile
from custom_components.automower_ble_plus.mapfiles import MapKind
from custom_components.automower_ble_plus.schedule import ScheduleTask
from custom_components.automower_ble_plus.transport import (
    CommandRejected,
    MowerConnectionError,
)
from custom_components.automower_ble_plus.wifi import encode_ssid
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
        if command == Command(4690, 3):
            self.values[Command(4690, 2), b""] = payload
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
async def test_planner_event_updates_named_restriction_and_keeps_raw_code(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    entry = _entry()
    entry.runtime_data = coordinator = MowerCoordinator(hass, entry)
    client = FakeAutomower420()
    coordinator.client = client
    reason = Command(4658, 0)
    next_start = Command(4658, 1)
    client.values[reason, b""] = b"\1"
    client.values[next_start, b""] = U32.pack(1800000000)
    client.values[Command(4658, 8), b""] = b""
    try:
        await coordinator._async_setup()
        coordinator.async_set_updated_data(await coordinator._async_update_data())
        entities = []
        await sensor.async_setup_entry(hass, entry, entities.extend)
        await coordinator.async_refresh_optional()
        detected = {entity.key: entity for entity in entities}
        named = detected["mowing_restriction"]
        raw = detected["restriction_reason"]
        assert named.native_value == "week_schedule"
        assert raw.native_value == 1
        assert (Command(4658, 8), b"") in client.calls
        coordinator._async_on_event(Event(4658, 2, b"\x00\x00"))
        assert coordinator._event_refresh_task is None
        client.values[reason, b""] = b"\x06"
        client.values[next_start, b""] = U32.pack(1800003600)
        coordinator._async_on_event(Event(4658, 2, b"\x06"))
        await coordinator._event_refresh_task
        assert named.native_value == "frost_guard"
        assert named.extra_state_attributes == {"code": 6}
        assert raw.native_value == 6
        assert coordinator.data["next_start"] == 1800003600
        client.values[reason, b""] = b"\x09"
        await coordinator.async_refresh_optional()
        assert named.native_value == "unknown"
        coordinator.firmware_profile = get_firmware_profile(
            (31, 1), "40.14_Main-App-P22_102.14"
        )
        assert named.native_value == "mission_abandoned"
        coordinator.firmware_profile = get_firmware_profile(
            (31, 1), "40.14_Main-App-P22_24.77"
        )
        assert named.native_value == "no_daylight"
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model_id", "node", "operation", "speed", "subscribes"),
    [
        ((5, 0), "App-G3_devicetype-5.0", 10, 36, True),
        ((28, 2), "40.3_Main-App-P16_29.47", 16, 31, True),
        ((5, 0), "App-G3_devicetype-5.0", 10, 36, False),
        ((255, 0), "Unknown", None, 32, False),
    ],
)
async def test_statistics_refresh_and_estimated_distance(
    tmp_path, monkeypatch, model_id, node, operation, speed, subscribes
):
    hass = HomeAssistant(str(tmp_path))
    entry = _entry()
    entry.runtime_data = coordinator = MowerCoordinator(hass, entry)
    client = FakeAutomower420()
    coordinator.client = client
    connected = True

    async def connect(_device):
        nonlocal connected
        connected = True
        return ResponseResult.OK

    monkeypatch.setattr(client, "connect", connect, raising=False)
    monkeypatch.setattr(client, "is_connected", lambda: connected)
    monkeypatch.setattr(client, "main_node_version", AsyncMock(return_value=node))
    monkeypatch.setattr(
        coordinator_module.bluetooth,
        "async_ble_device_from_address",
        lambda *args, **kwargs: object(),
    )
    family = 4726
    stats = Command(family, 0)
    subscription = Command(family, operation) if operation is not None else None
    client.values[Command(4698, 9), b""] = bytes(model_id)
    client.values[stats, b""] = b"\x00"
    if subscribes:
        client.values[subscription, b""] = b""
    try:
        await coordinator._async_setup()
        coordinator.async_set_updated_data(await coordinator._async_update_data())
        entities = []
        await sensor.async_setup_entry(hass, entry, entities.extend)
        await coordinator.async_refresh_optional()
        assert "estimated_travel_distance" not in {entity.key for entity in entities}
        client.values[stats, b""] = pack("<7I", 100, 81, 20, 19, 4, 3, 42)
        await coordinator.async_refresh_optional()
        sensors = {entity.key: entity for entity in entities}
        distance = sensors["estimated_travel_distance"]
        assert distance.native_value == speed
        assert distance.native_unit_of_measurement == "m"
        assert distance.state_class == "total_increasing"
        assert sensors["blade_usage_time"].native_value == 42

        await coordinator.async_refresh()
        stats_subscriptions = [
            command
            for command, _ in client.calls
            if command.family == family and command.operation in (10, 16)
        ]
        assert stats_subscriptions == ([subscription] if subscription else [])
        coordinator._async_on_event(Event(family, 1, bytes(28)))
        coordinator._async_on_event(Event(family, 0, b"\xff"))
        assert coordinator._event_refresh_task is None

        client.values[stats, b""] = pack("<7I", 200, 160, 30, 40, 6, 4, 1)
        coordinator._async_on_event(Event(family, 0, bytes(28)))
        if subscribes:
            await coordinator._event_refresh_task
        else:
            assert coordinator._event_refresh_task is None
            assert distance.native_value == speed
            await coordinator.async_refresh_optional()
        assert sensors["blade_usage_time"].native_value == 1
        assert sensors["collisions"].native_value == 6
        assert distance.native_value == 2 * speed

        client.values[stats, b""] = pack("<7I", 300, 260, 40, 40, 7, 5, 21)
        connected = False
        await coordinator.async_refresh()
        assert distance.native_value == 3 * speed
        assert client.calls.count((subscription, b"")) == (
            2 if subscribes else int(subscription is not None)
        )

        client.values[stats, b""] = pack("<7I", 0xFFFFFFFF, 260, 40, 40, 7, 5, 21)
        await coordinator.async_refresh_optional()
        assert not distance.available
    finally:
        await coordinator.async_shutdown()
        await hass.async_stop(force=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model_id", "firmware", "themes"),
    [
        ((5, 0), "App-G3_devicetype-5.0", ()),
        ((28, 2), "40.3_Main-App-P16_34.22", ()),
        ((24, 1), "40.4_Main-App-P1_999.0", ()),
        (
            (27, 1),
            "40.14_Main-App-P22_102.14",
            ("white", "solid_orange", "flashing_orange"),
        ),
        ((48, 1), "40.22_Main-App-P2Z_102.8", ("solid_orange", "flashing_orange")),
        (
            (39, 1),
            "40.2_Main-App-P3_102.8",
            ("white_and_red", "solid_orange", "flashing_orange"),
        ),
    ],
)
async def test_headlight_theme_uses_firmware_options_and_live_availability(
    tmp_path, monkeypatch, model_id, firmware, themes
):
    hass = HomeAssistant(str(tmp_path))
    entry = _entry()
    entry.runtime_data = coordinator = MowerCoordinator(hass, entry)
    client = FakeAutomower420()
    coordinator.client = client
    monkeypatch.setattr(client, "main_node_version", AsyncMock(return_value=firmware))
    availability = Command(4722, 0)
    current = Command(4722, 11)
    setter = Command(4722, 10)
    client.values.update(
        {
            (Command(4698, 9), b""): bytes(model_id),
            (availability, b""): b"\x02",
            (Command(4722, 2), b""): b"\x00",
            (current, b""): b"\x00",
        }
    )
    send = client.command
    writes = []
    save_change = True

    async def command(operation, payload=b""):
        if operation == setter:
            writes.append(payload)
            if save_change:
                client.values[current, b""] = payload
            return b""
        return await send(operation, payload)

    monkeypatch.setattr(client, "command", command)
    try:
        await coordinator._async_setup()
        coordinator.async_set_updated_data(await coordinator._async_update_data())
        entities = []
        await select.async_setup_entry(hass, entry, entities.extend)
        await coordinator.async_refresh_optional()
        assert not entities
        assert (current, b"") not in client.calls
        client.values[availability, b""] = b"\x01"
        client.values[current, b""] = b"\xff"
        await coordinator.async_refresh_optional()
        assert {entity.key for entity in entities} == {"headlight_schedule"}
        client.values[current, b""] = b"\x00"
        await coordinator.async_refresh_optional()
        detected = {entity.key: entity for entity in entities}
        if not themes:
            assert "headlight_theme" not in detected
            assert (current, b"") not in client.calls
            return
        editor = detected["headlight_theme"]
        assert tuple(editor.options) == themes
        assert editor.current_option == themes[0]
        await editor.async_select_option("flashing_orange")
        assert writes == [bytes([len(themes) - 1])]
        assert editor.current_option == "flashing_orange"
        save_change = False
        with pytest.raises(
            HomeAssistantError, match="did not confirm the headlight theme"
        ):
            await editor.async_select_option(themes[0])
        assert writes[-1] == b"\x00"
        assert editor.current_option == "flashing_orange"
        client.values[availability, b""] = b"\x00"
        with pytest.raises(
            HomeAssistantError, match="does not report available headlights"
        ):
            await editor.async_select_option("solid_orange")
        assert len(writes) == 2
        await coordinator.async_refresh_optional()
        assert not editor.available
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model_id", "firmware", "access"),
    [
        ((5, 0), "App-G3_devicetype-5.0", "unsupported"),
        ((27, 1), "40.14_Main-App-P22_102.14", "read"),
        ((28, 2), "40.3_Main-App-P16_81.4", "write"),
    ],
)
async def test_global_cutting_height_uses_reported_unit_and_live_range(
    tmp_path, monkeypatch, model_id, firmware, access
):
    hass = HomeAssistant(str(tmp_path))
    entry = _entry()
    entry.runtime_data = coordinator = MowerCoordinator(hass, entry)
    client = FakeAutomower420()
    coordinator.client = client
    monkeypatch.setattr(client, "main_node_version", AsyncMock(return_value=firmware))
    current = Command(4422, 28)
    setter = Command(4422, 29)
    maximum = Command(4422, 17)
    client.values.update(
        {
            (Command(4698, 9), b""): bytes(model_id),
            (Command(4422, 5), b""): b"\1",
            (Command(4422, 2), b""): b"\x05",
            (Command(4422, 15), b""): b"\x14",
            (maximum, b""): b"\x50",
            (current, b""): b"\x28",
        }
    )
    send = client.command
    writes = []

    async def command(operation, payload=b""):
        if operation == setter:
            writes.append(payload)
            client.values[current, b""] = b"\x3c" if payload == b"\x3d" else payload
            client.values[Command(4422, 2), b""] = b"\x08"
            return b""
        return await send(operation, payload)

    monkeypatch.setattr(client, "command", command)
    try:
        await coordinator._async_setup()
        coordinator.async_set_updated_data(await coordinator._async_update_data())
        numbers, sensors = [], []
        await number.async_setup_entry(hass, entry, numbers.extend)
        await sensor.async_setup_entry(hass, entry, sensors.extend)
        await coordinator.async_refresh_optional()
        editors = {entity.key: entity for entity in numbers}
        readings = {entity.key: entity for entity in sensors}
        key = "global_cutting_height_mm"
        assert editors["cutting_height"].native_value == 5
        if access != "write":
            assert key not in editors
            if access == "read":
                assert readings[key].native_value == 40
                assert readings[key].native_unit_of_measurement == "mm"
            else:
                assert key not in readings
                assert (current, b"") not in client.calls
            with pytest.raises(HomeAssistantError, match="does not report a writable"):
                await coordinator.async_set_global_height_mm(45)
            assert writes == []
            return
        assert key not in readings
        entity = editors[key]
        assert entity.native_unit_of_measurement == "mm"
        assert (
            entity.native_min_value,
            entity.native_max_value,
            entity.native_value,
        ) == (20, 80, 40)
        with pytest.raises(ServiceValidationError, match="whole number"):
            await entity.async_set_native_value(42.5)
        client.values[maximum, b""] = b"\x46"
        with pytest.raises(HomeAssistantError, match="between 20 and 70 mm"):
            await entity.async_set_native_value(75)
        assert writes == []
        await entity.async_set_native_value(62)
        assert writes == [b"\x3e"]
        assert entity.native_value == 62
        assert entity.native_max_value == 70
        assert editors["cutting_height"].native_value == 8
        with pytest.raises(HomeAssistantError, match="saved a cutting height of 60 mm"):
            await entity.async_set_native_value(61)
        assert entity.native_value == 60
        report = await diagnostics.async_get_config_entry_diagnostics(hass, entry)
        assert report["telemetry"][key] == {"minimum": 20, "maximum": 70, "value": 60}
        client.values[maximum, b""] = b"\x14"
        await coordinator.async_refresh_optional()
        assert not entity.available
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model_id", "firmware", "actual_height"),
    [
        ((5, 0), "App-G3_devicetype-5.0", None),
        ((28, 2), "40.3_Main-App-P16_81.4", (19, 42)),
        ((31, 1), "40.16_Main-App-P21NA_112.3", (33, 5)),
    ],
)
async def test_target_height_and_navigation_show_only_reported_telemetry(
    tmp_path, monkeypatch, model_id, firmware, actual_height
):
    hass = HomeAssistant(str(tmp_path))
    entry = _entry()
    entry.runtime_data = coordinator = MowerCoordinator(hass, entry)
    client = FakeAutomower420()
    coordinator.client = client
    monkeypatch.setattr(client, "main_node_version", AsyncMock(return_value=firmware))
    client.values.update(
        {
            (Command(4698, 9), b""): bytes(model_id),
            (Command(4422, 5), b""): b"\1",
            (Command(4422, 10), b""): b"\1",
            (Command(4422, 8), b""): b"\1",
            (Command(4422, 12), b""): b"\1",
            (Command(4732, 0), b""): bytes((actual_height is not None,)),
            (Command(4732, 2), b""): b"\1",
            (Command(4732, 3), b""): b"\2",
        }
    )
    if actual_height is not None:
        operation, level = actual_height
        client.values[Command(4422, operation), b""] = bytes((level,))
    try:
        await coordinator._async_setup()
        coordinator.async_set_updated_data(await coordinator._async_update_data())
        entities = []
        await sensor.async_setup_entry(hass, entry, entities.extend)
        await binary_sensor.async_setup_entry(hass, entry, entities.extend)
        await coordinator.async_refresh_optional()
        assert not any(entity.key == "target_height_active" for entity in entities)
        client.values[Command(4422, 12), b""] = pack("<BII", 1, 1800000000, 864000)
        await coordinator.async_refresh_optional()
        detected = {entity.key: entity for entity in entities}
        target = detected["target_height_active"]
        assert target.is_on
        assert target.extra_state_attributes == {
            "mower_start_time_seconds": 1800000000,
            "duration_seconds": 864000,
        }
        if actual_height is None:
            assert "gps_navigation_status" not in detected
            assert (Command(4732, 3), b"") not in client.calls
            assert not any(
                key.startswith("current_cutting_height_") for key in detected
            )
        else:
            assert detected["gps_navigation_status"].native_value == "map_in_use"
            key = (
                "current_cutting_height_percentage"
                if operation == 19
                else "current_cutting_height_level"
            )
            assert detected[key].native_value == level
            client.values[Command(4732, 3), b""] = b"\xff"
        client.values[Command(4422, 12), b""] = pack("<BII", 0, 1800000000, 864000)
        await coordinator.async_refresh_optional()
        assert target.is_on is False
        assert target.extra_state_attributes is None
        assert "gps_navigation_status" not in coordinator.data
        client.values[Command(4422, 10), b""] = b"\0"
        await coordinator.async_refresh_optional()
        assert not target.available
    finally:
        await hass.async_stop(force=True)


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
        "appdrive_undock",
        "appdrive_dock",
        "appdrive_stop",
    }
    assert not any(command.family == 5650 for command, _ in client.calls)
    assert not any(
        command.family == 5358 and command.operation != 8 for command, _ in client.calls
    )
    assert not coordinator.can_configure_wifi
    assert not coordinator.can_regenerate_aim_map
    assert coordinator.epos_origin_installation_id is None
    assert not coordinator.can_change_epos_boundary
    assert not any(key.startswith("aim_") for key in coordinator.data)
    assert all(
        command.operation == 2 for command, _ in client.calls if command.family == 5758
    )
    assert not any(
        command.family == 5916 and command.operation != 0 for command, _ in client.calls
    )
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
async def test_manual_clock_checks_live_mode_range_and_confirmation(monkeypatch):
    client = FakeAutomower420()
    read, write = Command(4690, 2), Command(4690, 3)
    mode = Command(6746, 7)
    original = datetime(2026, 11, 25, 23, 59, 58, tzinfo=UTC)
    client.values[read, b""] = U32.pack(int(original.timestamp()))
    for response, error in (
        (b"\x01", "Turn off automatic"),
        (b"\x02", "valid clock mode"),
    ):
        client.values[mode, b""] = response
        with pytest.raises(ValueError, match=error):
            await mower_clock.async_set_mower_clock_component(client, time(7, 30))
    client.values[mode, b""] = b"\x00"
    for invalid in (date(1969, 12, 31), date(2107, 1, 1)):
        with pytest.raises(ValueError, match="outside the mower's clock range"):
            await mower_clock.async_set_mower_clock_component(client, invalid)
    assert not any(command == write for command, _ in client.calls)
    send = client.command

    async def ignore_write(command, payload=b""):
        if command == write:
            return b""
        return await send(command, payload)

    monkeypatch.setattr(client, "command", ignore_write)
    with pytest.raises(ValueError, match="did not confirm the new local time"):
        await mower_clock.async_set_mower_clock_component(client, time(7, 30))


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
    in_station = Command(4692, 27)
    dock_by_wire = Command(6170, 11)
    set_dock_by_wire = Command(6170, 12)
    client.values.update(
        {
            (Command(6170, 1), b""): b"\x01",
            (enabled, b""): b"\x01",
            (Command(6170, 7), b""): b"\x01",
            (wire_assistance, b""): b"\0",
            (Command(6170, 10), b""): b"\0",
            (dock_by_wire, b""): b"\0",
            (in_station, b""): b"\0",
            (Command(4692, 23), b""): U32.pack(0),
            (Command(4692, 24), b""): U32.pack(1),
            (Command(5336, 3), b""): b"\x02",
            (Command(5650, 4), b""): b"\x50",
            (Command(5650, 17), b""): b"\x01",
            (Command(5242, 0), b""): pack("<iii", 3000, -4000, 40),
            (Command(5578, 52), b""): b"\x02",
            (Command(6778, 1), b""): b"",
            (Command(6778, 2), b""): b"",
            (Command(6170, 0), b""): b"",
        }
    )
    send = client.command
    docking_writes = []

    async def command(operation, payload=b""):
        if operation == Command(6170, 9):
            assert payload == b"\x01"
            client.values[wire_assistance, b""] = payload
            return b""
        if operation == set_dock_by_wire:
            client.values[dock_by_wire, b""] = payload
            docking_writes.append(payload)
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
    selectors = []
    await select.async_setup_entry(coordinator.hass, entry, selectors.extend)
    await coordinator.async_refresh_optional()
    assert entities["epos_enabled"].is_on
    assert "epos_dock_by_wire" not in entities
    assert not selectors
    assert (Command(6170, 11), b"") not in client.calls
    assert entities["epos_rtk_solution"].native_value == "fixed"
    assert entities["epos_installation_state"].native_value == "idle"
    assert entities["epos_installation_result"].native_value == "success"
    assert entities["epos_reference_station_channel"].native_value == "wifi"
    assert entities["epos_correction_source"].native_value == "local_reference_station"
    assert entities["epos_boundary_mode"].native_value == "virtual"
    assert entities["site_map_installation_type"].native_value == "residential"
    assert entities["epos_reference_station_signal"].native_value == 80
    assert entities["epos_reference_station_distance"].native_value == 5000
    assert "epos_cloud_signal" not in entities
    assert not entities["epos_wire_assistance"].is_on
    await entities["epos_wire_assistance"].async_turn_on()
    assert entities["epos_wire_assistance"].is_on
    await coordinator._event_refresh_task

    client.values[cloud, b""] = b"\x01"
    client.values[Command(6778, 11), b""] = b"\x42"
    client.values[Command(6778, 15), b""] = b"\x01"
    client.values[Command(6778, 16), b""] = b"\0"
    client.values[Command(6778, 19), b""] = b"\x03"
    client.values[Command(6778, 7), b""] = b"\x04"
    client.values[Command(6778, 29), b""] = b"\x01"
    client.values[Command(6170, 10), b""] = b"\x01"
    client.values[Command(4692, 23), b""] = b"\x00"
    client.calls.clear()
    await coordinator.async_refresh_optional()
    assert (Command(5650, 4), b"") not in client.calls
    assert not entities["epos_reference_station_signal"].available
    assert not entities["epos_installation_state"].available
    assert entities["epos_cloud_signal"].native_value == 66
    assert entities["epos_correction_source"].native_value == "husqvarna_cloud"
    assert entities["epos_boundary_mode"].native_value == "virtual_assisted_by_wire"
    assert entities["epos_cloud_connection_state"].native_value == "connected"
    assert entities["epos_cloud_service_state"].native_value == "no_position"
    assert entities["epos_cloud_correction_format"].native_value == "rtcm"
    assert entities["epos_cloud_has_origin"].is_on
    assert not entities["epos_cloud_origin_valid"].is_on
    (method,) = selectors
    assert method.current_option == "virtual"
    with pytest.raises(HomeAssistantError, match="charging station first"):
        await method.async_select_option("guide_wire")
    assert not docking_writes
    client.values[in_station, b""] = b"\x01"
    await method.async_select_option("guide_wire")
    assert docking_writes == [b"\x01"]
    assert method.current_option == "guide_wire"
    assert entities["epos_dock_by_wire"].is_on
    coordinator._debounced_refresh.async_cancel()
    client.values[Command(6778, 19), b""] = b"\x02"
    coordinator._async_on_event(Event(6778, 7, b"\x02"))
    await coordinator._event_refresh_task
    assert (Command(6778, 2), b"") in client.calls
    assert entities["epos_cloud_connection_state"].native_value == "connecting"

    client.values[enabled, b""] = b"\0"
    await coordinator.async_refresh_optional()
    assert not entities["epos_enabled"].is_on
    assert not entities["epos_cloud_signal"].available
    assert not entities["epos_rtk_solution"].available
    assert not method.available
    assert entities["epos_boundary_mode"].native_value == "boundary_wire"
    await coordinator.async_shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("manoeuvre", "outcome"),
    [
        (manoeuvre, outcome)
        for manoeuvre in ("dock", "undock")
        for outcome in ("complete", "stop_requested", "failed", "unconfirmed")
    ]
    + [("dock", "charging_only")],
)
async def test_appdrive_manoeuvre_checks_firmware_and_tracks_its_outcome(
    tmp_path, monkeypatch, manoeuvre, outcome
):
    entry = _entry()
    coordinator = MowerCoordinator(HomeAssistant(str(tmp_path)), entry)
    client = FakeAutomower420()
    coordinator.client = client
    entry.runtime_data = coordinator
    remote_control = Command(5100, 12)
    mower_state = Command(4586, 2)
    docked = Command(4692, 27)
    appdrive_state = Command(5358, 0)
    request = Command(5358, 2 if manoeuvre == "dock" else 3)
    possible = Command(5358, 4 if manoeuvre == "dock" else 5)
    stop = Command(5358, 7)
    starts_in_station = manoeuvre == "undock"
    completion = outcome in ("complete", "charging_only")
    client.values.update(
        {
            (mower_state, b""): b"\x05",
            (remote_control, b""): b"\0",
            (docked, b""): bytes([starts_in_station]),
            (appdrive_state, b""): b"\0",
            (request, b""): b"",
            (possible, b""): b"\x01",
            (Command(5358, 8), b""): b"\x01",
            (Command(5358, 10), b""): b"\0",
            (stop, b""): b"",
        }
    )
    if outcome == "charging_only":
        del client.values[(docked, b"")]
    send = client.command

    async def command(operation, payload=b""):
        result = await send(operation, payload)
        if operation == request:
            client.values[appdrive_state, b""] = {
                "complete": b"\x01",
                "charging_only": b"\x00",
                "stop_requested": b"\x02",
                "failed": b"\x03",
                "unconfirmed": b"\x02",
            }[outcome]
            if outcome == "charging_only":
                client.values[Command(4586, 3), b""] = b"\x01"
            elif completion:
                client.values[docked, b""] = bytes([not starts_in_station])
        elif operation == stop:
            client.values[appdrive_state, b""] = b"\0"
        return result

    monkeypatch.setattr(client, "command", command)
    monkeypatch.setattr(coordinator, "async_request_refresh", AsyncMock())
    await coordinator._async_setup()
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    entities = {}

    def add_entities(new_entities):
        entities.update((entity.key, entity) for entity in new_entities)

    for platform in (binary_sensor, button, sensor):
        await platform.async_setup_entry(coordinator.hass, entry, add_entities)
    await coordinator.async_refresh_optional()
    command_button = entities[f"appdrive_{manoeuvre}"]
    stop_button = entities["appdrive_stop"]
    assert entities["appdrive_activity"].native_value == "none"
    assert not entities["appdrive_remote_control"].is_on
    assert not command_button.available
    assert not stop_button.available
    with pytest.raises(HomeAssistantError, match="Enable remote control"):
        await command_button.async_press()
    client.values[remote_control, b""] = b"\x01"
    await coordinator.async_refresh_optional()
    assert command_button.available
    client.values[mower_state, b""] = b"\x07"
    with pytest.raises(HomeAssistantError, match="Pause the mower"):
        await command_button.async_press()
    client.values[mower_state, b""] = b"\x05"
    client.values[possible, b""] = b"\0"
    with pytest.raises(HomeAssistantError, match="does not currently permit"):
        await command_button.async_press()
    assert (request, b"") not in client.calls
    client.values[possible, b""] = b"\x01"
    if outcome == "unconfirmed":
        monkeypatch.setattr(coordinator_module, "APPDRIVE_MANOEUVRE_TIMEOUT", 0)
    if outcome == "charging_only":
        client.values[(mower_state, b"")] = b"\x06"
        client.values[(appdrive_state, b"")] = b"\x01"
    await command_button.async_press()
    result = entities[f"appdrive_{manoeuvre}_status"]
    assert result.native_value == "in_progress"
    assert not command_button.available
    assert stop_button.available
    assert client.calls.count((request, b"")) == 1
    if outcome == "stop_requested":
        with pytest.raises(HomeAssistantError, match="already in progress"):
            await command_button.async_press()
        opposite = next(
            candidate
            for candidate in coordinator_module.APPDRIVE_MANOEUVRES
            if candidate.name != manoeuvre
        )
        with pytest.raises(HomeAssistantError, match="already in progress"):
            await coordinator.async_start_appdrive_manoeuvre(opposite)
        await stop_button.async_press()
        assert client.calls.count((stop, b"")) == 1
    else:
        await coordinator._appdrive_manoeuvre_task
        assert (stop, b"") not in client.calls
    assert result.native_value == ("complete" if completion else outcome)
    assert coordinator.appdrive_manoeuvre is None
    if outcome == "complete":
        assert entities["appdrive_state"].native_value == "in_operation"
        assert entities["in_charging_station"].is_on == (not starts_in_station)
    await coordinator.async_shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", ["release", "expire", "abort", "disconnect"])
async def test_appdrive_joystick_requires_a_live_lease_and_stops(
    tmp_path, monkeypatch, finish
):
    entry = _entry()
    coordinator = MowerCoordinator(HomeAssistant(str(tmp_path)), entry)
    client = coordinator.client = FakeAutomower420()
    entry.runtime_data = coordinator
    drive = Command(5358, 1)
    abort = Command(5358, 7)
    client.values.update(
        {
            (Command(5358, 8), b""): b"\x01",
            (Command(5358, 0), b""): b"\0",
            (Command(4586, 2), b""): b"\x05",
            (Command(5100, 12), b""): b"\x01",
            (Command(4692, 27), b""): b"\0",
        }
    )
    monkeypatch.setattr(coordinator_module, "APPDRIVE_INPUT_TIMEOUT", 0.15)
    connected = True
    sent = []
    neutral = b"\0" * 4
    stopped = asyncio.Event()
    send = client.command
    monkeypatch.setattr(client, "is_connected", lambda: connected)

    async def command(operation, payload=b"", **kwargs):
        if operation in (drive, abort):
            sent.append((operation, payload))
            if operation == drive and payload == neutral and finish == "abort":
                raise CommandRejected(operation, ResponseResult.NOT_ALLOWED)
            if payload == neutral or operation == abort:
                stopped.set()
            return b""
        return await send(operation, payload)

    monkeypatch.setattr(client, "command", command)
    await coordinator._async_setup()
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    owner = object()
    token = await coordinator.async_begin_appdrive_control(owner)
    assert not sent
    with pytest.raises(HomeAssistantError, match="already in use"):
        await coordinator.async_begin_appdrive_control(object())
    with pytest.raises(HomeAssistantError, match="control has ended"):
        await coordinator.async_drive_appdrive(object(), token, 1, 0)
    await coordinator.async_drive_appdrive(owner, token, 0.5, -0.5)
    assert sent == [(drive, pack("<hh", 25, 180))]
    if finish == "expire":
        await asyncio.wait_for(stopped.wait(), 1)
    elif finish == "disconnect":
        connected = False
        with pytest.raises(HomeAssistantError, match="stop was not confirmed"):
            await coordinator.async_release_appdrive_control(owner, token)
        with pytest.raises(HomeAssistantError, match="stop was not confirmed"):
            await coordinator.async_release_appdrive_control(owner, token)
    else:
        await coordinator.async_release_appdrive_control(owner, token)
    if finish != "disconnect":
        assert sent[1] == (drive, neutral)
        assert stopped.is_set()
    if finish == "abort":
        assert sent[2] == (abort, b"")
    with pytest.raises(HomeAssistantError, match="control has ended"):
        await coordinator.async_drive_appdrive(owner, token, 1, 0)
    await coordinator.async_shutdown()


@pytest.mark.asyncio
async def test_appdrive_panel_permissions_and_websocket_disconnect(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    hass.config.components.add("frontend")
    hass.http = Mock(async_register_static_paths=AsyncMock())
    hass.config_entries = ConfigEntries(hass, {})
    await hass.config_entries.async_initialize()
    device_registry.async_setup(hass)
    await asyncio.gather(
        area_registry.async_load(hass),
        device_registry.async_load(hass),
        entity_registry.async_load(hass),
        label_registry.async_load(hass),
    )
    await appdrive_panel.async_setup_appdrive_panel(hass)
    entry = _entry()
    coordinator = MowerCoordinator(hass, entry)
    entry.runtime_data = coordinator
    coordinator.async_set_updated_data({"appdrive_state": 0})
    entity = entity_registry.async_get(hass).async_get_or_create(
        "lawn_mower", DOMAIN, f"{coordinator.address}_mower"
    )
    appdrive_panel.async_attach_appdrive_panel(hass, entry)
    await hass.async_block_till_done()
    assert frontend.async_panel_exists(hass, "automower-appdrive")
    paths = hass.http.async_register_static_paths.await_args.args[0]
    assert Path(paths[0].path).is_file()

    coordinator.async_begin_appdrive_control = AsyncMock(return_value="unit-lease")
    coordinator.async_drive_appdrive = AsyncMock()
    coordinator.async_release_appdrive_control = AsyncMock()
    messages = []

    def record(message):
        messages.append(
            json.loads(message) if isinstance(message, (str, bytes)) else message
        )

    user = Mock()
    user.permissions.check_entity.return_value = False
    connection = websocket_api.ActiveConnection(Mock(), hass, record, user, None, None)
    connection.async_handle({"id": 1, "type": f"{DOMAIN}/appdrive_list"})
    assert messages[-1]["result"] == []
    user.permissions.check_entity.return_value = True
    connection.async_handle({"id": 2, "type": f"{DOMAIN}/appdrive_list"})
    assert messages[-1]["result"] == [{"entry_id": entry.entry_id, "name": entry.title}]
    assert user.permissions.check_entity.call_args.args[0] == entity.entity_id
    connection.async_handle(
        {"id": 3, "type": f"{DOMAIN}/appdrive_start", "entry_id": entry.entry_id}
    )
    assert messages[-1]["event"] == {"token": "unit-lease"}
    connection.async_handle(
        {
            "id": 4,
            "type": f"{DOMAIN}/appdrive_input",
            "entry_id": entry.entry_id,
            "token": "unit-lease",
            "forward": 0.5,
            "steering": -0.2,
        }
    )
    coordinator.async_drive_appdrive.assert_awaited_once_with(
        connection, "unit-lease", 0.5, -0.2
    )
    connection.async_handle_close()
    await hass.async_block_till_done()
    coordinator.async_release_appdrive_control.assert_awaited_once_with(
        connection, "unit-lease"
    )
    coordinator.async_set_updated_data({})
    assert not frontend.async_panel_exists(hass, "automower-appdrive")


@pytest.mark.asyncio
async def test_maps_panel_tracks_map_capabilities_and_permissions(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    hass.config.components.add("frontend")
    hass.http = Mock(async_register_static_paths=AsyncMock())
    hass.config_entries = ConfigEntries(hass, {})
    await hass.config_entries.async_initialize()
    device_registry.async_setup(hass)
    await asyncio.gather(
        area_registry.async_load(hass),
        device_registry.async_load(hass),
        entity_registry.async_load(hass),
        label_registry.async_load(hass),
    )
    await maps_panel.async_setup_maps_panel(hass)
    assert Path(
        hass.http.async_register_static_paths.await_args.args[0][0].path
    ).is_file()
    entry = _entry()
    entry.runtime_data = coordinator = MowerCoordinator(hass, entry)
    coordinator.firmware_profile = get_firmware_profile((5, 0), "App-G3_devicetype-5.0")
    coordinator.async_set_updated_data({})
    registry = entity_registry.async_get(hass)
    entity = registry.async_get_or_create(
        "lawn_mower", DOMAIN, f"{coordinator.address}_mower"
    )
    maps_panel.async_attach_maps_panel(hass, entry)
    await hass.async_block_till_done()
    assert not frontend.async_panel_exists(hass, "automower-maps")

    coordinator.firmware_profile = get_firmware_profile(
        (28, 2), "40.3_Main-App-P16_81.4"
    )
    coordinator.async_set_updated_data(
        {"map_generated_preview": "generation-1", "aim_enabled": 1, "aim_map_state": 2}
    )
    await hass.async_block_till_done()
    assert frontend.async_panel_exists(hass, "automower-maps")
    messages = []

    def record(message):
        messages.append(
            json.loads(message) if isinstance(message, (str, bytes)) else message
        )

    user = Mock()
    user.permissions.check_entity.return_value = False
    connection = websocket_api.ActiveConnection(Mock(), hass, record, user, None, None)
    connection.async_handle({"id": 1, "type": f"{DOMAIN}/maps_list"})
    assert messages[-1]["result"] == []
    user.permissions.check_entity.return_value = True
    connection.async_handle({"id": 2, "type": f"{DOMAIN}/maps_list"})
    assert messages[-1]["result"] == [
        {
            "entity_id": entity.entity_id,
            "name": entry.title,
            "maps": ["generated", "zones"],
        }
    ]
    coordinator.async_set_updated_data({})
    connection.async_handle({"id": 3, "type": f"{DOMAIN}/maps_list"})
    assert messages[-1]["result"] == []
    assert not frontend.async_panel_exists(hass, "automower-maps")
    await hass.async_stop(force=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("initial", "outcome"),
    [
        (0, "confirmed"),
        (1, "confirmed"),
        (0, "changed_site"),
        (0, "lost_ack"),
        (0, "rejected"),
    ],
)
async def test_epos_boundary_switch_from_home_assistant(
    tmp_path, monkeypatch, initial, outcome
):
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
    entry = _entry()
    client = FakeAutomower420()
    monkeypatch.setattr(coordinator_module, "AutomowerClient", lambda *args: client)
    monkeypatch.setattr(
        client, "main_node_version", AsyncMock(return_value="40.16_Main-App-P21NA_79.3")
    )
    reload_entry = Mock(wraps=hass.config_entries.async_schedule_reload)
    monkeypatch.setattr(hass.config_entries, "async_schedule_reload", reload_entry)
    mode = Command(6170, 2)
    change = Command(6170, 3)
    site = Command(5900, 12)
    target = bytes((1 - initial,))
    client.values.update(
        {
            (Command(4698, 9), b""): bytes((31, 1)),
            (Command(6170, 1), b""): b"\1",
            (mode, b""): bytes((initial,)),
            (change, target): b"",
            (Command(5900, 17), b""): b"\1",
            (site, b""): U32.pack(17),
        }
    )
    send = client.command

    async def command(operation, payload=b""):
        reply = await send(operation, payload)
        if operation == change:
            if outcome == "rejected":
                raise CommandRejected(operation, ResponseResult.INVALID_ID)
            client.values[mode, b""] = payload
            if outcome == "lost_ack":
                raise MowerConnectionError("Disconnected before acknowledgement")
        return reply

    monkeypatch.setattr(client, "command", command)
    try:
        async with asyncio.timeout(15):
            assert await setup.async_setup_component(hass, DOMAIN, {})
            await hass.config_entries.async_add(entry)
            await hass.async_block_till_done()
            coordinator = entry.runtime_data
            await coordinator.async_refresh_optional()
            options = hass.config_entries.options
            flow = await options.async_init(entry.entry_id)
            assert "change_epos_boundary" in flow["menu_options"]
            flow_id = flow["flow_id"]
            await options.async_configure(
                flow_id, {"next_step_id": "change_epos_boundary"}
            )
            selection = "epos" if target == b"\1" else "boundary_wire"
            flow = await options.async_configure(
                flow_id, {"epos_boundary": selection, "confirm_epos_boundary": False}
            )
            assert flow["errors"] == {
                "confirm_epos_boundary": "epos_boundary_confirmation_required"
            }
            assert not any(operation == change for operation, _ in client.calls)
            if outcome == "changed_site":
                client.values[site, b""] = U32.pack(18)
            flow = await options.async_configure(
                flow_id, {"epos_boundary": selection, "confirm_epos_boundary": True}
            )
            assert flow["type"] == FlowResultType.ABORT
            assert client.calls.count((change, target)) == (outcome != "changed_site")
            if outcome == "confirmed":
                assert flow["reason"] == "epos_boundary_changed"
                reload_entry.assert_called_once_with(entry.entry_id)
                assert not coordinator.can_change_epos_boundary
                await hass.async_block_till_done()
                assert entry.runtime_data is not coordinator
                assert entry.runtime_data.data["epos_enabled"] == target[0]
            elif outcome == "lost_ack":
                assert flow["reason"] == "epos_boundary_unconfirmed"
                reload_entry.assert_called_once_with(entry.entry_id)
                assert not coordinator.can_change_epos_boundary
            else:
                assert flow["reason"] == "epos_boundary_failed"
                reload_entry.assert_not_called()
                assert coordinator.can_change_epos_boundary == (
                    outcome == "changed_site"
                )
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["valid", "changed", "timeout", "lost_ack"])
async def test_set_up_epos_cloud_origin(tmp_path, monkeypatch, outcome):
    hass = HomeAssistant(str(tmp_path))
    entry = _entry()
    coordinator = MowerCoordinator(hass, entry)
    client = FakeAutomower420()
    coordinator.client = client
    entry.runtime_data = coordinator
    legacy = outcome == "lost_ack"
    version = "40.10_Main-App-P14.1_24.71" if legacy else "40.14_Main-App-P22_102.14"
    monkeypatch.setattr(client, "main_node_version", AsyncMock(return_value=version))
    monkeypatch.setattr(coordinator_module, "_EPOS_ORIGIN_POLL_SECONDS", 0.001)
    monkeypatch.setattr(coordinator_module, "_EPOS_ORIGIN_TIMEOUT", 0.1)

    cloud = 6778
    enable = (Command(cloud, 5), b"\1")
    set_origin = (Command(cloud, 18), b"")
    readiness = (Command(cloud, 17), b"")
    site = (Command(5900, 12), b"")
    map_available = (Command(5578, 40), b"")
    client.values.update(
        {
            (Command(cloud, 3 if legacy else 23), b""): b"\1",
            (Command(cloud, 4), b""): b"\0",
            (Command(cloud, 15), b""): b"\0",
            (Command(cloud, 16), b""): b"\0",
            readiness: b"\0",
            enable: b"",
            set_origin: b"",
            (Command(5900, 17), b""): b"\1",
            site: U32.pack(71),
            map_available: b"\1",
            (Command(5578, 38), b""): b"\0",
            (Command(5578, 43), b""): b"\0",
        }
    )
    if not legacy:
        client.values[Command(6170, 1), b""] = b"\1"
        client.values[Command(6170, 2), b""] = b"\1"
    send = client.command

    async def command(operation, payload=b""):
        request = (operation, payload)
        reply = await send(operation, payload)
        if request == enable:
            client.values[Command(cloud, 4), b""] = b"\1"
        elif request == readiness and outcome != "timeout":
            if outcome == "changed":
                client.values[site] = U32.pack(72)
            client.values[readiness] = b"\1"
        elif request == set_origin:
            client.values[Command(cloud, 15), b""] = b"\1"
            client.values[Command(cloud, 16), b""] = b"\1"
            if outcome == "lost_ack":
                raise MowerConnectionError("No response after the origin request")
        return reply

    monkeypatch.setattr(client, "command", command)
    await coordinator._async_setup()
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    await coordinator.async_refresh_optional()
    assert coordinator.epos_origin_installation_id is None
    with pytest.raises(HomeAssistantError, match="unavailable"):
        await coordinator.async_setup_epos_origin(71)
    assert enable not in client.calls
    client.values[map_available] = b"\0"
    await coordinator.async_refresh_optional()
    assert coordinator.epos_origin_installation_id == 71

    if outcome == "changed":
        with pytest.raises(HomeAssistantError, match="active installation changed"):
            await coordinator.async_setup_epos_origin(71)
    elif outcome == "timeout":
        with pytest.raises(TimeoutError):
            await coordinator.async_setup_epos_origin(71)
    else:
        assert await coordinator.async_setup_epos_origin(71)
        assert coordinator.data["epos_cloud_origin_valid"] == 1
        assert coordinator.epos_origin_installation_id is None
        assert client.calls.index(enable) < client.calls.index(set_origin)
        await coordinator.async_refresh_optional()
        assert coordinator.data["epos_cloud_origin_valid"] == 1
    assert client.calls.count(enable) == 1
    assert client.calls.count(set_origin) == (outcome in ("valid", "lost_ack"))
    await coordinator.async_shutdown()


@pytest.mark.asyncio
async def test_epos_origin_options_require_confirmation_and_can_cancel(
    tmp_path, monkeypatch
):
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
    entry = _entry()
    client = FakeAutomower420()
    monkeypatch.setattr(coordinator_module, "AutomowerClient", lambda *args: client)
    try:
        async with asyncio.timeout(15):
            assert await setup.async_setup_component(hass, DOMAIN, {})
            await hass.config_entries.async_add(entry)
            await hass.async_block_till_done()
            coordinator = entry.runtime_data
            options = hass.config_entries.options
            flow = await options.async_init(entry.entry_id)
            assert flow["step_id"] == "reset"
            options.async_abort(flow["flow_id"])
            coordinator.async_set_updated_data(
                {**coordinator.data, "epos_origin_installation_id": 71}
            )
            pending = asyncio.Event()
            cancelled = asyncio.Event()

            async def wait_for_origin(site_id):
                assert site_id == 71
                try:
                    await pending.wait()
                finally:
                    cancelled.set()

            operation = AsyncMock(side_effect=wait_for_origin)
            monkeypatch.setattr(coordinator, "async_setup_epos_origin", operation)
            flow = await options.async_init(entry.entry_id)
            assert "setup_epos_origin" in flow["menu_options"]
            flow_id = flow["flow_id"]
            await options.async_configure(
                flow_id, {"next_step_id": "setup_epos_origin"}
            )
            flow = await options.async_configure(
                flow_id, {"confirm_epos_origin": False}
            )
            assert flow["errors"] == {
                "confirm_epos_origin": "epos_origin_confirmation_required"
            }
            operation.assert_not_called()
            flow = await options.async_configure(flow_id, {"confirm_epos_origin": True})
            assert flow["type"] == FlowResultType.SHOW_PROGRESS
            options.async_abort(flow_id)
            await cancelled.wait()
            operation.assert_awaited_once_with(71)
    finally:
        await hass.async_stop(force=True)


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
    ("version", "pin", "writer", "expected_payload"),
    [
        ("App-G3_devicetype-5.0", None, None, None),
        ("App-G3_devicetype-5.0", "0042", Command(4738, 1), b"\x00\x2a\x00"),
        ("40.1_Main-App-P25_109.3", None, Command(4738, 17), b"\x00"),
    ],
)
async def test_connect_module_controls_follow_module_and_pin_capabilities(
    tmp_path, monkeypatch, version, pin, writer, expected_payload
):
    entry = _entry(pin)
    coordinator = MowerCoordinator(HomeAssistant(str(tmp_path)), entry)
    client = FakeAutomower420()
    coordinator.client = client
    entry.runtime_data = coordinator
    available = Command(4738, 0)
    enabled = Command(4738, 2)
    client.values.update(
        {
            (available, b""): b"\x01",
            (enabled, b""): b"\x01",
            (Command(4738, 7), b""): b"\x03",
            (Command(4738, 8), b""): b"\x32",
        }
    )
    monkeypatch.setattr(client, "main_node_version", AsyncMock(return_value=version))
    send = client.command
    writes = []

    async def command(operation, payload=b""):
        if operation in (Command(4738, 1), Command(4738, 17)):
            writes.append((operation, payload))
            client.values[(enabled, b"")] = payload[:1]
            return b""
        return await send(operation, payload)

    monkeypatch.setattr(client, "command", command)
    await coordinator._async_setup()
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    entities = []
    for platform in (sensor, binary_sensor, switch):
        await platform.async_setup_entry(coordinator.hass, entry, entities.extend)
    await coordinator.async_refresh_optional()
    detected = {
        entity.key: entity
        for entity in entities
        if entity.key.startswith("automower_connect_")
    }
    assert detected["automower_connect_state"].native_value == "sim_card_error"
    assert detected["automower_connect_signal_strength"].native_value == 50
    control = detected["automower_connect_enabled"]
    assert control.is_on
    if writer is None:
        assert isinstance(control, binary_sensor.DiagnosticStatusSensor)
        assert not writes
    else:
        assert isinstance(control, switch.AutomowerSwitch)
        await control.async_turn_off()
        assert not control.is_on
        assert writes == [(writer, expected_payload)]
    client.values[(available, b"")] = b"\0"
    await coordinator.async_refresh_optional()
    assert all(not entity.available for entity in detected.values())
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
async def test_gps_tracker_handles_reports_loss_of_fix_and_reconnect(
    tmp_path, monkeypatch
):
    hass = HomeAssistant(str(tmp_path))
    entry = _entry()
    coordinator = MowerCoordinator(hass, entry)
    client = FakeAutomower420()
    coordinator.client = client
    entry.runtime_data = coordinator
    gps = Command(5264, 0)
    subscribe = Command(5264, 1)
    pose = Command(5242, 5)
    # Legacy firmware can respond to GetPosition without a GetAvailable method.
    client.values.update(
        {
            (gps, b""): pack("<ii", 0, 0),
            (subscribe, b""): b"",
            (pose, b""): pack("<iiihiih", 0, 0, 0, 1812, 14, -1, 12),
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
        coordinator_module.bluetooth,
        "async_ble_device_from_address",
        lambda *args, **kwargs: object(),
    )
    await coordinator._async_setup()
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    trackers = []
    sensors = []
    await device_tracker.async_setup_entry(hass, entry, trackers.extend)
    await sensor.async_setup_entry(hass, entry, sensors.extend)
    await coordinator.async_refresh_optional()
    (tracker,) = trackers
    assert tracker.unique_id.endswith("_latitude")
    assert not tracker.available
    assert (subscribe, b"") not in client.calls
    lookup = {entity.key: entity for entity in sensors}
    assert lookup["local_heading"].native_value == 181.2
    assert lookup["local_position_accuracy"].native_value == 14
    assert lookup["local_heading_accuracy"].native_value == 1.2

    monkeypatch.setattr(tracker, "async_write_ha_state", Mock())
    await tracker.async_added_to_hass()
    assert client.calls.count((subscribe, b"")) == 1
    assert coordinator._gps_report_task is None
    coordinator._async_on_event(Event(5264, 0, b"\x01"))
    assert coordinator._gps_report_task is None
    original_timer = coordinator._unsub_refresh
    coordinator._async_on_event(Event(5264, 0, pack("<ii", -22_500_000, 520_000_000)))
    await coordinator._gps_report_task
    assert coordinator._unsub_refresh is original_timer
    assert tracker.available
    assert (tracker.latitude, tracker.longitude) == (52.0, -2.25)
    coordinator._async_on_event(Event(5264, 0, pack("<ii", 0, 0)))
    await coordinator._gps_report_task
    assert not tracker.available
    assert tracker.latitude is None and tracker.longitude is None

    connected = False
    client.values[gps, b""] = pack("<ii", 22_500_000, 519_000_000)
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    assert client.calls.count((subscribe, b"")) == 2
    assert (tracker.latitude, tracker.longitude) == (51.9, 2.25)
    coordinator.async_set_gps_tracking(False)
    latest_task = coordinator._gps_report_task
    coordinator._async_on_event(Event(5264, 0, pack("<ii", 22_500_000, 530_000_000)))
    assert coordinator._gps_report_task is latest_task
    assert tracker.latitude == 51.9
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
        client, "main_node_version", AsyncMock(return_value="40.1_Main-App-P25_32.22")
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
            (Command(5264, 4), b""): b"\x01",
            (Command(5264, 0), b""): pack("<ii", 22_500_000, 520_000_000),
            (Command(5242, 5), b""): pack("<iiihiih", 910, -1200, 45, -900, 20, 30, 11),
            (Command(5242, 11), b""): pack("<ii", 22_499_900, 519_999_900),
            (Command(4738, 0), b""): b"\x01",
            (Command(4738, 2), b""): b"\x01",
            (Command(4738, 10), b""): b"\x01\x44",
            (Command(4738, 12), b""): b"",
            (Command(4738, 13), b""): b"",
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
            assert not hass.states.async_entity_ids("image")
            radio_state = "sensor.automower_420_automower_connect_status"
            assert hass.states.get(radio_state).state == "connected"
            client.values[(Command(4738, 10), b"")] = b"\x04\x00"
            entry.runtime_data._async_on_event(Event(4738, 2, b"\x04\x00"))
            await entry.runtime_data._event_refresh_task
            assert hass.states.get(radio_state).state == "no_sim_card"
            assert (Command(4738, 13), b"") in client.calls
            entry.runtime_data._debounced_refresh.async_cancel()
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
            clock_address = entry.runtime_data.address
            date_id = registry.async_get_entity_id(
                "date", DOMAIN, f"{clock_address}_mower_local_date"
            )
            time_id = registry.async_get_entity_id(
                "time", DOMAIN, f"{clock_address}_mower_local_time"
            )
            observed = datetime.fromtimestamp(1_800_000_000, UTC)
            assert hass.states.get(date_id).state == observed.date().isoformat()
            assert hass.states.get(time_id).state == observed.time().isoformat()
            # The time on the mower changed since the latest notification.
            fresh = datetime(2026, 12, 1, 6, 0, 2, tzinfo=UTC)
            client.values[Command(4690, 2), b""] = U32.pack(int(fresh.timestamp()))
            await hass.services.async_call(
                "date",
                "set_value",
                {"entity_id": date_id, "date": "2026-12-02"},
                blocking=True,
            )
            assert hass.states.get(time_id).state == "06:00:02"
            await hass.services.async_call(
                "time",
                "set_value",
                {"entity_id": time_id, "time": "07:30:15"},
                blocking=True,
            )
            assert hass.states.get(date_id).state == "2026-12-02"
            assert hass.states.get(time_id).state == "07:30:15"
            assert client.values[Command(4690, 2), b""] == U32.pack(
                int(datetime(2026, 12, 2, 7, 30, 15, tzinfo=UTC).timestamp())
            )
            tracker_id = registry.async_get_entity_id(
                "device_tracker", DOMAIN, f"{entry.runtime_data.address}_latitude"
            )
            assert registry.async_get(tracker_id).disabled
            assert (Command(5264, 1), b"") not in client.calls
            position = await hass.services.async_call(
                DOMAIN,
                "get_position",
                {"entity_id": "lawn_mower.automower_420"},
                blocking=True,
                return_response=True,
            )
            assert position["lawn_mower.automower_420"] == {
                "gps": {"longitude": 2.25, "latitude": 52.0},
                "local": {
                    "x_mm": 910,
                    "y_mm": -1200,
                    "z_mm": 45,
                    "z_accuracy_mm": 30,
                    "heading_degrees": 270.0,
                    "heading_accuracy_degrees": 1.1,
                    "xy_accuracy_mm": 20,
                    "origin": {"longitude": 2.24999, "latitude": 51.99999},
                },
            }
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
            coordinator = entry.runtime_data
            maps = {
                100: (
                    b'<svg><polygon id="lawn" fill="green" points="0,0 1000,0 0,1000"/>'
                    b'<polyline id="pass" stroke="blue" stroke-width="3000" points="0,0 1000,0"/></svg>'
                ),
                101: b'<svg><polygon fill="green" points="0,0 1000,0 1000,1000 0,1000"/></svg>',
                103: b'<svg><polygon fill="red" points="0,0 1000,0 0,1000"/></svg>',
            }
            reads = []
            uploads = []
            missions = {}
            refuse_mission = False
            confirm_poi_mode = True
            poi_commands = []
            send = client.command

            async def command(operation, payload=b""):
                if operation == Command(4586, 0) and payload == b"\x04":
                    poi_commands.append((operation, payload))
                    if confirm_poi_mode:
                        client.values[(Command(4586, 1), b"")] = payload
                    return b""
                if operation == Command(4586, 4):
                    poi_commands.append((operation, payload))
                    return b""
                if operation == Command(5578, 23):
                    return md5(maps[100], usedforsecurity=False).digest()
                if operation == Command(5786, 7):
                    family, attribute = unpack_from("<HH", payload)
                    assert family == 5578
                    reads.append(attribute)
                    raw = gzip.compress(maps[attribute])
                    return pack("<IIBI", crc32(raw), len(raw), 1, len(raw)) + raw
                if operation == Command(5786, 11):
                    family, attribute, _, crc, size, last, chunk_size = unpack_from(
                        "<HHIIIBI", payload
                    )
                    raw = payload[21:]
                    assert (family, attribute, crc, size, last, chunk_size) == (
                        5578,
                        100,
                        crc32(raw),
                        len(raw),
                        1,
                        len(raw),
                    )
                    uploads.append(gzip.decompress(raw))
                    return b""
                if operation == Command(5578, 24):
                    assert payload == md5(uploads[-1], usedforsecurity=False).digest()
                    maps[100] = uploads[-1]
                    return b""
                if operation == Command(5604, 1):
                    if refuse_mission:
                        raise CommandRejected(operation, ResponseResult.INVALID_VALUE)
                    missions[20] = {"name": payload, "area": b"", "schedulable": b"\0"}
                    return U32.pack(20)
                if operation == Command(5604, 2):
                    return U32.pack(len(missions))
                if operation == Command(5604, 3):
                    mission_id = sorted(missions)[U32.unpack(payload)[0]]
                    return U32.pack(mission_id) + missions[mission_id]["name"]
                if operation == Command(5604, 5):
                    del missions[U32.unpack(payload)[0]]
                    return b""
                mission_fields = {7: "name", 11: "area", 18: "schedulable"}
                writable_fields = {12: "area", 19: "schedulable"}
                if operation.family == 5604 and operation.operation in mission_fields:
                    return missions[U32.unpack(payload)[0]][
                        mission_fields[operation.operation]
                    ]
                if operation.family == 5604 and operation.operation in writable_fields:
                    missions[U32.unpack_from(payload)[0]][
                        writable_fields[operation.operation]
                    ] = payload[4:]
                    return b""
                return await send(operation, payload)

            monkeypatch.setattr(client, "command", command)
            client.values.update(
                {
                    (Command(5578, 0), b""): b"\x01",
                    (Command(5578, 26), b""): b"",
                    (Command(5578, 38), b""): b"\x01",
                    (Command(5578, 40), b""): b"\x01",
                    (Command(5578, 43), b""): b"\x01",
                    (Command(5786, 13), b""): U32.pack(1024),
                    (Command(5604, 38), b""): U32.pack(5),
                }
            )
            await coordinator.async_refresh_optional()
            await hass.async_block_till_done()
            previews = {
                entity.key: entity for entity in hass.data[IMAGE_COMPONENT].entities
            }
            assert set(previews) == {
                "map_site_preview",
                "map_generated_preview",
                "map_zones_preview",
            }
            for preview in previews.values():
                if preview._render_task is not None:
                    await preview._render_task
                native = await async_get_image(hass, preview.entity_id)
                assert native.content_type == "image/png"
                assert native.content.startswith(b"\x89PNG")
            assert sorted(reads) == [100, 101, 103]
            generated = previews["map_generated_preview"]
            old_image = await generated.async_image()
            assert await generated.async_image() == old_image
            assert sorted(reads) == [100, 101, 103]

            maps[101] = maps[101].replace(b'fill="green"', b'fill="red"')
            coordinator._debounced_refresh.async_cancel()
            coordinator._async_on_event(Event(5578, 6, U32.pack(crc32(maps[101]))))
            await coordinator._event_refresh_task
            if generated._render_task is not None:
                await generated._render_task
            assert await generated.async_image() != old_image
            assert sorted(reads) == [100, 101, 101, 103]
            zones = await hass.services.async_call(
                DOMAIN,
                "get_zone_map",
                {"entity_id": "lawn_mower.automower_420"},
                blocking=True,
                return_response=True,
            )
            assert zones["lawn_mower.automower_420"]["svg"] == maps[103].decode()
            map_target = {"entity_id": "lawn_mower.automower_420"}
            client.values[(Command(5900, 17), b"")] = b"\x01"
            client.values[(Command(5900, 12), b"")] = U32.pack(7)

            async def map_action(action, **data):
                response = await hass.services.async_call(
                    DOMAIN,
                    action,
                    {**map_target, **data},
                    blocking=True,
                    return_response=True,
                )
                return response[map_target["entity_id"]]

            initial = await map_action("get_map_features")
            assert initial["installation_id"] == 7
            assert initial["features"][0]["type"] == "work_area"
            assert initial["features"][0]["editable"]
            generated_features = await map_action(
                "get_map_features", map_type="generated"
            )
            assert generated_features["features"][0]["type"] == "island"
            assert not generated_features["features"][0]["editable"]
            edit = {
                "feature_id": "lawn",
                "expected_checksum": initial["checksum"],
                "expected_installation_id": 7,
                "points": [
                    {"x_mm": 0, "y_mm": 0},
                    {"x_mm": 2000, "y_mm": 0},
                    {"x_mm": 0, "y_mm": 1000},
                ],
            }
            client.values[(Command(5900, 12), b"")] = U32.pack(8)
            with pytest.raises(HomeAssistantError, match="current installation ID"):
                await map_action("edit_map_geometry", **edit)
            assert not uploads
            client.values[(Command(5900, 12), b"")] = U32.pack(7)
            updated = await map_action("edit_map_geometry", **edit)
            assert updated["checksum"] != initial["checksum"]
            assert updated["feature_id"] == "lawn"
            assert uploads == [maps[100]]
            with pytest.raises(HomeAssistantError, match="map changed"):
                await map_action("edit_map_geometry", **edit)
            assert len(uploads) == 1
            assert (await map_action("get_map_features"))["features"][0][
                "points"
            ] == edit["points"]
            change = {
                "feature_id": "pass",
                "expected_checksum": updated["checksum"],
                "expected_installation_id": 7,
                "name": "Camino a la base",
                "enabled": False,
            }
            with pytest.raises(HomeAssistantError, match="3000 to 5000 mm"):
                await map_action("set_map_feature_settings", **change, width_mm=2000)
            assert len(uploads) == 1
            settings = await map_action(
                "set_map_feature_settings", **change, width_mm=3500
            )
            assert settings["checksum"] != updated["checksum"]
            path = (await map_action("get_map_features"))["features"][1]
            assert not path["enabled"]
            assert path["name"] == change["name"]
            assert path["width_mm"] == 3500
            assert path["width_limits_mm"] == {"min": 3000, "max": 5000}
            new_area = {
                "expected_checksum": settings["checksum"],
                "expected_installation_id": 7,
                "feature_type": "work_area",
                "name": "Patio",
                "points": edit["points"],
            }
            before_create = maps[100]
            refuse_mission = True
            with pytest.raises(HomeAssistantError, match="polygon was reverted"):
                await map_action("create_map_feature", **new_area)
            assert maps[100] == before_create
            assert not missions
            refuse_mission = False
            created = await map_action("create_map_feature", **new_area)
            assert created["mission_id"] == 20
            assert (
                missions[20]["area"].decode("ascii").strip("\0")
                == created["feature_id"]
            )
            request_drop = {
                "feature_id": created["feature_id"],
                "expected_checksum": created["checksum"],
                "expected_installation_id": 7,
            }
            task_key = (Command(4690, 5), U32.pack(0))
            general = client.values[task_key]
            client.values[task_key] = ScheduleTask.from_action(
                time(11), 45, ["mon"], 20
            ).encode()[1]
            with pytest.raises(
                HomeAssistantError, match="Remove the schedule intervals"
            ):
                await map_action("delete_map_feature", **request_drop)
            assert 20 in missions
            assert created["feature_id"].encode() in maps[100]
            client.values[task_key] = general
            await map_action("delete_map_feature", **request_drop)
            assert not missions
            assert maps[100] == before_create
            poi_button_id = "button.automower_420_park_at_point_of_interest"
            assert hass.states.get(poi_button_id) is None
            parking = await map_action(
                "create_map_feature",
                expected_checksum=settings["checksum"],
                expected_installation_id=7,
                feature_type="poi_path",
                name="Mantenimiento",
                points=edit["points"][:2],
                width_mm=3000,
            )
            await hass.async_block_till_done()
            assert hass.states.get(poi_button_id).state != "unavailable"
            confirm_poi_mode = False
            with pytest.raises(
                HomeAssistantError, match="did not confirm point-of-interest mode"
            ):
                await hass.services.async_call(
                    "button", "press", {"entity_id": poi_button_id}, blocking=True
                )
            assert poi_commands == [(Command(4586, 0), b"\x04")]
            confirm_poi_mode = True
            await hass.services.async_call(
                "button", "press", {"entity_id": poi_button_id}, blocking=True
            )
            assert poi_commands[-2:] == [
                (Command(4586, 0), b"\x04"),
                (Command(4586, 4), b""),
            ]
            assert coordinator.data["operation_mode"] == 4
            await map_action(
                "set_map_feature_settings",
                feature_id=parking["feature_id"],
                expected_checksum=parking["checksum"],
                expected_installation_id=7,
                enabled=False,
            )
            assert hass.states.get(poi_button_id).state == "unavailable"
            with pytest.raises(
                HomeAssistantError, match="needs an enabled point-of-interest path"
            ):
                await coordinator.async_mower_action("park_at_point_of_interest")
            assert len(poi_commands) == 3
            before_empty = maps[100]
            client.values[(Command(5578, 40), b"")] = b"\0"
            client.values[(Command(6170, 2), b"")] = b"\0"
            empty = await map_action("get_map_features")
            assert not empty["available"]
            assert not empty["can_create"]
            first_zone = {
                **new_area,
                "feature_type": "stay_out_zone",
                "expected_checksum": empty["checksum"],
            }
            with pytest.raises(HomeAssistantError, match="enabled EPOS installation"):
                await map_action("create_map_feature", **first_zone)
            assert maps[100] == before_empty
            client.values[(Command(6170, 2), b"")] = b"\x01"
            assert (await map_action("get_map_features"))["can_create"]
            first = await map_action("create_map_feature", **first_zone)
            assert first["feature_id"].encode() in maps[100]
            assert b'fill="red"' in maps[100]
            assert b'id="lawn"' not in maps[100]
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
async def test_wifi_options_scan_join_and_forget_without_storing_credentials(
    tmp_path, monkeypatch
):
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
    entry = _entry()
    monkeypatch.setattr(
        "custom_components.automower_ble_plus.coordinator.AutomowerClient",
        lambda *args: client,
    )
    wifi = 5916
    network = "Jardín de prueba"
    password = "contraseña de prueba"
    scan_sent = asyncio.Event()
    join_sent = asyncio.Event()
    client.values.update(
        {
            (Command(wifi, 0), b""): b"\x01",
            (Command(wifi, 2), b""): b"\0",
            (Command(wifi, 3), b"\x01"): b"",
            (Command(wifi, 7), b""): U32.pack(0),
            (Command(wifi, 17), b""): b"\0",
            (Command(wifi, 27), b""): b"",
            (Command(wifi, 28), b""): b"",
            (Command(wifi, 30), b""): b"",
            (Command(wifi, 15), encode_ssid(network)): b"",
            (Command(wifi, 13), encode_ssid(network)): b"",
        }
    )
    send = client.command

    async def command(operation, payload=b""):
        if operation == Command(wifi, 11):
            client.calls.append((operation, payload))
            assert payload == encode_ssid(network) + password.encode().ljust(64, b"\0")
            join_sent.set()
            return b""
        value = await send(operation, payload)
        if operation == Command(wifi, 3):
            client.values[Command(wifi, 2), b""] = payload
        elif operation == Command(wifi, 27):
            scan_sent.set()
        elif operation == Command(wifi, 15):
            client.values[Command(wifi, 7), b""] = U32.pack(0)
            client.values[Command(wifi, 17), b""] = b"\0"
        elif operation == Command(wifi, 13):
            join_sent.set()
        return value

    monkeypatch.setattr(client, "command", command)
    try:
        async with asyncio.timeout(15):
            assert await setup.async_setup_component(hass, DOMAIN, {})
            await hass.config_entries.async_add(entry)
            await hass.async_block_till_done()
            coordinator = entry.runtime_data
            await coordinator.async_refresh_optional()
            assert coordinator.can_configure_wifi
            assert coordinator.data["wifi_enabled"] == 0
            options = hass.config_entries.options
            flow = await options.async_init(entry.entry_id)
            assert flow["menu_options"] == ["wifi", "reset"]
            flow_id = flow["flow_id"]
            await options.async_configure(flow_id, {"next_step_id": "wifi"})
            flow = await options.async_configure(flow_id, {"next_step_id": "wifi_scan"})
            assert flow["type"] == FlowResultType.SHOW_PROGRESS
            await scan_sent.wait()
            # Core BLE commands can run while the Wi-Fi scan is in progress.
            await coordinator.async_keep_alive()
            for name, signal, auth_mode in (
                (network, -71, 3),
                (network, -45, 3),
                ("Enterprise", -10, 5),
                ("Open", -85, 0),
            ):
                coordinator._async_on_event(
                    Event(
                        wifi,
                        1,
                        pack("<33sbBB", encode_ssid(name), signal, 1, auth_mode),
                    )
                )
            coordinator._async_on_event(Event(wifi, 0, b""))
            await asyncio.sleep(0)
            await hass.async_block_till_done()
            flow = await options.async_configure(flow_id)
            assert flow["step_id"] == "wifi_choose"
            choices = next(iter(flow["data_schema"].schema.values())).config["options"]
            assert [choice["label"] for choice in choices] == [
                f"{network} (WPA2, -45 dBm)",
                "Open (Open, -85 dBm)",
            ]
            flow = await options.async_configure(flow_id, {"network": "0"})
            assert flow["step_id"] == "wifi_password"
            flow = await options.async_configure(flow_id, {"password": "é" * 33})
            assert flow["errors"] == {"password": "wifi_invalid_password"}
            flow = await options.async_configure(flow_id, {"password": password})
            assert flow["type"] == FlowResultType.SHOW_PROGRESS
            await join_sent.wait()
            coordinator._async_on_event(Event(wifi, 4, encode_ssid("Other") + b"\xa0"))
            assert coordinator.data["wifi_connected"] == 0
            client.values.update(
                {
                    (Command(wifi, 7), b""): U32.pack(1),
                    (Command(wifi, 5), U32.pack(0)): encode_ssid(network),
                    (Command(wifi, 17), b""): b"\x01",
                    (Command(wifi, 18), b""): encode_ssid(network),
                    (Command(wifi, 20), b""): pack("<b", -45),
                }
            )
            coordinator._async_on_event(Event(wifi, 4, encode_ssid(network) + b"\xd3"))
            await asyncio.sleep(0)
            await hass.async_block_till_done()
            flow = await options.async_configure(flow_id)
            assert flow["reason"] == "wifi_joined"
            assert coordinator.data["wifi_ssid"] == network
            assert coordinator.data["wifi_signal_strength"] == -45
            report = await diagnostics.async_get_config_entry_diagnostics(hass, entry)
            assert network not in str(report)
            assert password not in str(entry.data) and not entry.options

            async def saved_flow():
                result = await options.async_init(entry.entry_id)
                saved_flow_id = result["flow_id"]
                await options.async_configure(saved_flow_id, {"next_step_id": "wifi"})
                await options.async_configure(
                    saved_flow_id, {"next_step_id": "wifi_saved"}
                )
                return saved_flow_id

            saved = await saved_flow()
            join_sent.clear()
            await options.async_configure(
                saved, {"network": "0", "operation": "connect"}
            )
            await join_sent.wait()
            coordinator._async_on_event(Event(wifi, 6, encode_ssid(network) + b"\0"))
            await asyncio.sleep(0)
            await hass.async_block_till_done()
            flow = await options.async_configure(saved)
            assert flow["reason"] == "wifi_join_failed"

            saved = await saved_flow()
            flow = await options.async_configure(
                saved, {"network": "0", "operation": "forget"}
            )
            assert flow["reason"] == "wifi_forgotten"
            assert (Command(wifi, 15), encode_ssid(network)) in client.calls
            assert coordinator.data["wifi_connected"] == 0
            assert "wifi_ssid" not in coordinator.data
            monkeypatch.setattr(coordinator_module, "WIFI_TIMEOUT", 0)
            with pytest.raises(HomeAssistantError, match="complete the Wi-Fi scan"):
                await coordinator.async_scan_wifi()
            assert client.calls[-1] == (Command(wifi, 28), b"")
            assert await hass.config_entries.async_unload(entry.entry_id)
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
async def test_read_legacy_aim_maps_through_home_assistant(tmp_path, monkeypatch):
    hass = HomeAssistant(str(tmp_path))
    coordinator = MowerCoordinator(hass, _entry())
    client = FakeAutomower420()
    coordinator.client = client
    client.values[Command(4698, 9), b""] = bytes((28, 2))
    client.values[Command(5578, 0), b""] = b"\x01"
    monkeypatch.setattr(
        client, "main_node_version", AsyncMock(return_value="40.3_Main-App-P16_34.18")
    )
    maps = {
        101: b'<svg><polygon fill="green" points="0,0 1000,0 0,1000"/></svg>',
        103: b'<svg><polygon id="zone" fill="red" points="0,0 1000,0 0,1000"/></svg>',
    }
    send = client.command

    async def command(operation, payload=b""):
        if operation in (Command(5786, 7), Command(5786, 15)):
            family, attribute, transaction = unpack_from("<HHI", payload)
            assert family == 5578 and transaction != 0
            if operation.operation == 15:
                if attribute not in maps:
                    raise CommandRejected(operation, ResponseResult.NOT_AVAILABLE)
                return U32.pack(crc32(maps[attribute]))
            raw = gzip.compress(maps[attribute], mtime=0)
            return pack("<IIBI", crc32(raw), len(raw), 1, len(raw)) + raw
        return await send(operation, payload)

    monkeypatch.setattr(client, "command", command)
    try:
        await coordinator._async_setup()
        coordinator.async_set_updated_data(await coordinator._async_update_data())
        await coordinator.async_refresh_optional()
        assert maps_panel._supported_maps(coordinator) == ["generated", "zones"]
        preview = await coordinator.async_map_preview(MapKind.GENERATED)
        assert preview.startswith(b"\x89PNG")
        assert (await coordinator.async_read_zone_map())["svg"] == maps[103].decode()
        zones = await coordinator.async_read_map_features(MapKind.ZONES)
        assert not zones["can_create"]
        assert zones["features"][0]["id"] == "zone"
        assert not zones["features"][0]["editable"]
        previous = coordinator.data["map_generated_preview"]
        maps[101] = maps[101].replace(b'fill="green"', b'fill="red"')
        del maps[103]
        await coordinator.async_refresh_optional()
        assert maps_panel._supported_maps(coordinator) == ["generated"]
        assert coordinator.data["map_generated_preview"] != previous
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "schedule_result", ["default", "confirmed", "capacity", "lost_ack"]
)
async def test_edit_aim_zones_and_work_areas_through_home_assistant(
    tmp_path, monkeypatch, schedule_result
):
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
    entry = _entry()
    client = FakeAutomower420()
    monkeypatch.setattr(coordinator_module, "AutomowerClient", lambda *args: client)
    monkeypatch.setattr(
        client, "main_node_version", AsyncMock(return_value="40.3_Main-App-P16_81.4")
    )
    state = Command(5758, 7)
    get_raw = Command(5786, 7)
    set_raw = Command(5786, 11)
    set_next = Command(5786, 12)
    client.values.update(
        {
            (Command(4698, 9), b""): bytes((28, 2)),
            (Command(5758, 2), b""): b"\1",
            (Command(5758, 4), b""): b"\1",
            (state, b""): b"\2",
            (Command(5578, 0), b""): b"\1",
            (Command(5900, 17), b""): b"\1",
            (Command(5900, 12), b""): U32.pack(5),
            (Command(5786, 13), b""): U32.pack(40),
        }
    )
    stored = b""
    staged = bytearray()
    uploads = []
    total, checksum, transaction = 0, 0, 0
    work_area = {}
    refuse_mission = False
    lawn_interval = pack("<II7B", 3600, 1800, 1, 0, 0, 0, 0, 0, 0)
    area_interval = pack("<II7BI", 0, 86400, *([1] * 7), 12)
    schedule = [lawn_interval]
    pending_schedule = []
    send = client.command

    async def command(operation, payload=b""):
        nonlocal stored, total, checksum, transaction
        if operation == Command(5578, 38):
            return bytes((bool(stored),))
        if operation == get_raw:
            assert unpack_from("<HH", payload) == (5578, 103)
            return pack("<IIBI", crc32(stored), len(stored), 1, len(stored)) + stored
        if operation in (set_raw, set_next):
            if operation == set_raw:
                family, attribute, transaction, checksum, total = unpack_from(
                    "<HHIII", payload
                )
                assert (family, attribute) == (5578, 103)
                staged.clear()
                offset = 16
            else:
                assert U32.unpack_from(payload)[0] == transaction
                offset = 4
            last, length = unpack_from("<BI", payload, offset)
            chunk = payload[offset + 5 :]
            assert length == len(chunk) <= 40
            staged.extend(chunk)
            if last:
                assert (len(staged), crc32(staged)) == (total, checksum)
                stored = bytes(staged)
                uploads.append(gzip.decompress(stored))
            return b""
        assert operation != Command(5578, 24)
        if operation.family == 5604:
            match operation.operation:
                case 1:
                    if refuse_mission:
                        raise CommandRejected(operation, ResponseResult.INVALID_VALUE)
                    work_area["name"] = payload
                    work_area["height"] = b"\x23"
                    return U32.pack(12)
                case 2:
                    return U32.pack(bool(work_area))
                case 3:
                    assert payload == U32.pack(0)
                    return U32.pack(12) + work_area["name"]
                case 5:
                    assert payload == U32.pack(12)
                    work_area.clear()
                    return b""
                case 7 | 11 | 18:
                    assert payload == U32.pack(12)
                    return work_area[
                        {7: "name", 11: "polygon", 18: "schedulable"}[
                            operation.operation
                        ]
                    ]
                case 12 | 19:
                    assert U32.unpack_from(payload)[0] == 12
                    work_area[
                        {12: "polygon", 19: "schedulable"}[operation.operation]
                    ] = payload[4:]
                    return b""
                case 38:
                    return U32.pack(5)
                case 9:
                    assert payload == U32.pack(12)
                    return work_area["height"]
                case 10:
                    assert U32.unpack_from(payload)[0] == 12
                    work_area["height"] = payload[U32.size :]
                    return b""
                case 13:
                    assert payload == U32.pack(12)
                    return b"\1"
        if operation.family == 4690:
            match operation.operation:
                case 4:
                    return U32.pack(len(schedule))
                case 5:
                    return schedule[U32.unpack(payload)[0]]
                case 9 | 10:
                    pending_schedule.clear()
                    return b""
                case 7 | 16:
                    pending_schedule.append(payload)
                    return b""
                case 11:
                    schedule[:] = pending_schedule
                    if schedule_result == "lost_ack":
                        raise MowerConnectionError("Schedule saved but reply was lost")
                    return b""
        return await send(operation, payload)

    monkeypatch.setattr(client, "command", command)
    entity_id = "lawn_mower.automower_420"

    async def map_action(action, **data):
        response = await hass.services.async_call(
            DOMAIN,
            action,
            {"entity_id": entity_id, "map_type": "zones", **data},
            blocking=True,
            return_response=True,
        )
        return response[entity_id]

    try:
        async with asyncio.timeout(15):
            assert await setup.async_setup_component(hass, DOMAIN, {})
            await hass.config_entries.async_add(entry)
            await hass.async_block_till_done()
            baseline = await map_action("get_map_features")
            assert baseline["features"] == []
            assert baseline["creatable_types"] == ["stay_out_zone"]
            assert "can_schedule_full_day" not in baseline
            points = [
                {"x_mm": 0, "y_mm": 0},
                {"x_mm": 1000, "y_mm": 0},
                {"x_mm": 0, "y_mm": 1000},
            ]
            revision = {
                "expected_checksum": baseline["checksum"],
                "expected_installation_id": baseline["installation_id"],
            }
            with pytest.raises(HomeAssistantError, match="does not support a full-day"):
                await map_action(
                    "create_map_feature",
                    **revision,
                    feature_type="stay_out_zone",
                    name="Flores",
                    points=points,
                    schedule_full_day=True,
                )
            created = await map_action(
                "create_map_feature",
                **revision,
                feature_type="stay_out_zone",
                name="Flores",
                points=points,
            )
            selected = {**revision, "feature_id": created["feature_id"]}
            with pytest.raises(HomeAssistantError, match="map changed"):
                await map_action("set_map_feature_settings", **selected, enabled=False)
            selected["expected_checksum"] = created["checksum"]
            client.values[state, b""] = b"\1"
            readonly = await map_action("get_map_features")
            assert not readonly["features"][0]["editable"]
            assert not readonly["can_create"]
            with pytest.raises(HomeAssistantError, match="ready AIM map"):
                await map_action("set_map_feature_settings", **selected, enabled=False)
            assert len(uploads) == 1
            client.values[state, b""] = b"\2"
            renamed = await map_action(
                "set_map_feature_settings",
                **selected,
                name="Árbol & flores",
                enabled=False,
            )
            selected["expected_checksum"] = renamed["checksum"]
            points[1]["x_mm"] = 1500
            edited = await map_action("edit_map_geometry", **selected, points=points)
            selected["expected_checksum"] = edited["checksum"]
            feature = (await map_action("get_map_features"))["features"][0]
            assert (feature["name"], feature["points"], feature["enabled"]) == (
                "Árbol & flores",
                points,
                False,
            )
            await map_action("delete_map_feature", **selected)
            assert (await map_action("get_map_features"))["features"] == []
            assert len(uploads) == 4
            client.values[Command(5604, 20), b""] = b"\1"
            client.values[Command(4690, 14), b""] = U32.pack(
                1 if schedule_result == "capacity" else 8
            )
            client.values[Command(4690, 15), b""] = U32.pack(4)
            empty = await map_action("get_map_features")
            assert empty["creatable_types"] == ["work_area", "stay_out_zone"]
            assert empty["can_schedule_full_day"]
            new_area = {
                "expected_checksum": empty["checksum"],
                "expected_installation_id": empty["installation_id"],
                "feature_type": "work_area",
                "name": "Huerto",
                "points": points,
            }
            refuse_mission = True
            with pytest.raises(HomeAssistantError, match="polygon was reverted"):
                await map_action("create_map_feature", **new_area)
            assert not work_area
            assert (await map_action("get_map_features"))["checksum"] == empty[
                "checksum"
            ]
            refuse_mission = False
            if schedule_result != "default":
                new_area["schedule_full_day"] = True
            area = await map_action("create_map_feature", **new_area)
            assert area["mission_id"] == 12
            assert area["mowing_area_id"] == area["feature_id"]
            if schedule_result == "default":
                assert "schedule_confirmed" not in area
                assert schedule == [lawn_interval]
            elif schedule_result == "capacity":
                assert area["schedule_confirmed"] is False
                assert "at most" in area["schedule_error"]
                assert schedule == [lawn_interval]
            else:
                assert area["schedule_confirmed"] is (schedule_result == "confirmed")
                assert schedule == [lawn_interval, area_interval]
                if schedule_result == "confirmed":
                    calendar = entry.runtime_data.data["weekly_schedule"]
                    assert calendar.tasks[-1].mission_id == 12
                    assert calendar.tasks[-1].duration == 86400
            areas = await entry.runtime_data.async_read_work_areas(0, 20)
            assert areas["work_areas"][0]["mowing_area_id"] == area["feature_id"]
            area_edit = {
                "feature_id": area["feature_id"],
                "expected_checksum": area["checksum"],
                "expected_installation_id": 5,
            }
            points[1]["x_mm"] = 1600
            edited_area = await map_action(
                "edit_map_geometry", **area_edit, points=points
            )
            area_edit["expected_checksum"] = edited_area["checksum"]
            assert work_area["polygon"].rstrip(b"\0").decode() == area["feature_id"]
            if schedule_result == "confirmed":
                settings = await map_action("get_map_work_area_mowing", **area_edit)
                assert settings["name"] == "Huerto"
                assert settings["editable"] == ["cutting_height"]
                assert settings["settings"] == {
                    "cutting_height": 35,
                    "cutting_height_unit": "%",
                }
                change_height = dict(
                    area_edit,
                    expected_mission_id=12,
                    expected_value=35,
                    change={"cutting_height": 45},
                )
                with pytest.raises(
                    HomeAssistantError, match="linked mowing mission changed"
                ):
                    await map_action(
                        "set_map_work_area_mowing",
                        **dict(change_height, expected_mission_id=13),
                    )
                assert work_area["height"] == b"\x23"
                confirmed = await map_action(
                    "set_map_work_area_mowing", **change_height
                )
                assert confirmed["settings"]["cutting_height"] == 45
                with pytest.raises(HomeAssistantError, match="setting changed"):
                    await map_action("set_map_work_area_mowing", **change_height)
                assert work_area["height"] == b"\x2d"
                initial = await map_action("get_work_area_schedule", **area_edit)
                assert initial["mission_id"] == 12
                assert initial["intervals"][0]["duration_seconds"] == 86400
                update = {
                    **area_edit,
                    "expected_schedule_revision": initial["revision"],
                }
                intervals = [
                    {
                        "start": "23:20:05",
                        "duration_seconds": 6901,
                        "weekdays": ["sat", "sun"],
                    }
                ]
                changed_lawn = pack("<II7B", 7200, 1800, 0, 1, 0, 0, 0, 0, 0)
                schedule[0] = changed_lawn
                saved = await map_action(
                    "replace_work_area_schedule", **update, intervals=intervals
                )
                assert saved["revision"] != initial["revision"]
                assert saved["intervals"][0]["start"] == intervals[0]["start"]
                assert schedule == [
                    changed_lawn,
                    pack("<II7BI", 84005, 6901, 0, 0, 0, 0, 0, 1, 1, 12),
                ]
                with pytest.raises(HomeAssistantError, match="area schedule changed"):
                    await map_action(
                        "replace_work_area_schedule", **update, intervals=[]
                    )
                update["expected_schedule_revision"] = saved["revision"]
                work_area["schedulable"] = b"\0"
                with pytest.raises(
                    HomeAssistantError, match="does not support scheduling"
                ):
                    await map_action(
                        "replace_work_area_schedule", **update, intervals=[]
                    )
                work_area["schedulable"] = b"\1"
                cleared = await map_action(
                    "replace_work_area_schedule", **update, intervals=[]
                )
                assert cleared["intervals"] == []
                assert schedule == [changed_lawn]
            schedule[:] = [area_interval]
            with pytest.raises(HomeAssistantError, match="Remove the schedule"):
                await map_action("delete_map_feature", **area_edit)
            assert work_area
            schedule[:] = [lawn_interval]
            await map_action("delete_map_feature", **area_edit)
            assert not work_area
            assert (await map_action("get_map_features"))["features"] == []
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event",
    [
        Event(4738, 1, b""),
        Event(4738, 0, b"\x01"),
        Event(4738, 0, U32.pack(1_000_000)),
        Event(4690, 0, U32.pack(42)),
    ],
)
async def test_connect_pairing_code_rejects_invalid_events(event):
    client = Mock()
    client.command_and_wait_for_event = AsyncMock(return_value=event)
    with pytest.raises(ValueError, match="pairing"):
        await async_request_connect_pairing_code(client)
    client.command_and_wait_for_event.assert_awaited_once_with(
        Command(4738, 3), (0, 1), timeout=45.0
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ["acknowledged", "disconnected", "guest", "absent"])
async def test_maintain_connect_pairings_from_home_assistant(
    tmp_path, monkeypatch, reply
):
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
    entry = _entry("0042")
    client = FakeAutomower420()
    operator, available, remove = Command(4664, 3), Command(4738, 0), Command(4738, 4)
    enabled = Command(4738, 2)
    client.values.update(
        {
            (operator, b""): b"\x00" if reply == "guest" else b"\x01",
            (available, b""): b"\x00" if reply == "absent" else b"\x01",
            (enabled, b""): b"\x01",
            (Command(4738, 10), b""): b"\x01\x55",
            (remove, b""): b"",
        }
    )
    send = client.command

    async def command(operation, payload=b""):
        result = await send(operation, payload)
        if operation == remove and reply == "disconnected":
            raise MowerConnectionError("BLE link lost after writing")
        return result

    monkeypatch.setattr(client, "command", command)
    monkeypatch.setattr(coordinator_module, "AutomowerClient", lambda *args: client)
    pairing_started, finish_pairing = asyncio.Event(), asyncio.Event()

    async def wait_for_code(operation, event_ids, *, timeout):
        assert (operation, event_ids, timeout) == (Command(4738, 3), (0, 1), 45.0)
        pairing_started.set()
        await finish_pairing.wait()
        if reply == "disconnected":
            raise MowerConnectionError("BLE link lost while waiting for the event")
        return Event(4738, 0, U32.pack(12345))

    wait_for_event = AsyncMock(side_effect=wait_for_code)
    monkeypatch.setattr(
        client, "command_and_wait_for_event", wait_for_event, raising=False
    )
    try:
        async with asyncio.timeout(15):
            assert await setup.async_setup_component(hass, DOMAIN, {})
            await hass.config_entries.async_add(entry)
            await hass.async_block_till_done()
            coordinator = entry.runtime_data
            await coordinator.async_refresh_optional()
            options = hass.config_entries.options
            flow = await options.async_init(entry.entry_id)
            if reply in ("guest", "absent"):
                assert not coordinator.can_remove_connect_pairings
                assert "request_connect_pairing_code" not in flow.get(
                    "menu_options", ()
                )
                assert "remove_connect_pairings" not in flow.get("menu_options", ())
                assert (remove, b"") not in client.calls
                return

            assert "request_connect_pairing_code" in flow["menu_options"]
            flow_id = flow["flow_id"]
            await options.async_configure(
                flow_id, {"next_step_id": "request_connect_pairing_code"}
            )
            rejected = await options.async_configure(flow_id, {"current_pin": "9999"})
            assert rejected["errors"] == {"current_pin": "pin_current_mismatch"}
            client.values[enabled, b""] = b"\x00"
            with pytest.raises(
                HomeAssistantError, match="Enable the Automower Connect"
            ):
                await coordinator.async_request_connect_pairing_code("0042")
            wait_for_event.assert_not_called()
            client.values[enabled, b""] = b"\x01"
            progress = await options.async_configure(flow_id, {"current_pin": "0042"})
            assert progress["type"] == FlowResultType.SHOW_PROGRESS
            await pairing_started.wait()
            with pytest.raises(
                HomeAssistantError, match="cannot request a pairing code"
            ):
                await coordinator.async_request_connect_pairing_code("0042")
            finish_pairing.set()
            await asyncio.gather(
                options._progress[flow_id].async_get_progress_task(),
                return_exceptions=True,
            )
            await hass.async_block_till_done()
            progress_done = options._progress[flow_id].cur_step
            assert progress_done["type"] == FlowResultType.SHOW_PROGRESS_DONE
            result = await options.async_configure(flow_id)
            if reply == "acknowledged":
                assert result["reason"] == "connect_pairing_code_ready"
                assert result["description_placeholders"] == {"code": "012345"}
            else:
                assert result["reason"] == "connect_pairing_code_failed"
                assert result.get("description_placeholders") is None
            assert "012345" not in json.dumps(
                await diagnostics.async_get_config_entry_diagnostics(hass, entry)
            )
            wait_for_event.assert_awaited_once()

            flow = await options.async_init(entry.entry_id)
            assert "remove_connect_pairings" in flow["menu_options"]
            flow_id = flow["flow_id"]
            flow = await options.async_configure(
                flow_id, {"next_step_id": "remove_connect_pairings"}
            )
            assert flow["step_id"] == "remove_connect_pairings"
            flow = await options.async_configure(
                flow_id,
                {"current_pin": "9999", "confirm_remove_connect_pairings": False},
            )
            assert flow["errors"] == {
                "current_pin": "pin_current_mismatch",
                "confirm_remove_connect_pairings": "connect_pairings_confirmation_required",
            }
            confirmation = {
                "current_pin": "0042",
                "confirm_remove_connect_pairings": True,
            }
            client.values[operator, b""] = b"\x00"
            flow = await options.async_configure(flow_id, confirmation)
            assert flow["errors"] == {"base": "connect_pairings_failed"}
            assert (remove, b"") not in client.calls
            client.values[operator, b""] = b"\x01"
            client.values[available, b""] = b"\x00"
            flow = await options.async_configure(flow_id, confirmation)
            assert flow["errors"] == {"base": "connect_pairings_failed"}
            assert (remove, b"") not in client.calls
            client.values[available, b""] = b"\x01"
            flow = await options.async_configure(flow_id, confirmation)
            assert flow["type"] == FlowResultType.ABORT
            assert flow["reason"] == (
                "connect_pairings_requested"
                if reply == "acknowledged"
                else "connect_pairings_unconfirmed"
            )
            assert client.calls.count((remove, b"")) == 1
            assert entry.state == ConfigEntryState.LOADED
            assert entry.runtime_data is coordinator
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply", ["acknowledged", "disconnected", "unsupported", "guest"]
)
async def test_change_operator_pin_from_home_assistant(tmp_path, monkeypatch, reply):
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
    entry = _entry("0042")
    client = FakeAutomower420()
    client.values[Command(4664, 3), b""] = b"\0" if reply == "guest" else b"\1"
    change = Command(4664, 5)
    expected = (change, pack("<HH", 42, 73))
    if reply == "acknowledged":
        client.values[expected] = b""
    send = client.command

    async def command(operation, payload=b""):
        if operation == change and reply == "disconnected":
            client.calls.append((operation, payload))
            raise MowerConnectionError("BLE link lost after writing")
        return await send(operation, payload)

    reload_entry = Mock()
    start_reauth = Mock()
    monkeypatch.setattr(ConfigEntry, "async_start_reauth", start_reauth)
    monkeypatch.setattr(hass.config_entries, "async_schedule_reload", reload_entry)
    monkeypatch.setattr(client, "command", command)
    monkeypatch.setattr(coordinator_module, "AutomowerClient", lambda *args: client)
    try:
        async with asyncio.timeout(15):
            assert await setup.async_setup_component(hass, DOMAIN, {})
            await hass.config_entries.async_add(entry)
            await hass.async_block_till_done()
            options = hass.config_entries.options
            flow = await options.async_init(entry.entry_id)
            if reply == "guest":
                assert flow["step_id"] == "reset"
                assert not any(op == change for op, _ in client.calls)
                return

            assert flow["menu_options"] == ["reset", "change_pin"]
            flow_id = flow["flow_id"]
            flow = await options.async_configure(
                flow_id, {"next_step_id": "change_pin"}
            )
            assert flow["step_id"] == "change_pin"
            flow = await options.async_configure(
                flow_id,
                {"current_pin": "9999", "new_pin": "0073", "confirm_pin": "0074"},
            )
            assert flow["errors"] == {
                "current_pin": "pin_current_mismatch",
                "confirm_pin": "pin_confirmation_mismatch",
            }
            assert not any(op == change for op, _ in client.calls)

            flow = await options.async_configure(
                flow_id,
                {"current_pin": "0042", "new_pin": "0073", "confirm_pin": "0073"},
            )
            assert flow["type"] == FlowResultType.ABORT
            assert [call for call in client.calls if call[0] == change] == [expected]
            if reply == "acknowledged":
                assert flow["reason"] == "pin_changed"
                assert entry.data["pin"] == "0073"
                assert client.pin == 73
                reload_entry.assert_called_once_with(entry.entry_id)
                start_reauth.assert_not_called()
            else:
                assert entry.data["pin"] == "0042"
                reload_entry.assert_not_called()
                if reply == "disconnected":
                    assert flow["reason"] == "pin_change_unconfirmed"
                    start_reauth.assert_called_once_with(hass)
                    with pytest.raises(ConfigEntryAuthFailed, match="unconfirmed"):
                        await entry.runtime_data._async_connect()
                else:
                    assert flow["reason"] == "pin_change_unavailable"
                    start_reauth.assert_not_called()
                assert not entry.runtime_data.can_change_pin
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
async def test_aim_map_progress_and_regeneration_from_options(tmp_path, monkeypatch):
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
    entry = _entry()
    client = FakeAutomower420()
    monkeypatch.setattr(coordinator_module, "AutomowerClient", lambda *args: client)
    monkeypatch.setattr(
        client, "main_node_version", AsyncMock(return_value="40.3_Main-App-P16_81.4")
    )
    client.values[Command(4698, 9), b""] = bytes((28, 2))
    aim = 5758
    updated = 1795719999
    client.values.update(
        {
            (Command(aim, 0), b""): b"",
            (Command(aim, 2), b""): b"\1",
            (Command(aim, 4), b""): b"\1",
            (Command(aim, 5), b""): U32.pack(updated),
            (Command(aim, 6), b""): b"",
            (Command(aim, 7), b""): b"\2",
            (Command(aim, 10), b""): bytes((100,)),
            (Command(5578, 0), b""): b"\1",
            (Command(5578, 43), b""): b"\1",
        }
    )
    try:
        async with asyncio.timeout(15):
            assert await setup.async_setup_component(hass, DOMAIN, {})
            await hass.config_entries.async_add(entry)
            await hass.async_block_till_done()
            coordinator = entry.runtime_data
            await coordinator.async_refresh_optional()
            coordinator.async_set_updated_data(await coordinator._async_update_data())
            assert coordinator.device_info["model"] == "Automower 415X"
            assert (Command(aim, 0), b"") in client.calls
            assert coordinator.data["map_generated_preview"].endswith(f":{updated}")
            await hass.async_block_till_done()
            status_id = entity_registry.async_get(hass).async_get_entity_id(
                "sensor", DOMAIN, f"{coordinator.address}_aim_map_state"
            )
            assert hass.states.get(status_id).state == "ready"
            options = hass.config_entries.options

            async def confirm_flow():
                flow = await options.async_init(entry.entry_id)
                assert "regenerate_aim_map" in flow["menu_options"]
                flow_id = flow["flow_id"]
                flow = await options.async_configure(
                    flow_id, {"next_step_id": "regenerate_aim_map"}
                )
                assert flow["step_id"] == "regenerate_aim_map"
                return flow_id

            flow_id = await confirm_flow()
            result = await options.async_configure(flow_id, {"confirm_aim_map": False})
            assert result["errors"] == {"confirm_aim_map": "aim_confirmation_required"}
            assert (Command(aim, 6), b"") not in client.calls
            # Re-check the active state even when the user already opened the form.
            client.values[Command(aim, 7), b""] = b"\1"
            result = await options.async_configure(flow_id, {"confirm_aim_map": True})
            assert result["reason"] == "aim_map_unavailable"
            assert (Command(aim, 6), b"") not in client.calls
            client.values[Command(aim, 7), b""] = b"\2"
            await coordinator.async_refresh_optional()

            flow_id = await confirm_flow()
            result = await options.async_configure(flow_id, {"confirm_aim_map": True})
            assert result["reason"] == "aim_map_requested"
            assert client.calls.count((Command(aim, 6), b"")) == 1
            assert not coordinator.can_regenerate_aim_map
            client.values[Command(aim, 7), b""] = b"\1"
            client.values[Command(aim, 10), b""] = bytes((48,))
            coordinator._async_on_event(Event(aim, 0, pack("<BIB", 1, updated, 48)))
            coordinator.async_set_updated_data(await coordinator._async_update_data())
            assert coordinator.data["aim_map_progress"] == 48
            client.values[Command(aim, 5), b""] = U32.pack(updated + 1)
            client.values[Command(aim, 7), b""] = b"\2"
            client.values[Command(aim, 10), b""] = bytes((100,))
            coordinator._async_on_event(
                Event(aim, 0, pack("<BIB", 2, updated + 1, 100))
            )
            coordinator.async_set_updated_data(await coordinator._async_update_data())
            assert coordinator.data["map_generated_preview"].endswith(f":{updated + 1}")
            assert coordinator.can_regenerate_aim_map
            client.values[Command(aim, 2), b""] = b"\0"
            coordinator._async_on_event(Event(aim, 1, b"\0\0"))
            coordinator.async_set_updated_data(await coordinator._async_update_data())
            assert not any(key.startswith("aim_") for key in coordinator.data)
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
