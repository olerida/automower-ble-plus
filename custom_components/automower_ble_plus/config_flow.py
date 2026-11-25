"""Set up an Automower by discovery or Bluetooth address."""

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
from homeassistant.const import CONF_ADDRESS, CONF_PIN
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector

from .const import CORE_DOMAIN, DOMAIN
from .features import GET_BATTERY, GET_MODEL, MODEL, U8
from .transport import AutomowerClient, CommandRejected, MowerConnectionError

_PIN = selector.TextSelector(
    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
)
_MAC = re.compile(r"(?:[A-F0-9]{2}:){5}[A-F0-9]{2}")


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
        fields = {vol.Optional(vol.Secret(CONF_PIN)): _PIN}
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
                if pin is not None and not re.fullmatch(r"[0-9]{4}", pin):
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
    """Confirm a settings reset for exactly one configured mower."""

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
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
            step_id="init",
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
