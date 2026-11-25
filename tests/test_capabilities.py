"""Verify that Home Assistant exposes only controls confirmed over BLE."""

import asyncio
from pathlib import Path
from types import MappingProxyType

import pytest
from homeassistant import loader, setup
from homeassistant.config_entries import (
    SOURCE_USER,
    ConfigEntries,
    ConfigEntry,
    ConfigEntryState,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import (
    area_registry,
    device_registry,
    entity_registry,
    label_registry,
)

from custom_components.automower_ble_plus import (
    binary_sensor,
    button,
    number,
    select,
    switch,
    update,
)
from custom_components.automower_ble_plus.const import DOMAIN
from custom_components.automower_ble_plus.coordinator import MowerCoordinator
from custom_components.automower_ble_plus.features import I16, U16, U32
from custom_components.automower_ble_plus.firmware import get_firmware_profile
from custom_components.automower_ble_plus.transport import CommandRejected
from custom_components.automower_ble_plus.wire import Command


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
    coordinator = MowerCoordinator(hass, entry)
    coordinator.client = client
    entry.runtime_data = coordinator
    await coordinator._async_setup()
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    entities = []
    await switch.async_setup_entry(hass, entry, entities.extend)
    await number.async_setup_entry(hass, entry, entities.extend)
    assert not entities

    await coordinator.async_refresh_optional()
    detected = {entity.key: entity for entity in entities}
    assert set(detected) == {
        "eco_mode",
        "drive_past_wire",
        "starting_point_1_distance",
        "reversing_distance",
        "sector_1_start_angle",
        "sector_1_end_angle",
    }
    assert (Command(4422, 2), b"") not in client.calls
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
async def test_accessory_switches_require_mower_availability(tmp_path, monkeypatch):
    entry = _entry()
    coordinator = MowerCoordinator(HomeAssistant(str(tmp_path)), entry)
    client = FakeAutomower420()
    coordinator.client = client
    entry.runtime_data = coordinator
    ultrasonic = Command(4728, 1)
    edge_cut = Command(6690, 2)
    client.values.update(
        {
            (Command(4728, 0), b""): b"\x01",
            (ultrasonic, b""): b"\0",
            (Command(6690, 3), b""): b"\0",
            (edge_cut, b""): b"\0",
        }
    )
    send = client.command

    async def command(operation, payload=b""):
        if operation == Command(4728, 2) and payload == b"\x01":
            client.values[ultrasonic, b""] = payload
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
    await detected["ultrasonic"].async_turn_on()
    assert detected["ultrasonic"].is_on
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
    hass.config.components.update({"bluetooth", "bluetooth_adapters"})
    client = FakeAutomower420()
    area = U32.pack(12)
    client.values.update(
        {
            (Command(5604, 20), b""): b"\x01",
            (Command(5604, 13), area): b"\x02",
            (Command(5582, 0), b""): b"\x01",
            (Command(5582, 3), area): I16.pack(450),
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
            assert await hass.config_entries.async_unload(entry.entry_id)
            assert entry.state == ConfigEntryState.NOT_LOADED
    finally:
        await hass.async_stop(force=True)


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
