"""Expose configuration text that a mower can store locally."""

from homeassistant.components.text import TextEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import MowerEntry
from .entity import MowerEntity, async_add_detected
from .identity import MAX_MOWER_NAME_LENGTH


async def async_setup_entry(
    hass: HomeAssistant, entry: MowerEntry, add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Add the name field after the first successful read from the mower."""
    async_add_detected(
        entry,
        add_entities,
        {"mower_name": None},
        lambda key, _: MowerNameText(entry.runtime_data, key),
    )


class MowerNameText(MowerEntity, TextEntity):
    """The user-assigned name saved in the mower."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min = 1
    _attr_native_max = MAX_MOWER_NAME_LENGTH

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> str | None:
        name = self.coordinator.data.get(self.key)
        return name if isinstance(name, str) else None

    async def async_set_value(self, value: str) -> None:
        """Rename the mower over its existing authenticated Bluetooth session."""
        await self.coordinator.async_set_mower_name(value)
