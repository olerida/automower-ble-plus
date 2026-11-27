"""The local date used by the mower's scheduling clock."""

from datetime import date

from homeassistant.components.date import DateEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import MowerEntry
from .entity import MowerClockEntity, async_add_detected


async def async_setup_entry(
    hass: HomeAssistant, entry: MowerEntry, add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Add the date control once the mower reports its clock."""
    async_add_detected(
        entry,
        add_entities,
        {"mower_clock": None},
        lambda _key, _: MowerDate(entry.runtime_data, "mower_local_date"),
    )


class MowerDate(MowerClockEntity, DateEntity):
    """Edit the local date while keeping the mower's current time of day."""

    @property
    def native_value(self) -> date | None:
        return clock.date() if (clock := self.wall_clock) is not None else None

    async def async_set_value(self, value: date) -> None:
        await self.coordinator.async_set_clock_component(value)
