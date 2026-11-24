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
    """Add charging and theft alarm telemetry when available."""
    async_add_detected(
        entry,
        add_entities,
        {"charging": None},
        lambda key, _: ChargingSensor(entry.runtime_data, key),
    )
    async_add_detected(
        entry,
        add_entities,
        {
            "theft_motion_alarm": None,
            "theft_geofence_alarm": None,
            "theft_stop_alarm": None,
        },
        lambda key, _: TheftAlarmSensor(entry.runtime_data, key),
    )


class ChargingSensor(MowerValueEntity, BinarySensorEntity):
    """Whether the mower reports it is charging."""

    _attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool | None:
        return None if self.value is None else bool(self.value)


class TheftAlarmSensor(MowerValueEntity, BinarySensorEntity):
    """Indicate an active alarm reported by the firmware."""

    _attr_device_class = BinarySensorDeviceClass.TAMPER
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool | None:
        value = self.value
        return value == 2 if value in (0, 1, 2, 3, 4) else None
