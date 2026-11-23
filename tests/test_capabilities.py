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

from custom_components.automower_ble_plus import button, number, switch
from custom_components.automower_ble_plus.const import DOMAIN
from custom_components.automower_ble_plus.coordinator import MowerCoordinator
from custom_components.automower_ble_plus.features import U16, U32
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


def _entry():
    return ConfigEntry(
        domain=DOMAIN,
        title="Automower 420",
        version=1,
        minor_version=1,
        source=SOURCE_USER,
        data={"address": "00:11:22:33:44:55"},
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
    monkeypatch.setattr(
        "custom_components.automower_ble_plus.coordinator.AutomowerClient",
        lambda *args: FakeAutomower420(),
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
