"""Set up an Automower by discovery or Bluetooth address."""

import asyncio
import re
from struct import error as StructError
from uuid import UUID

import probatio as vol
from automower_ble.models import MowerModels
from automower_ble.protocol import ResponseResult
from homeassistant.components import bluetooth
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS, CONF_PASSWORD, CONF_PIN
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector

from .const import CORE_DOMAIN, DOMAIN
from .features import GET_BATTERY, GET_MODEL, MODEL, U8
from .transport import AutomowerClient, CommandRejected, MowerConnectionError
from .wifi import WifiNetwork, encode_wifi_password

_SECRET = selector.TextSelector(
    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
)
_MAC = re.compile(r"(?:[A-F0-9]{2}:){5}[A-F0-9]{2}")
_PIN = re.compile(r"[0-9]{4}")


def normalize_address(value: str) -> str:
    """Normalize a MAC address or a Core Bluetooth identifier."""
    address = value.strip().upper().replace("-", ":")
    if _MAC.fullmatch(address):
        return address
    return str(UUID(value.strip())).upper()


def core_controls_address(hass: HomeAssistant, address: str) -> bool:
    """Detect an enabled core entry for the same mower."""
    return any(
        entry.disabled_by is None
        and str(entry.data.get(CONF_ADDRESS, "")).upper() == address
        for entry in hass.config_entries.async_entries(CORE_DOMAIN)
    )


class AutomowerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Validate the local BLE connection before creating an entry."""

    address: str

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Open maintenance for the configured mower."""
        return AutomowerMaintenanceFlow()

    async def async_step_bluetooth(
        self, discovery_info: bluetooth.BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Ask for credentials for a discovered mower."""
        self.address = discovery_info.address.upper()
        await self.async_set_unique_id(self.address)
        self._abort_if_unique_id_configured()
        if core_controls_address(self.hass, self.address):
            return self.async_abort(reason="core_entry_enabled")
        self.context["title_placeholders"] = {
            "name": discovery_info.name or self.address
        }
        return await self.async_step_bluetooth_confirm()

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        """Set up a mower by its BLE address and operator PIN."""
        return await self._async_configure("user", user_input)

    async def async_step_bluetooth_confirm(self, user_input=None) -> ConfigFlowResult:
        """Confirm setup with the mower's operator PIN."""
        return await self._async_configure("bluetooth_confirm", user_input)

    async def async_step_reauth(self, entry_data) -> ConfigFlowResult:
        """Start reauthentication for a configured mower."""
        self.address = entry_data[CONF_ADDRESS]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None) -> ConfigFlowResult:
        """Accept the mower's updated operator PIN."""
        return await self._async_configure("reauth_confirm", user_input)

    async def _async_configure(self, step: str, user_input) -> ConfigFlowResult:
        errors = {}
        fields = {vol.Optional(vol.Secret(CONF_PIN)): _SECRET}
        if step == "user":
            fields = {vol.Required(CONF_ADDRESS): str, **fields}
        if user_input is not None:
            try:
                if step == "user":
                    self.address = normalize_address(user_input[CONF_ADDRESS])
                    await self.async_set_unique_id(self.address)
                    self._abort_if_unique_id_configured()
            except ValueError:
                errors[CONF_ADDRESS] = "invalid_address"
            else:
                if core_controls_address(self.hass, self.address):
                    return self.async_abort(reason="core_entry_enabled")
                pin = user_input.get(CONF_PIN) or None
                if pin is not None and not _PIN.fullmatch(pin):
                    errors[CONF_PIN] = "invalid_pin"
                else:
                    error, model = await self._async_validate(pin)
                    if error is not None:
                        errors["base"] = error
                    elif step == "reauth_confirm":
                        return self.async_update_reload_and_abort(
                            self._get_reauth_entry(), data_updates={CONF_PIN: pin}
                        )
                    else:
                        return self.async_create_entry(
                            title=model or "Automower",
                            data={CONF_ADDRESS: self.address, CONF_PIN: pin},
                        )
        suggested = user_input and {CONF_ADDRESS: user_input.get(CONF_ADDRESS)}
        return self.async_show_form(
            step_id=step,
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(fields), suggested
            ),
            errors=errors,
            description_placeholders={"name": getattr(self, "address", "")},
        )

    async def _async_validate(self, pin: str | None) -> tuple[str | None, str | None]:
        device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if device is None:
            return "cannot_connect", None
        client = AutomowerClient(self.address, pin)
        try:
            result = await client.connect(device)
            if result in (ResponseResult.INVALID_PIN, ResponseResult.NOT_ALLOWED):
                return "invalid_auth", None
            if result != ResponseResult.OK:
                return "cannot_connect", None
            model_id = MODEL.unpack(await client.command(GET_MODEL))
            U8.unpack(await client.command(GET_BATTERY))
        except CommandRejected as err:
            if err.result in (ResponseResult.INVALID_PIN, ResponseResult.NOT_ALLOWED):
                return "invalid_auth", None
            return "cannot_connect", None
        except MowerConnectionError, StructError:
            return "cannot_connect", None
        finally:
            await client.disconnect()
        identity = MowerModels.get(model_id)
        return None, identity.model if identity else None


