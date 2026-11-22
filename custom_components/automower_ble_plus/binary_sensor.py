"""Binary telemetry reported by the mower."""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import MowerEntry
from .entity import MowerValueEntity, async_add_detected


async def async_setup_entry(
    hass: HomeAssistant, entry: MowerEntry, add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Add charging telemetry when available."""
    async_add_detected(
        entry,
        add_entities,
        {"charging": None},
        lambda key, _: ChargingSensor(entry.runtime_data, key),
    )


class ChargingSensor(MowerValueEntity, BinarySensorEntity):
    """Whether the mower reports it is charging."""

    _attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool | None:
        return None if self.value is None else bool(self.value)
