"""BLE position reporting for Automowers with an available GPS fix."""

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import MowerEntry
from .entity import MowerValueEntity, async_add_detected
from .features import POSITION_SCALE


async def async_setup_entry(
    hass: HomeAssistant, entry: MowerEntry, add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Create a tracker after the first usable mower position."""
    async_add_detected(
        entry,
        add_entities,
        {"latitude": None},
        lambda key, _: MowerTracker(entry.runtime_data, key),
    )


class MowerTracker(MowerValueEntity, TrackerEntity):
    """A mower's geographic position as read from its BLE interface."""

    _attr_entity_registry_enabled_default = False
    _attr_source_type = SourceType.GPS

    @property
    def latitude(self) -> float | None:
        value = self.coordinator.data.get("latitude")
        return None if value is None else value / POSITION_SCALE

    @property
    def longitude(self) -> float | None:
        value = self.coordinator.data.get("longitude")
        return None if value is None else value / POSITION_SCALE
