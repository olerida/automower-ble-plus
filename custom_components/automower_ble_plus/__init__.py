"""Integrate Husqvarna Automower devices with Home Assistant over BLE."""

from datetime import timedelta

import probatio as vol
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.service import async_register_platform_entity_service

from .config_flow import core_controls_address
from .const import DOMAIN
from .coordinator import MowerCoordinator, MowerEntry
from .schedule import WEEKDAYS

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.DEVICE_TRACKER,
    Platform.LAWN_MOWER,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.UPDATE,
]


def _bounded_integer(minimum: int, maximum: int):
    return vol.All(cv.positive_int, vol.Range(min=minimum, max=maximum))


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register actions for the integration's mower entities."""
    duration = _bounded_integer(1, 0xFFFFFFFF // 60)
    interval_schema = vol.Schema(
        {
            vol.Required("start"): cv.time,
            vol.Required("duration_minutes"): _bounded_integer(1, 1440),
            vol.Required("weekdays"): vol.All(
                [vol.In(WEEKDAYS)], vol.Length(min=1, max=7)
            ),
            vol.Optional("mission_id", default=0): _bounded_integer(0, 0xFFFFFFFF),
        }
    )
    page_schema = {
        vol.Optional("offset", default=0): _bounded_integer(0, 0xFFFFFFFF),
        vol.Optional("limit", default=20): _bounded_integer(1, 100),
    }
    angle = vol.All(vol.Coerce(float), vol.Range(min=-360, max=360))
    mission = _bounded_integer(0, 0xFFFFFFFF)
    schemas = {
        "mow_for": {
            vol.Required("minutes"): duration,
            vol.Optional("mission_id"): _bounded_integer(0, 0xFFFFFFFF),
        },
        "park_for": {vol.Required("minutes"): duration},
        "mow_secondary_area": {
            vol.Optional("minutes"): duration,
            vol.Optional("mission_id"): _bounded_integer(0, 0xFFFFFFFF),
        },
        "get_schedule": {},
        "replace_schedule": {vol.Required("intervals"): [interval_schema]},
        "get_message_history": page_schema,
        "get_work_areas": page_schema,
        "get_work_area_mowing": {vol.Required("mission_id"): mission},
        "get_installations": page_schema,
        "activate_installation": {
            vol.Required("site_id"): _bounded_integer(0, 0xFFFFFFFF),
            vol.Optional("expected_active_id"): _bounded_integer(0, 0xFFFFFFFF),
        },
        "create_installation": {vol.Required("copy_current"): cv.boolean},
        "delete_installation": {
            vol.Required("site_id"): _bounded_integer(0, 0xFFFFFFFF),
            vol.Required("expected_name"): cv.string,
        },
        "get_geofence": {},
        "get_loop_signals": {},
        "get_map": {vol.Optional("generated", default=False): cv.boolean},
        "replace_map": {
            vol.Required("svg"): cv.string,
            vol.Required("expected_checksum"): cv.string,
        },
        "rename_installation": {
            vol.Required("site_id"): _bounded_integer(0, 0xFFFFFFFF),
            vol.Required("name"): cv.string,
        },
        "update_work_area": {
            vol.Required("mission_id"): _bounded_integer(0, 0xFFFFFFFF),
            vol.Exclusive("name", "work_area_change"): cv.string,
            vol.Exclusive("enabled", "work_area_change"): cv.boolean,
        },
        "set_work_area_mowing": {
            vol.Required("mission_id"): mission,
            vol.Exclusive("mowing_pattern", "mowing_change"): vol.In(
                ("random", "systematic")
            ),
            vol.Exclusive("orientation", "mowing_change"): angle,
            vol.Exclusive("orientation_shift", "mowing_change"): angle,
            vol.Exclusive("boundary_mowing", "mowing_change"): cv.boolean,
            vol.Exclusive("cutting_height", "mowing_change"): _bounded_integer(0, 100),
            vol.Exclusive("use_global_cutting_height", "mowing_change"): cv.boolean,
        },
    }
    for action, schema in schemas.items():
        async_register_platform_entity_service(
            hass,
            DOMAIN,
            action,
            entity_domain=Platform.LAWN_MOWER,
            schema=schema,
            func=f"async_{action}",
            supports_response=SupportsResponse.ONLY
            if action.startswith("get_")
            else SupportsResponse.OPTIONAL
            if action in ("create_installation", "replace_map")
            else SupportsResponse.NONE,
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: MowerEntry) -> None:
    """Open the mower and initialise its platforms."""
    if core_controls_address(hass, entry.data[CONF_ADDRESS]):
        raise ConfigEntryNotReady(
            "Disable the Husqvarna Automower BLE entry for this mower"
        )
    coordinator = MowerCoordinator(hass, entry)
    entry.runtime_data = coordinator
    try:
        await coordinator.async_config_entry_first_refresh()
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await coordinator.async_shutdown()
        raise

    entry.async_on_unload(
        async_track_time_interval(
            hass, coordinator.async_keep_alive, timedelta(seconds=15)
        )
    )
    entry.async_on_unload(
        async_track_time_interval(
            hass, coordinator.async_refresh_optional, timedelta(minutes=3)
        )
    )
    entry.async_create_background_task(
        hass,
        coordinator.async_refresh_optional(),
        "discover Automower BLE capabilities",
    )


async def async_unload_entry(hass: HomeAssistant, entry: MowerEntry) -> None:
    """Remove the mower entities and close the connection."""
    await hass.config_entries.async_unload_platforms_or_raise(entry, PLATFORMS)
    await entry.runtime_data.async_shutdown()
