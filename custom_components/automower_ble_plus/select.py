"""Select entities for mower settings with enumerated values."""

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import MowerCoordinator, MowerEntry
from .entity import MowerSettingEntity, async_add_detected
from .features import Setting


async def async_setup_entry(
    hass: HomeAssistant, entry: MowerEntry, add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Add supported settings with a fixed set of options."""
    async_add_detected(
        entry,
        add_entities,
        {
            setting.key: setting
            for setting in entry.runtime_data.settings
            if setting.options
        },
        lambda key, setting: AutomowerSelect(entry.runtime_data, setting),
    )


class AutomowerSelect(MowerSettingEntity, SelectEntity):
    """An enumerated setting written over BLE."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: MowerCoordinator, setting: Setting) -> None:
        super().__init__(coordinator, setting)
        self._attr_options = list(setting.options)
        self._option_values = setting.option_values or tuple(
            range(len(setting.options))
        )

    @property
    def current_option(self) -> str | None:
        value = self.value
        if value is None or value not in self._option_values:
            return None
        return self.options[self._option_values.index(value)]

    async def async_select_option(self, option: str) -> None:
        await self.async_set_value(self._option_values[self.options.index(option)])
