"""Switches for confirmed mower settings."""

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import MowerEntry
from .entity import MowerSettingEntity, async_add_detected


async def async_setup_entry(
    hass: HomeAssistant, entry: MowerEntry, add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Add boolean settings reported by the mower."""
    async_add_detected(
        entry,
        add_entities,
        {
            setting.key: setting
            for setting in entry.runtime_data.settings
            if not setting.options and setting.maximum == 1
        },
        lambda key, setting: AutomowerSwitch(entry.runtime_data, setting),
    )


class AutomowerSwitch(MowerSettingEntity, SwitchEntity):
    """A boolean value stored by the mower."""

    _attr_entity_category = EntityCategory.CONFIG

    @property
    def is_on(self) -> bool | None:
        return None if self.value is None else bool(self.value)

    async def async_turn_on(self, **kwargs) -> None:
        await self.async_set_value(1)

    async def async_turn_off(self, **kwargs) -> None:
        await self.async_set_value(0)
