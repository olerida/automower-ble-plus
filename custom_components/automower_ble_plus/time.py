"""The local time used by the mower's scheduling clock."""

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import MowerEntry
from .entity import MowerClockEntity, async_add_detected


async def async_setup_entry(
    hass: HomeAssistant, entry: MowerEntry, add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Add the time control once the mower reports its clock."""
    async_add_detected(
        entry,
        add_entities,
        {"mower_clock": None},
        lambda _key, _: MowerTime(entry.runtime_data, "mower_local_time"),
    )


class MowerTime(MowerClockEntity, TimeEntity):
    """Edit the local time while keeping the mower's current date."""

    @property
    def native_value(self) -> time | None:
        return clock.time() if (clock := self.wall_clock) is not None else None

    async def async_set_value(self, value: time) -> None:
        await self.coordinator.async_set_clock_component(value)
