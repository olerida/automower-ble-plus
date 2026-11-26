"""Register the Home Assistant appDrive panel and its control channel."""

from pathlib import Path

import probatio as vol
from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, LOGGER
from .coordinator import MowerCoordinator, MowerEntry

_PANEL = "automower-appdrive"
_DATA_KEY = f"{DOMAIN}_appdrive_panel"
_SCRIPT = f"/api/{DOMAIN}/appdrive.js"
_ENTRY = vol.Required("entry_id")
_TOKEN = vol.Required("token")
_AXIS = vol.All(vol.Coerce(float), vol.Range(min=-1, max=1))


class AppDrivePanel:
    """Expose a sidebar panel while a configured mower offers appDrive."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.entries: dict[str, MowerCoordinator] = {}
        self._registration_pending = False

    @callback
    def async_add_entry(self, entry: MowerEntry) -> None:
        self.entries[entry.entry_id] = entry.runtime_data
        entry.async_on_unload(entry.runtime_data.async_add_listener(self._async_sync))

        @callback
        def remove_entry() -> None:
            self.entries.pop(entry.entry_id, None)
            self._async_sync()

        entry.async_on_unload(remove_entry)
        self._async_sync()

    def _has_appdrive(self) -> bool:
        return any(
            "appdrive_state" in coordinator.data
            for coordinator in self.entries.values()
        )

    @callback
    def _async_sync(self) -> None:
        if self._has_appdrive():
            if not self._registration_pending and not frontend.async_panel_exists(
                self.hass, _PANEL
            ):
                self._registration_pending = True
                self.hass.async_create_task(
                    self._async_register(), "show the appDrive panel"
                )
        else:
            frontend.async_remove_panel(self.hass, _PANEL, warn_if_unknown=False)

    async def _async_register(self) -> None:
        try:
            if self._has_appdrive():
                await panel_custom.async_register_panel(
                    self.hass,
                    frontend_url_path=_PANEL,
                    webcomponent_name="automower-ble-appdrive",
                    sidebar_title="Automower appDrive",
                    sidebar_icon="mdi:gamepad-variant-outline",
                    module_url=_SCRIPT,
                )
        finally:
            self._registration_pending = False


async def async_setup_appdrive_panel(hass: HomeAssistant) -> None:
    """Register the script and commands when Home Assistant has a frontend."""
    if "frontend" not in hass.config.components:
        return
    await hass.http.async_register_static_paths(
        [StaticPathConfig(_SCRIPT, str(Path(__file__).with_name("appdrive.js")), False)]
    )
    hass.data[_DATA_KEY] = AppDrivePanel(hass)
    for command in (_async_list, _async_start, _async_input, _async_release):
        websocket_api.async_register_command(hass, command)


@callback
def async_attach_appdrive_panel(hass: HomeAssistant, entry: MowerEntry) -> None:
    """Track this entry's reported appDrive availability."""
    if (panel := hass.data.get(_DATA_KEY)) is not None:
        panel.async_add_entry(entry)


def _allowed_mowers(hass: HomeAssistant, connection: websocket_api.ActiveConnection):
    registry = er.async_get(hass)
    for entry_id, coordinator in hass.data[_DATA_KEY].entries.items():
        if "appdrive_state" not in coordinator.data:
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
            yield entry_id, coordinator


def _mower(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, entry_id: str
) -> MowerCoordinator:
    for allowed_id, coordinator in _allowed_mowers(hass, connection):
        if allowed_id == entry_id:
            return coordinator
    raise HomeAssistantError("No appDrive access to this mower")


@websocket_api.websocket_command({"type": f"{DOMAIN}/appdrive_list"})
@callback
def _async_list(hass, connection, msg):
    connection.send_result(
        msg["id"],
        [
            {"entry_id": entry_id, "name": coordinator.config_entry.title}
            for entry_id, coordinator in _allowed_mowers(hass, connection)
        ],
    )


@websocket_api.websocket_command({"type": f"{DOMAIN}/appdrive_start", _ENTRY: str})
@websocket_api.async_response
async def _async_start(hass, connection, msg):
    coordinator = _mower(hass, connection, msg["entry_id"])
    token = await coordinator.async_begin_appdrive_control(connection)

    async def release() -> None:
        try:
            await coordinator.async_release_appdrive_control(connection, token)
        except HomeAssistantError as err:
            LOGGER.debug("appDrive connection closed with an unconfirmed stop: %s", err)

    @callback
    def unsubscribe() -> None:
        hass.async_create_task(release(), "release Automower joystick control")

    connection.subscriptions[msg["id"]] = unsubscribe
    connection.send_result(msg["id"])
    connection.send_event(msg["id"], {"token": token})


@websocket_api.websocket_command(
    {
        "type": f"{DOMAIN}/appdrive_input",
        _ENTRY: str,
        _TOKEN: str,
        vol.Required("forward"): _AXIS,
        vol.Required("steering"): _AXIS,
    }
)
@websocket_api.async_response
async def _async_input(hass, connection, msg):
    coordinator = _mower(hass, connection, msg["entry_id"])
    await coordinator.async_drive_appdrive(
        connection, msg["token"], msg["forward"], msg["steering"]
    )
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {"type": f"{DOMAIN}/appdrive_release", _ENTRY: str, _TOKEN: str}
)
@websocket_api.async_response
async def _async_release(hass, connection, msg):
    coordinator = _mower(hass, connection, msg["entry_id"])
    await coordinator.async_release_appdrive_control(connection, msg["token"])
    connection.send_result(msg["id"])
