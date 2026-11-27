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
    DEGREE,
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
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

from .aim import AIM_STATES
from .appdrive import APPDRIVE_ENUM_OPTIONS
from .connect_module import CONNECT_SIGNAL_KEY, CONNECT_STATE_KEY, CONNECT_STATE_OPTIONS
from .coordinator import MowerCoordinator, MowerEntry
from .cutting_status import GLOBAL_HEIGHT_MM
from .entity import (
    MowerEntity,
    MowerGlobalHeightEntity,
    MowerValueEntity,
    async_add_detected,
)
from .epos import EPOS_ENUM_OPTIONS
from .faults import FAULT_STATES, message_description
from .firmware import get_message_key
from .planner import (
    OVERRIDE_OPTIONS,
    RESTRICTION_OPTIONS,
    PlannerOverride,
    restriction_state,
)
from .position import GPS_NAVIGATION_STATES
from .reference_station import SCAN_OPTIONS
from .schedule import WeeklySchedule
from .wire_test import WIRE_TEST_OPTIONS


def _description(
    key: str,
    device_class: SensorDeviceClass | None = None,
    unit: str | None = None,
    state_class: SensorStateClass | None = None,
    *,
    enabled: bool = True,
    options: tuple[str, ...] = (),
) -> SensorEntityDescription:
    return SensorEntityDescription(
        key=key,
        translation_key=key,
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=device_class,
        native_unit_of_measurement=unit,
        state_class=state_class,
        entity_registry_enabled_default=enabled,
        options=list(options) if options else None,
    )


