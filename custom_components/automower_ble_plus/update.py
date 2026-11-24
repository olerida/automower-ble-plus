"""Updates for firmware packages offered by the mower and its charging station."""

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import MowerEntry
from .entity import MowerEntity, async_add_detected
from .fota import FIRMWARE_TARGETS, UpdatePhase


async def async_setup_entry(
    hass: HomeAssistant, entry: MowerEntry, add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Expose firmware updates only for recipients discovered over BLE."""
    async_add_detected(
        entry,
        add_entities,
        FIRMWARE_TARGETS,
        lambda key, _: AutomowerFirmwareUpdate(entry.runtime_data, key),
    )


class AutomowerFirmwareUpdate(MowerEntity, UpdateEntity):
    """Firmware installation using the vendor package already in the device."""

    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS
    )

    @property
    def available(self) -> bool:
        return super().available and self.key in self.coordinator.data

    @property
    def installed_version(self) -> str | None:
        status = self.coordinator.firmware_status(self.key)
        return status.installed if status else None

    @property
    def latest_version(self) -> str | None:
        status = self.coordinator.firmware_status(self.key)
        return (status.offered or status.installed) if status else None

    @property
    def in_progress(self) -> bool:
        status = self.coordinator.firmware_status(self.key)
        return status is not None and status.phase in (
            UpdatePhase.DOWNLOADING,
            UpdatePhase.INSTALLING,
        )

    @property
    def update_percentage(self) -> int | None:
        status = self.coordinator.firmware_status(self.key)
        return status.progress if status else None

    def version_is_newer(self, latest_version: str, installed_version: str) -> bool:
        return latest_version != installed_version

    async def async_install(self, version: str | None, backup: bool, **kwargs) -> None:
        await self.coordinator.async_firmware_action(
            self.key, expected_version=version or self.latest_version
        )
