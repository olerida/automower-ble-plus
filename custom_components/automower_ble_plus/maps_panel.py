"""Register a map editor for Automower entries with a BLE map."""

from pathlib import Path

from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .aim import AIM_READY
from .const import DOMAIN
from .coordinator import MowerCoordinator, MowerEntry
from .mapfiles import MapKind

_PANEL = "automower-maps"
_DATA_KEY = f"{DOMAIN}_maps_panel"
_SCRIPT = f"/api/{DOMAIN}/maps.js"


def _supported_maps(coordinator: MowerCoordinator) -> list[str]:
    data = coordinator.data or {}
    profile = coordinator.firmware_profile
    maps = [kind.value for kind in MapKind if kind.preview_key in data]
    if (
        MapKind.SITE not in maps
        and data.get("epos_enabled") == 1
        and "site_map_installation_type" in data
    ):
        maps.insert(0, MapKind.SITE.value)
    if (
        MapKind.ZONES not in maps
        and profile is not None
        and profile.zone_map_writable
        and data.get("aim_enabled") == 1
        and data.get("aim_map_state") == AIM_READY
    ):
        maps.append(MapKind.ZONES.value)
    return maps


class MapsPanel:
    """Keep the sidebar entry in sync with discovered map types."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.entries: dict[str, MowerCoordinator] = {}
        self._registering = False

    @callback
    def async_attach(self, entry: MowerEntry) -> None:
        self.entries[entry.entry_id] = entry.runtime_data
        entry.async_on_unload(entry.runtime_data.async_add_listener(self._async_sync))

        @callback
        def remove_entry() -> None:
            self.entries.pop(entry.entry_id, None)
            self._async_sync()

        entry.async_on_unload(remove_entry)
        self._async_sync()

    def _has_maps(self) -> bool:
        return any(
            _supported_maps(coordinator) for coordinator in self.entries.values()
        )

    @callback
    def _async_sync(self) -> None:
        if not self._has_maps():
            frontend.async_remove_panel(self.hass, _PANEL, warn_if_unknown=False)
        elif not self._registering and not frontend.async_panel_exists(
            self.hass, _PANEL
        ):
            self._registering = True
            self.hass.async_create_task(
                self._async_register(), "show the Automower maps panel"
            )

    async def _async_register(self) -> None:
        try:
            if self._has_maps():
                await panel_custom.async_register_panel(
                    self.hass,
                    frontend_url_path=_PANEL,
                    webcomponent_name="automower-ble-maps",
                    sidebar_title="Automower Maps",
                    sidebar_icon="mdi:map-outline",
                    module_url=_SCRIPT,
                )
            if not self._has_maps():
                frontend.async_remove_panel(self.hass, _PANEL, warn_if_unknown=False)
        finally:
            self._registering = False


async def async_setup_maps_panel(hass: HomeAssistant) -> None:
    """Register the panel assets and device discovery command."""
    if "frontend" not in hass.config.components:
        return
    await hass.http.async_register_static_paths(
        [StaticPathConfig(_SCRIPT, str(Path(__file__).with_name("maps.js")), False)]
    )
    hass.data[_DATA_KEY] = MapsPanel(hass)
    websocket_api.async_register_command(hass, _async_list_maps)


@callback
def async_attach_maps_panel(hass: HomeAssistant, entry: MowerEntry) -> None:
    """Track the map capabilities of a loaded config entry."""
    if (panel := hass.data.get(_DATA_KEY)) is not None:
        panel.async_attach(entry)


@websocket_api.websocket_command({"type": f"{DOMAIN}/maps_list"})
@callback
def _async_list_maps(hass, connection, msg):
    registry = er.async_get(hass)
    mowers = []
    for coordinator in hass.data[_DATA_KEY].entries.values():
        maps = _supported_maps(coordinator)
        if not maps:
            continue
        entity_id = registry.async_get_entity_id(
            "lawn_mower", DOMAIN, f"{coordinator.address}_mower"
        )
        if (
            entity_id is not None
            and (entity := registry.async_get(entity_id)) is not None
            and not entity.disabled
            and connection.user.permissions.check_entity(entity_id, POLICY_CONTROL)
        ):
            mowers.append(
                {
                    "entity_id": entity_id,
                    "name": coordinator.config_entry.title,
                    "maps": maps,
                }
            )
    connection.send_result(msg["id"], mowers)