_MEASUREMENT = SensorStateClass.MEASUREMENT
_COUNTER = SensorStateClass.TOTAL_INCREASING
_DESCRIPTIONS = (
    _description("battery", SensorDeviceClass.BATTERY, PERCENTAGE, _MEASUREMENT),
    _description(
        "local_heading",
        unit=DEGREE,
        state_class=SensorStateClass.MEASUREMENT_ANGLE,
    ),
    _description("local_heading_accuracy", unit=DEGREE, state_class=_MEASUREMENT),
    _description(
        "local_position_accuracy",
        SensorDeviceClass.DISTANCE,
        UnitOfLength.MILLIMETERS,
        _MEASUREMENT,
    ),
    _description(
        "wifi_signal_strength",
        SensorDeviceClass.SIGNAL_STRENGTH,
        SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        _MEASUREMENT,
    ),
    _description(CONNECT_SIGNAL_KEY, unit=PERCENTAGE, state_class=_MEASUREMENT),
    _description(
        CONNECT_STATE_KEY, SensorDeviceClass.ENUM, options=CONNECT_STATE_OPTIONS
    ),
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
    _description("aim_map_state", SensorDeviceClass.ENUM, options=AIM_STATES),
    _description("aim_map_progress", unit=PERCENTAGE, state_class=_MEASUREMENT),
    _description("aim_map_updated", SensorDeviceClass.TIMESTAMP),
    _description(
        "gps_navigation_status", SensorDeviceClass.ENUM, options=GPS_NAVIGATION_STATES
    ),
    _description("current_cutting_height_percentage", unit=PERCENTAGE),
    _description("current_cutting_height_level"),
    _description("mower_clock"),
    _description("wire_test_status", SensorDeviceClass.ENUM, options=WIRE_TEST_OPTIONS),
    _description("wire_test_distance", SensorDeviceClass.DISTANCE, UnitOfLength.METERS),
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
    _description(
        "epos_reference_station_signal", unit=PERCENTAGE, state_class=_MEASUREMENT
    ),
    _description("epos_cloud_signal", unit=PERCENTAGE, state_class=_MEASUREMENT),
    _description(
        "epos_reference_station_distance",
        SensorDeviceClass.DISTANCE,
        UnitOfLength.MILLIMETERS,
        _MEASUREMENT,
    ),
    _description(
        "epos_reference_scan_status", SensorDeviceClass.ENUM, options=SCAN_OPTIONS
    ),
    *(
        _description(key, SensorDeviceClass.ENUM, options=options)
        for key, options in (EPOS_ENUM_OPTIONS | APPDRIVE_ENUM_OPTIONS).items()
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: MowerEntry, add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Expose supported telemetry as typed Home Assistant sensors."""
    writable = {setting.key for setting in entry.runtime_data.settings}
    if not entry.runtime_data.can_set_global_height_mm:
        async_add_detected(
            entry,
            add_entities,
            {GLOBAL_HEIGHT_MM: None},
            lambda key, _: GlobalCuttingHeightSensor(entry.runtime_data),
        )
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
        {"restriction_reason": None},
        lambda key, _: MowerRestrictionSensor(entry.runtime_data, "mowing_restriction"),
    )
    async_add_detected(
        entry,
        add_entities,
        {"error_code": None},
        lambda key, _: MowerFaultSensor(entry.runtime_data, "active_fault"),
    )
    async_add_detected(
        entry,
        add_entities,
        {"planner_override": None},
        lambda key, _: PlannerOverrideSensor(entry.runtime_data, key),
    )


class GlobalCuttingHeightSensor(MowerGlobalHeightEntity, SensorEntity):
    """Report the fixed native global height on read-only firmware."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_native_unit_of_measurement = UnitOfLength.MILLIMETERS


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
    def native_value(self) -> int | float | str | datetime | None:
        value = self.value
        if self.entity_description.options and value is not None:
            return self.entity_description.options[value]
        if (
            self.key in ("local_heading", "local_heading_accuracy")
            and value is not None
        ):
            return value / 10
        if self.key == "mower_clock" and value is not None:
            # Display the wall time reported by the mower, including any clock drift.
            return (
                datetime.fromtimestamp(value, UTC).replace(tzinfo=None).isoformat(" ")
            )
        if self.key == "aim_map_updated" and value is not None:
            return datetime.fromtimestamp(value, UTC)
        if self.key != "next_start" or value is None:
            return value
        if value in (0, 0xFFFFFFFF):
            return None
        schedule = self.coordinator.data.get("weekly_schedule")
        zone = (
            schedule.time_zone
            if isinstance(schedule, WeeklySchedule)
            else dt_util.get_time_zone(self.hass.config.time_zone) or UTC
        )
        # The protocol reports local wall-clock time in an epoch-shaped field.
        return datetime.fromtimestamp(value, UTC).replace(tzinfo=zone)


class MowerRestrictionSensor(MowerEntity, SensorEntity):
    """Translate the planner restriction while preserving its raw code."""

    entity_description = _description(
        "mowing_restriction", SensorDeviceClass.ENUM, options=RESTRICTION_OPTIONS
    )

    @property
    def _code(self) -> int | None:
        value = self.coordinator.data.get("restriction_reason")
        return value if isinstance(value, int) else None

    @property
    def available(self) -> bool:
        return super().available and self._code is not None

    @property
    def native_value(self) -> str | None:
        code = self._code
        if code is None:
            return None
        profile = self.coordinator.firmware_profile
        return restriction_state(code, profile.planner_reason_9 if profile else 0)

    @property
    def extra_state_attributes(self) -> dict[str, int] | None:
        code = self._code
        return {"code": code} if code is not None else None


class PlannerOverrideSensor(MowerEntity, SensorEntity):
    """Report an active manual mowing or parking request."""

    entity_description = _description(
        "planner_override", SensorDeviceClass.ENUM, options=OVERRIDE_OPTIONS
    )

    @property
    def override(self) -> PlannerOverride | None:
        value = self.coordinator.data.get(self.key)
        return value if isinstance(value, PlannerOverride) else None

    @property
    def available(self) -> bool:
        return super().available and self.override is not None

    @property
    def native_value(self) -> str | None:
        return override.action if (override := self.override) is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, int] | None:
        if (override := self.override) is None:
            return None
        return {
            key: value
            for key, value in (
                ("duration_seconds", override.duration_seconds),
                ("mission_id", override.mission_id),
            )
            if value is not None
        }


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
