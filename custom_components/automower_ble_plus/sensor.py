"""Numeric and time telemetry reported by an Automower."""

from datetime import UTC, datetime

from automower_ble.protocol import MowerState
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfLength,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import MowerCoordinator, MowerEntry
from .entity import MowerEntity, MowerValueEntity, async_add_detected
from .faults import FAULT_STATES, message_description
from .firmware import get_message_key


def _description(
    key: str,
    device_class: SensorDeviceClass | None = None,
    unit: str | None = None,
    state_class: SensorStateClass | None = None,
    *,
    enabled: bool = True,
) -> SensorEntityDescription:
    return SensorEntityDescription(
        key=key,
        translation_key=key,
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=device_class,
        native_unit_of_measurement=unit,
        state_class=state_class,
        entity_registry_enabled_default=enabled,
    )


_MEASUREMENT = SensorStateClass.MEASUREMENT
_COUNTER = SensorStateClass.TOTAL_INCREASING
_DESCRIPTIONS = (
    _description("battery", SensorDeviceClass.BATTERY, PERCENTAGE, _MEASUREMENT),
    _description(
        "battery_voltage",
        SensorDeviceClass.VOLTAGE,
        UnitOfElectricPotential.MILLIVOLT,
        _MEASUREMENT,
        enabled=False,
    ),
    _description(
        "battery_current",
        SensorDeviceClass.CURRENT,
        UnitOfElectricCurrent.MILLIAMPERE,
        _MEASUREMENT,
        enabled=False,
    ),
    _description(
        "battery_temperature",
        SensorDeviceClass.TEMPERATURE,
        UnitOfTemperature.CELSIUS,
        _MEASUREMENT,
    ),
    _description("next_start", SensorDeviceClass.TIMESTAMP),
    _description(
        "remaining_charging_time", SensorDeviceClass.DURATION, UnitOfTime.SECONDS
    ),
    _description("operation_mode"),
    _description("error_code"),
    _description("restriction_reason"),
    _description(
        "drive_past_wire", SensorDeviceClass.DISTANCE, UnitOfLength.MILLIMETERS
    ),
    _description(
        "reversing_distance", SensorDeviceClass.DISTANCE, UnitOfLength.MILLIMETERS
    ),
    _description(
        "total_running_time", SensorDeviceClass.DURATION, UnitOfTime.SECONDS, _COUNTER
    ),
    _description(
        "total_cutting_time", SensorDeviceClass.DURATION, UnitOfTime.SECONDS, _COUNTER
    ),
    _description(
        "total_charging_time", SensorDeviceClass.DURATION, UnitOfTime.SECONDS, _COUNTER
    ),
    _description(
        "total_searching_time", SensorDeviceClass.DURATION, UnitOfTime.SECONDS, _COUNTER
    ),
    _description("collisions", state_class=_COUNTER),
    _description("charging_cycles", state_class=_COUNTER),
    _description(
        "blade_usage_time", SensorDeviceClass.DURATION, UnitOfTime.SECONDS, _COUNTER
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: MowerEntry, add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Expose supported telemetry as typed Home Assistant sensors."""
    writable = {setting.key for setting in entry.runtime_data.settings}
    async_add_detected(
        entry,
        add_entities,
        {
            description.key: description
            for description in _DESCRIPTIONS
            if description.key not in writable
        },
        lambda key, description: AutomowerSensor(entry.runtime_data, key, description),
    )
    async_add_detected(
        entry,
        add_entities,
        {"error_code": None},
        lambda key, _: MowerFaultSensor(entry.runtime_data, "active_fault"),
    )


class AutomowerSensor(MowerValueEntity, SensorEntity):
    """A diagnostic reading returned by the mower."""

    def __init__(
        self,
        coordinator: MowerCoordinator,
        key: str,
        description: SensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, key)
        self.entity_description = description

    @property
    def native_value(self) -> int | datetime | None:
        value = self.value
        if self.key != "next_start" or value is None:
            return value
        if value in (0, 0xFFFFFFFF):
            return None
        zone = dt_util.get_time_zone(self.hass.config.time_zone) or UTC
        # The protocol reports local wall-clock time in an epoch-shaped field.
        return datetime.fromtimestamp(value, UTC).replace(tzinfo=zone)


class MowerFaultSensor(MowerEntity, SensorEntity):
    """Show a translated mower fault and the underlying protocol code."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_options = sorted({*FAULT_STATES.values(), "other"})

    @property
    def _code(self) -> int | None:
        code = self.coordinator.data.get("error_code")
        return code if isinstance(code, int) else None

    @property
    def available(self) -> bool:
        return super().available and self._code is not None

    @property
    def native_value(self) -> str | None:
        code = self._code
        if code is None:
            return None
        if not code and self.coordinator.data["state"] in (
            MowerState.ERROR,
            MowerState.FATAL_ERROR,
        ):
            return "other"
        return FAULT_STATES.get(code, "other")

    @property
    def extra_state_attributes(self) -> dict | None:
        code = self._code
        if code is None:
            return None
        data = {"code": code}
        if key := get_message_key(code):
            data["message_key"] = key
        if (
            code
            and code not in FAULT_STATES
            and (description := message_description(code))
        ):
            data["message"] = description
        return data
