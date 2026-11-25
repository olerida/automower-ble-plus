"""Downloadable support information for Automower BLE Plus."""

from dataclasses import asdict

from homeassistant.core import HomeAssistant

from .coordinator import MowerEntry
from .fota import FirmwareStatus
from .planner import PlannerOverride
from .sites import InstallationSelection


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: MowerEntry
) -> dict:
    """Report model and BLE capabilities without the PIN or device address."""
    coordinator = entry.runtime_data
    return {
        "model": coordinator.device_info.get("model"),
        "model_id": coordinator.device_info.get("model_id"),
        "firmware": coordinator.device_info.get("sw_version"),
        "connected": coordinator.client.is_connected(),
        "available_settings": sorted(coordinator.setting_variants),
        "telemetry": {
            key: asdict(value)
            if isinstance(value, (FirmwareStatus, PlannerOverride))
            else value
            for key, value in coordinator.data.items()
            if key not in ("latitude", "longitude", "mower_name")
            and not isinstance(value, InstallationSelection)
        },
    }
