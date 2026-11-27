"""Integrate Husqvarna Automower devices with Home Assistant over BLE."""

from datetime import timedelta

import probatio as vol
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.service import async_register_platform_entity_service

from .appdrive_panel import async_attach_appdrive_panel, async_setup_appdrive_panel
from .config_flow import core_controls_address
from .const import DOMAIN
from .coordinator import MowerCoordinator, MowerEntry
from .map_features import (
    CREATABLE_MAP_TYPES,
    MAX_COORDINATE,
    MAX_PATH_WIDTH,
    MIN_COORDINATE,
)
from .mapfiles import MapKind
from .maps_panel import async_attach_maps_panel, async_setup_maps_panel
from .schedule import WEEKDAYS
from .work_areas import (
    AREA_ASSISTANCE_BOOLEAN_SETTINGS,
    AREA_PATTERN_PRESETS,
    AREA_PRIORITIES,
    AREA_VISION_PROFILES,
)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CALENDAR,
    Platform.DATE,
    Platform.DEVICE_TRACKER,
    Platform.EVENT,
    Platform.IMAGE,
    Platform.LAWN_MOWER,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TEXT,
    Platform.TIME,
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
    coordinate = vol.All(int, vol.Range(min=MIN_COORDINATE, max=MAX_COORDINATE))
    point = vol.Schema(
        {vol.Required("x_mm"): coordinate, vol.Required("y_mm"): coordinate}
    )
    map_revision = {
        vol.Required("expected_checksum"): cv.string,
        vol.Optional("expected_installation_id"): mission,
        vol.Optional("map_type", default=MapKind.SITE.value): vol.In(
            (MapKind.SITE.value, MapKind.ZONES.value)
        ),
    }
    selected_feature = {**map_revision, vol.Required("feature_id"): cv.string}
    area_interval = vol.Schema(
        {
            vol.Required("start"): cv.time,
            vol.Required("duration_seconds"): _bounded_integer(1, 86400),
            vol.Required("weekdays"): vol.All(
                [vol.In(WEEKDAYS)], vol.Length(min=1, max=7)
            ),
        }
    )
    map_points = vol.All([point], vol.Length(min=2))
    map_width = _bounded_integer(1, MAX_PATH_WIDTH)
    mowing_change = {
        vol.Exclusive("mowing_pattern", "mowing_change"): vol.In(
            ("random", "systematic")
        ),
        vol.Exclusive("mowing_pattern_preset", "mowing_change"): vol.In(
            AREA_PATTERN_PRESETS
        ),
        vol.Exclusive("orientation", "mowing_change"): angle,
        vol.Exclusive("orientation_shift", "mowing_change"): angle,
        vol.Exclusive("boundary_mowing", "mowing_change"): cv.boolean,
        vol.Exclusive("cutting_height", "mowing_change"): _bounded_integer(0, 100),
        vol.Exclusive("use_global_cutting_height", "mowing_change"): cv.boolean,
        vol.Exclusive("vision_obstacle_profile", "mowing_change"): vol.In(
            tuple(AREA_VISION_PROFILES.values())
        ),
        vol.Exclusive("priority", "mowing_change"): vol.In(
            tuple(AREA_PRIORITIES.values())
        ),
        **{
            vol.Exclusive(key, "mowing_change"): cv.boolean
            for key in sorted(AREA_ASSISTANCE_BOOLEAN_SETTINGS)
        },
    }
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
        "get_work_area_status": {vol.Optional("mission_id"): mission},
        "get_work_area_schedule": selected_feature,
        "replace_work_area_schedule": {
            **selected_feature,
            vol.Required("expected_schedule_revision"): cv.string,
            vol.Required("intervals"): [area_interval],
        },
        "create_work_area": {
            vol.Required("name"): cv.string,
            vol.Required("area_id"): cv.string,
            vol.Required("expected_checksum"): cv.string,
        },
        "delete_work_area": {
            vol.Required("mission_id"): mission,
            vol.Required("expected_name"): cv.string,
        },
        "reset_work_area_progress": {vol.Required("mission_id"): mission},
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
        "get_position": {},
        "get_loop_signals": {},
        "get_map": {vol.Optional("generated", default=False): cv.boolean},
        "get_zone_map": {},
        "get_map_features": {
            vol.Optional("map_type", default=MapKind.SITE.value): vol.In(
                tuple(MapKind)
            ),
        },
        "edit_map_geometry": {
            **selected_feature,
            vol.Required("points"): map_points,
        },
        "set_map_feature_settings": {
            **selected_feature,
            vol.Optional("name"): cv.string,
            vol.Optional("enabled"): cv.boolean,
            vol.Optional("width_mm"): map_width,
        },
        "create_map_feature": {
            **map_revision,
            vol.Required("feature_type"): vol.In(CREATABLE_MAP_TYPES),
            vol.Required("name"): cv.string,
            vol.Required("points"): map_points,
            vol.Optional("width_mm"): map_width,
            vol.Optional("schedule_full_day", default=False): cv.boolean,
        },
        "delete_map_feature": selected_feature,
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
        "set_work_area_mowing": {vol.Required("mission_id"): mission, **mowing_change},
        "get_map_work_area_mowing": selected_feature,
        "set_map_work_area_mowing": {
            **selected_feature,
            vol.Required("expected_mission_id"): mission,
            vol.Required("expected_value"): vol.Any(bool, int, float, str),
            vol.Required("change"): vol.Schema(mowing_change),
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
            if action
            in (
                "create_installation",
                "create_work_area",
                "replace_map",
                "edit_map_geometry",
                "set_map_feature_settings",
                "create_map_feature",
                "delete_map_feature",
                "replace_work_area_schedule",
                "set_map_work_area_mowing",
            )
            else SupportsResponse.NONE,
        )
    await async_setup_appdrive_panel(hass)
    await async_setup_maps_panel(hass)
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

    async_attach_appdrive_panel(hass, entry)
    async_attach_maps_panel(hass, entry)

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
