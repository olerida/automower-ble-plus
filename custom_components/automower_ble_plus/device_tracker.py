"""BLE position reporting for Automowers with an available GPS fix."""

from functools import partial

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import MowerEntry
from .entity import MowerValueEntity, async_add_detected
from .features import POSITION_SCALE


async def async_setup_entry(
    hass: HomeAssistant, entry: MowerEntry, add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Create an optional tracker when the mower reports GPS support."""
    async_add_detected(
        entry,
        add_entities,
        {"gps_position_supported": None},
        lambda _key, _: MowerTracker(entry.runtime_data, "latitude"),
    )


class MowerTracker(MowerValueEntity, TrackerEntity):
    """A mower's geographic position as read from its BLE interface."""

    _attr_entity_registry_enabled_default = False
    _attr_source_type = SourceType.GPS

    async def async_added_to_hass(self) -> None:
        """Subscribe to the mower's position reports while the tracker is enabled."""
        await super().async_added_to_hass()
        self.coordinator.async_set_gps_tracking(True)
        self.async_on_remove(partial(self.coordinator.async_set_gps_tracking, False))
        await self.coordinator.async_request_refresh()

    @property
    def latitude(self) -> float | None:
        value = self.coordinator.data.get("latitude")
        return None if value is None else value / POSITION_SCALE

    @property
    def longitude(self) -> float | None:
        value = self.coordinator.data.get("longitude")
        return None if value is None else value / POSITION_SCALE
