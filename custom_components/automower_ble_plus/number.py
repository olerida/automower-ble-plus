"""Number entities for supported mower settings."""

from homeassistant.components.number import NumberDeviceClass, NumberEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import MowerCoordinator, MowerEntry
from .entity import MowerSettingEntity, async_add_detected
from .features import Setting


async def async_setup_entry(
    hass: HomeAssistant, entry: MowerEntry, add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Add numeric settings after their range and availability are established."""
    async_add_detected(
        entry,
        add_entities,
        {
            setting.key: setting
            for setting in entry.runtime_data.settings
            if not setting.options and setting.maximum > 1
        },
        lambda key, setting: AutomowerNumber(entry.runtime_data, setting),
    )


class AutomowerNumber(MowerSettingEntity, NumberEntity):
    """A range-checked integer setting stored by the mower."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: MowerCoordinator, setting: Setting) -> None:
        super().__init__(coordinator, setting)
        self._attr_native_min_value = setting.minimum
        self._attr_native_max_value = setting.maximum
        self._attr_native_unit_of_measurement = setting.unit
        self._attr_native_step = setting.step
        if setting.unit == "s":
            self._attr_device_class = NumberDeviceClass.DURATION

    @property
    def native_value(self) -> int | None:
        return self.value

    async def async_set_native_value(self, value: float) -> None:
        if not float(value).is_integer():
            raise ServiceValidationError("Enter a whole number accepted by the mower")
        await self.async_set_value(int(value))
