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
from homeassistant.helpers import (
    area_registry,
    device_registry,
    entity_registry,
    label_registry,
)

from custom_components.automower_ble_plus import number, switch
from custom_components.automower_ble_plus.const import DOMAIN
from custom_components.automower_ble_plus.coordinator import MowerCoordinator
from custom_components.automower_ble_plus.features import U16
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
            (Command(4692, 6), b""): b"\x01",
            (Command(4422, 5), b""): b"\x00",
            (Command(4422, 2), b""): b"\x04",
            (Command(4712, 0), b""): U16.pack(310),
            (Command(4706, 10), b"\x01"): U16.pack(150),
        }

    def is_connected(self):
        return True

    async def command(self, command, payload=b""):
        self.calls.append((command, payload))
        if command == Command(4712, 1):
            self.values[Command(4712, 0), b""] = payload
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
    assert set(detected) == {"eco_mode", "drive_past_wire", "starting_point_1_distance"}
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