class AutomowerMaintenanceFlow(OptionsFlow):
    """Manage one configured mower's maintenance features."""

    def __init__(self) -> None:
        self._scan_task: asyncio.Task[tuple[WifiNetwork, ...]] | None = None
        self._network_task: asyncio.Task[None] | None = None
        self._selected_network: WifiNetwork | None = None
        self._saved_networks: tuple[str, ...] | None = None
        self._network_result = "wifi_joined"
        self._epos_origin_site: int | None = None
        self._epos_origin_task: asyncio.Task[bool] | None = None
        self._epos_origin_result = "epos_origin_set"
        self._epos_boundary_context: tuple[int, int | None] | None = None

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        """Offer maintenance for the capabilities this mower reports."""
        entry = self.config_entry
        if entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="mower_unavailable")
        coordinator = entry.runtime_data
        options = [
            step
            for step, supported in (
                ("wifi", coordinator.can_configure_wifi),
                ("reset", coordinator.can_reset_user_settings),
                ("change_pin", coordinator.can_change_pin),
                ("regenerate_aim_map", coordinator.can_regenerate_aim_map),
                (
                    "setup_epos_origin",
                    coordinator.epos_origin_installation_id is not None,
                ),
                ("change_epos_boundary", coordinator.can_change_epos_boundary),
            )
            if supported
        ]
        if len(options) > 1:
            return self.async_show_menu(step_id="init", menu_options=options)
        step = options[0] if options else "reset"
        return await getattr(self, f"async_step_{step}")()

    async def async_step_change_epos_boundary(
        self, user_input=None
    ) -> ConfigFlowResult:
        """Confirm the target perimeter and the reset it can trigger."""
        entry = self.config_entry
        if entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="mower_unavailable")
        coordinator = entry.runtime_data
        if not coordinator.can_change_epos_boundary:
            return self.async_abort(reason="epos_boundary_unavailable")
        if self._epos_boundary_context is None:
            try:
                self._epos_boundary_context = (
                    await coordinator.async_epos_boundary_context()
                )
            except HomeAssistantError:
                return self.async_abort(reason="epos_boundary_unavailable")

        current = self._epos_boundary_context[0]
        errors = {}
        if user_input is not None:
            enabled = user_input["epos_boundary"] == "epos"
            if enabled == current:
                errors["epos_boundary"] = "epos_boundary_already_selected"
            elif not user_input["confirm_epos_boundary"]:
                errors["confirm_epos_boundary"] = "epos_boundary_confirmation_required"
            else:
                try:
                    confirmed = await coordinator.async_set_epos_boundary(
                        enabled, self._epos_boundary_context
                    )
                except HomeAssistantError:
                    return self.async_abort(reason="epos_boundary_failed")
                return self.async_abort(
                    reason="epos_boundary_changed"
                    if confirmed
                    else "epos_boundary_unconfirmed"
                )

        return self.async_show_form(
            step_id="change_epos_boundary",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "epos_boundary", default="boundary_wire" if current else "epos"
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=["boundary_wire", "epos"],
                            translation_key="epos_boundary_target",
                        )
                    ),
                    vol.Required(
                        "confirm_epos_boundary", default=False
                    ): selector.BooleanSelector(),
                }
            ),
            errors=errors,
            description_placeholders={"name": entry.title},
        )

    async def async_step_setup_epos_origin(self, user_input=None) -> ConfigFlowResult:
        """Ask to set up Husqvarna Cloud corrections for the selected empty profile."""
        entry = self.config_entry
        if entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="mower_unavailable")
        coordinator = entry.runtime_data
        active = coordinator.epos_origin_installation_id
        if active is None or (
            self._epos_origin_site is not None and self._epos_origin_site != active
        ):
            return self.async_abort(reason="epos_origin_unavailable")
        self._epos_origin_site = active
        errors = {}
        if user_input is not None:
            if not user_input["confirm_epos_origin"]:
                errors["confirm_epos_origin"] = "epos_origin_confirmation_required"
            else:
                self._epos_origin_task = entry.async_create_background_task(
                    self.hass,
                    coordinator.async_setup_epos_origin(active),
                    "configure the Automower EPOS cloud origin",
                )
                return await self.async_step_epos_origin_progress()
        return self.async_show_form(
            step_id="setup_epos_origin",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "confirm_epos_origin", default=False
                    ): selector.BooleanSelector()
                }
            ),
            errors=errors,
            description_placeholders={"name": entry.title},
        )

    async def async_step_epos_origin_progress(
        self, user_input=None
    ) -> ConfigFlowResult:
        """Show progress while the mower is determining a valid origin."""
        assert self._epos_origin_task is not None
        if not self._epos_origin_task.done():
            return self.async_show_progress(
                step_id="epos_origin_progress",
                progress_action="epos_origin_waiting",
                progress_task=self._epos_origin_task,
            )
        try:
            if not self._epos_origin_task.result():
                self._epos_origin_result = "epos_origin_unconfirmed"
        except TimeoutError:
            self._epos_origin_result = "epos_origin_timeout"
        except HomeAssistantError:
            self._epos_origin_result = "epos_origin_failed"
        return self.async_show_progress_done(next_step_id="epos_origin_result")

    async def async_step_epos_origin_result(self, user_input=None) -> ConfigFlowResult:
        """Report whether the mower established a valid origin."""
        return self.async_abort(reason=self._epos_origin_result)

    async def async_step_regenerate_aim_map(self, user_input=None) -> ConfigFlowResult:
        """Confirm replacement of the current AIM map."""
        entry = self.config_entry
        if (
            entry.state is not ConfigEntryState.LOADED
            or not entry.runtime_data.can_regenerate_aim_map
        ):
            return self.async_abort(reason="aim_map_unavailable")
        errors = {}
        if user_input is not None:
            if not user_input["confirm_aim_map"]:
                errors["confirm_aim_map"] = "aim_confirmation_required"
            else:
                try:
                    acknowledged = await entry.runtime_data.async_regenerate_aim_map()
                except HomeAssistantError:
                    if not entry.runtime_data.can_regenerate_aim_map:
                        return self.async_abort(reason="aim_map_unavailable")
                    errors["base"] = "aim_map_failed"
                else:
                    return self.async_abort(
                        reason="aim_map_requested"
                        if acknowledged
                        else "aim_map_unconfirmed"
                    )
        return self.async_show_form(
            step_id="regenerate_aim_map",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "confirm_aim_map", default=False
                    ): selector.BooleanSelector()
                }
            ),
            errors=errors,
            description_placeholders={"name": entry.title},
        )

    async def async_step_change_pin(self, user_input=None) -> ConfigFlowResult:
        """Use masked inputs for an operator-authorized PIN change."""
        entry = self.config_entry
        if (
            entry.state is not ConfigEntryState.LOADED
            or not entry.runtime_data.can_change_pin
        ):
            return self.async_abort(reason="pin_change_unavailable")

        errors = {}
        if user_input is not None:
            current_pin = user_input["current_pin"]
            new_pin = user_input["new_pin"]
            confirmation = user_input["confirm_pin"]
            if not _PIN.fullmatch(current_pin):
                errors["current_pin"] = "invalid_pin"
            elif current_pin != entry.data.get(CONF_PIN):
                errors["current_pin"] = "pin_current_mismatch"
            if not _PIN.fullmatch(new_pin):
                errors["new_pin"] = "invalid_pin"
            elif new_pin == current_pin:
                errors["new_pin"] = "pin_unchanged"
            if new_pin != confirmation:
                errors["confirm_pin"] = "pin_confirmation_mismatch"
            if not errors:
                try:
                    acknowledged = await entry.runtime_data.async_change_pin(
                        current_pin, new_pin
                    )
                except HomeAssistantError:
                    if not entry.runtime_data.can_change_pin:
                        return self.async_abort(reason="pin_change_unavailable")
                    errors["base"] = "pin_change_failed"
                else:
                    return self.async_abort(
                        reason="pin_changed"
                        if acknowledged
                        else "pin_change_unconfirmed"
                    )

        return self.async_show_form(
            step_id="change_pin",
            data_schema=vol.Schema(
                {
                    vol.Required(vol.Secret(field)): _SECRET
                    for field in ("current_pin", "new_pin", "confirm_pin")
                }
            ),
            errors=errors,
            description_placeholders={"name": entry.title},
        )

    async def async_step_reset(self, user_input=None) -> ConfigFlowResult:
        """Show the effect of a reset before sending the BLE command."""
        entry = self.config_entry
        if entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="mower_unavailable")
        coordinator = entry.runtime_data
        if not coordinator.can_reset_user_settings:
            return self.async_abort(reason="reset_unavailable")

        errors = {}
        if user_input is not None:
            if not user_input["confirm_reset"]:
                errors["confirm_reset"] = "confirmation_required"
            else:
                try:
                    acknowledged = await coordinator.async_reset_user_settings()
                except HomeAssistantError:
                    errors["base"] = "reset_failed"
                else:
                    return self.async_abort(
                        reason="reset_requested"
                        if acknowledged
                        else "reset_unconfirmed"
                    )

        return self.async_show_form(
            step_id="reset",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "confirm_reset", default=False
                    ): selector.BooleanSelector(),
                }
            ),
            errors=errors,
            description_placeholders={"name": entry.title},
        )

    async def async_step_wifi(self, user_input=None) -> ConfigFlowResult:
        """Offer scanning and management of saved networks."""
        entry = self.config_entry
        if (
            entry.state is not ConfigEntryState.LOADED
            or not entry.runtime_data.can_configure_wifi
        ):
            return self.async_abort(reason="wifi_unavailable")
        return self.async_show_menu(
            step_id="wifi", menu_options=["wifi_scan", "wifi_saved"]
        )

    async def async_step_wifi_scan(self, user_input=None) -> ConfigFlowResult:
        """Scan at the mower while Home Assistant shows progress."""
        if self._scan_task is None:
            entry = self.config_entry
            self._scan_task = entry.async_create_background_task(
                self.hass,
                entry.runtime_data.async_scan_wifi(),
                "scan Wi-Fi networks from the Automower",
            )
        if not self._scan_task.done():
            return self.async_show_progress(
                step_id="wifi_scan",
                progress_action="wifi_scanning",
                progress_task=self._scan_task,
            )
        return self.async_show_progress_done(next_step_id="wifi_choose")

    async def async_step_wifi_choose(self, user_input=None) -> ConfigFlowResult:
        """Choose a network the mower found."""
        assert self._scan_task is not None
        try:
            networks = self._scan_task.result()
        except HomeAssistantError:
            return self.async_abort(reason="wifi_scan_failed")
        if not networks:
            return self.async_abort(reason="wifi_no_networks")
        if user_input is not None:
            self._selected_network = networks[int(user_input["network"])]
            if self._selected_network.auth:
                return await self.async_step_wifi_password()
            return await self._async_request_wifi_join(self._selected_network.name, "")
        return self.async_show_form(
            step_id="wifi_choose",
            data_schema=vol.Schema(
                {
                    vol.Required("network"): self._network_selector(
                        [network.label for network in networks]
                    )
                }
            ),
        )

    async def async_step_wifi_password(self, user_input=None) -> ConfigFlowResult:
        """Read a password for the current BLE request only."""
        assert self._selected_network is not None
        errors = {}
        if user_input is not None:
            password = user_input[CONF_PASSWORD]
            try:
                if not password:
                    raise ValueError
                encode_wifi_password(password)
            except ValueError:
                errors[CONF_PASSWORD] = "wifi_invalid_password"
            else:
                return await self._async_request_wifi_join(
                    self._selected_network.name, password
                )
        return self.async_show_form(
            step_id="wifi_password",
            data_schema=vol.Schema({vol.Required(vol.Secret(CONF_PASSWORD)): _SECRET}),
            errors=errors,
        )

    async def async_step_wifi_saved(self, user_input=None) -> ConfigFlowResult:
        """Choose whether to connect to or remove a saved network."""
        coordinator = self.config_entry.runtime_data
        if self._saved_networks is None:
            try:
                self._saved_networks = await coordinator.async_list_saved_wifi()
            except HomeAssistantError:
                return self.async_abort(reason="wifi_saved_failed")
        if not self._saved_networks:
            return self.async_abort(reason="wifi_no_saved")
        if user_input is not None:
            name = self._saved_networks[int(user_input["network"])]
            if user_input["operation"] == "connect":
                return await self._async_request_wifi_join(name, None)
            try:
                await coordinator.async_forget_wifi(name)
            except HomeAssistantError:
                return self.async_abort(reason="wifi_forget_failed")
            return self.async_abort(reason="wifi_forgotten")
        return self.async_show_form(
            step_id="wifi_saved",
            data_schema=vol.Schema(
                {
                    vol.Required("network"): self._network_selector(
                        list(self._saved_networks)
                    ),
                    vol.Required(
                        "operation", default="connect"
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=["connect", "forget"],
                            translation_key="wifi_operation",
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
        )

    async def _async_request_wifi_join(
        self, name: str, password: str | None
    ) -> ConfigFlowResult:
        entry = self.config_entry
        self._network_task = entry.async_create_background_task(
            self.hass,
            entry.runtime_data.async_join_wifi(name, password),
            "connect the Automower to Wi-Fi",
        )
        return await self.async_step_wifi_join()

    async def async_step_wifi_join(self, user_input=None) -> ConfigFlowResult:
        """Show progress while the mower attempts to join the network."""
        assert self._network_task is not None
        if not self._network_task.done():
            return self.async_show_progress(
                step_id="wifi_join",
                progress_action="wifi_joining",
                progress_task=self._network_task,
            )
        try:
            self._network_task.result()
        except HomeAssistantError:
            self._network_result = "wifi_join_failed"
        return self.async_show_progress_done(next_step_id="wifi_join_result")

    async def async_step_wifi_join_result(self, user_input=None) -> ConfigFlowResult:
        """Report the firmware's connection outcome."""
        return self.async_abort(reason=self._network_result)

    @staticmethod
    def _network_selector(labels: list[str]) -> selector.SelectSelector:
        return selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    selector.SelectOptionDict(value=str(index), label=label)
                    for index, label in enumerate(labels)
                ],
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )
