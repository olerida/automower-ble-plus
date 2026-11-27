"""Model-dependent BLE capabilities exposed as Home Assistant entities."""

from dataclasses import dataclass
from struct import Struct
from struct import error as StructError

from .firmware import FirmwareProfile
from .models import (
    BOUNDARY_RANGES,
    LIFT_TILT_ALARM_MODELS,
    MOTION_ALARM_MODELS,
    STARTING_POINT_COUNTS,
)
from .wire import Command

U8 = Struct("<B")
I16 = Struct("<h")
U16 = Struct("<H")
U32 = Struct("<I")
I32 = Struct("<i")
MODEL = Struct("<BB")
STATISTICS = Struct("<7I")
POSITION = Struct("<ii")
POSITION_SCALE = 10_000_000

GET_MODEL = Command(4698, 9)
GET_FIRMWARE = Command(4698, 22)
GET_STATISTICS = Command(4726, 0)
GET_STATE = Command(4586, 2)
GET_ACTIVITY = Command(4586, 3)
GET_BATTERY = Command(4106, 20)
GET_POSITION_AVAILABLE = Command(5264, 4)
GET_POSITION = Command(5264, 0)
GET_GUIDE_COUNT = Command(4706, 14)
GET_LEGACY_BOUNDARY_CORRIDOR = Command(4706, 0)
SET_LEGACY_BOUNDARY_CORRIDOR = Command(4706, 1)
VISION_ACCESSORY_AVAILABLE = Command(7276, 1)
VISION_OBSTACLE_AVOIDANCE_AVAILABLE = Command(7338, 2)
BOUNDARY_EDGE_DETECTION_AVAILABLE = Command(7414, 2)
VISION_POSITIONING_AVAILABLE = Command(6170, 14)
IS_DAYLIGHT = Command(5472, 2)
CONNECT_FAMILY = 4738
CONNECT_ENABLED_KEY = "automower_connect_enabled"


@dataclass(frozen=True, slots=True)
class Reading:
    """A command with a fixed-width scalar response."""

    command: Command
    codec: Struct
    arguments: bytes = b""

    def decode(self, payload: bytes) -> int:
        """Require the full width of the corresponding TIF field."""
        try:
            return self.codec.unpack(payload)[0]
        except StructError as err:
            raise ValueError(f"Unexpected response size for {self.command}") from err


MINIMUM_LEGACY_BOUNDARY_CORRIDOR = Reading(GET_LEGACY_BOUNDARY_CORRIDOR, Struct("<Bx"))


@dataclass(frozen=True, slots=True)
class SettingVariant:
    """One firmware variant of a readable and writable setting."""

    read: Reading
    write: Command
    availability: Command | None = None
    requires_pin: bool = False
    pin_after_value: bool = False


@dataclass(frozen=True, slots=True)
class Setting:
    """A setting whose entity is created after a successful capability probe."""

    key: str
    variants: tuple[SettingVariant, ...]
    options: tuple[str, ...] = ()
    minimum: int = 0
    maximum: int = 1
    unit: str | None = None
    step: int = 1
    translation_key: str | None = None
    index: int | None = None
    option_values: tuple[int, ...] = ()

    def encode(self, variant: SettingVariant, value: int) -> bytes:
        """Check the app's value range before sending a new setting."""
        if not self.minimum <= value <= self.maximum:
            raise ValueError(
                f"{self.key} must be between {self.minimum} and {self.maximum}"
            )
        if (value - self.minimum) % self.step:
            raise ValueError(f"{self.key} must be set in steps of {self.step}")
        if self.option_values and value not in self.option_values:
            raise ValueError(f"{self.key} does not accept {value}")
        return variant.read.arguments + variant.read.codec.pack(value)


def _variant(
    family: int,
    get: int,
    set_: int,
    *,
    available: int | None = None,
    codec: Struct = U8,
    arguments: bytes = b"",
    requires_pin: bool = False,
    pin_after_value: bool = False,
) -> SettingVariant:
    return SettingVariant(
        Reading(Command(family, get), codec, arguments),
        Command(family, set_),
        Command(family, available) if available is not None else None,
        requires_pin,
        pin_after_value,
    )


_SETTINGS = (
    Setting("eco_mode", (_variant(4692, 6, 5),)),
    Setting("mower_house", (_variant(4692, 4, 3),)),
    Setting("wifi_enabled", (_variant(5916, 2, 3, available=0),)),
    Setting("weather_timer", (_variant(4460, 4, 5, available=2),)),
    Setting("spiral_cutting", (_variant(4710, 1, 2, available=0),)),
    Setting(
        "spiral_cutting_sensitivity",
        (_variant(4710, 3, 4, available=0),),
        options=("very_low", "low", "medium", "high", "very_high"),
        maximum=4,
    ),
    Setting(
        "weather_timer_sensitivity",
        (_variant(4460, 6, 7, available=2),),
        options=("very_low", "low", "medium", "high", "very_high"),
        maximum=4,
    ),
    Setting(
        "frost_guard",
        (_variant(5370, 1, 2, available=0), _variant(5412, 1, 2, available=0)),
    ),
    Setting(
        "cutting_height", (_variant(4422, 2, 3, available=5),), minimum=1, maximum=9
    ),
    Setting("automatic_height_reduction", (_variant(4422, 8, 9, available=10),)),
    Setting("gps_assisted_navigation", (_variant(4732, 2, 1, available=0),)),
    Setting("obstacle_avoidance", (_variant(5356, 4, 3, available=2),)),
    Setting("obstacle_avoidance_at_boundary", (_variant(5356, 6, 5, available=2),)),
    Setting("ultrasonic", (_variant(4728, 1, 2, available=0),)),
    Setting(
        "flexifence",
        (_variant(5926, 2, 3, available=0), _variant(6050, 2, 3, available=0)),
    ),
    Setting("edge_cut", (_variant(6690, 2, 1, available=3),)),
    Setting("boundary_edge_detection", (_variant(7414, 5, 4, available=2),)),
    Setting("vision_positioning", (_variant(6170, 15, 16, available=14),)),
    Setting("epos_wire_assistance", (_variant(6170, 8, 9, available=7),)),
    Setting("vision_keep_running_reduced", (_variant(7338, 8, 7, available=2),)),
    Setting("mower_firmware_auto_check", (_variant(5296, 3, 2, available=1),)),
    Setting("station_firmware_auto_check", (_variant(6244, 3, 2, available=1),)),
    Setting(
        "headlight_schedule",
        (_variant(4722, 2, 1, available=0),),
        options=("always_on", "always_off", "evening", "evening_and_night"),
        maximum=3,
    ),
    Setting("headlight_error_indication", (_variant(4722, 4, 3, available=0),)),
    Setting("automatic_clock_sync", (_variant(6746, 7, 8),)),
    Setting("use_24_hour_time", (_variant(5100, 10, 11),)),
    Setting("display_cutting_height_in_inches", (_variant(5100, 8, 9),)),
    Setting("automatic_passage_handling", (_variant(4706, 4, 5),)),
    Setting(
        "station_search_range",
        (_variant(4708, 5, 6),),
        options=("minimum", "medium", "maximum"),
        maximum=2,
    ),
)


_THEFT_SETTINGS = (
    Setting("stop_pin_required", (_variant(4736, 1, 2),)),
    Setting("theft_time_lock", (_variant(4736, 5, 6),)),
    Setting("theft_alarm_on_stop", (_variant(4736, 7, 8),)),
    Setting(
        "theft_alarm_duration",
        (_variant(4736, 15, 16, codec=U32),),
        minimum=60,
        maximum=600,
        unit="s",
        step=60,
    ),
    Setting(
        "theft_time_lock_duration",
        (_variant(4736, 3, 4, codec=U32),),
        minimum=86400,
        maximum=8640000,
        unit="s",
        step=86400,
    ),
)


def settings_for_model(
    model: tuple[int, int],
    guide_count: int | None,
    limits: FirmwareProfile | None = None,
    *,
    has_pin: bool = False,
) -> tuple[Setting, ...]:
    """Select settings with verified value ranges for this mower model."""
    bounds = BOUNDARY_RANGES.get(model)
    settings = list(_SETTINGS)
    if has_pin:
        settings.append(
            Setting(
                CONNECT_ENABLED_KEY,
                (
                    _variant(
                        CONNECT_FAMILY,
                        2,
                        1,
                        available=0,
                        requires_pin=True,
                        pin_after_value=True,
                    ),
                ),
            )
        )
        settings.extend(_THEFT_SETTINGS)
        if model in LIFT_TILT_ALARM_MODELS:
            settings.extend(
                (
                    Setting("theft_alarm_on_lift", (_variant(4736, 9, 10),)),
                    Setting("theft_alarm_on_tilt", (_variant(4736, 11, 12),)),
                )
            )
        if model in MOTION_ALARM_MODELS:
            settings.append(Setting("theft_alarm_on_motion", (_variant(4736, 13, 14),)))
    elif limits and limits.connect_enabled_no_pin:
        settings.append(
            Setting(
                CONNECT_ENABLED_KEY, (_variant(CONNECT_FAMILY, 2, 17, available=0),)
            )
        )
    if limits:
        if limits.headlight_themes:
            themes = tuple(limits.headlight_themes.split(","))
            settings.append(
                Setting(
                    "headlight_theme",
                    (_variant(4722, 11, 10, available=0),),
                    options=themes,
                    maximum=len(themes) - 1,
                )
            )
        if limits.vision_obstacle_profile:
            settings.append(
                Setting(
                    "vision_obstacle_profile",
                    (_variant(7338, 6, 5, available=2, codec=U32),),
                    options=("standard", "people_and_wildlife"),
                )
            )
        enable = limits.geofence_enable
        if enable == 15 or (enable == 3 and has_pin):
            settings.append(
                Setting(
                    "geofence_enabled",
                    (_variant(4734, 2, enable, available=0, requires_pin=enable == 3),),
                )
            )
        if limits.geofence_radius_max:
            settings.append(
                Setting(
                    "geofence_radius",
                    (_variant(4734, 10, 11, available=0, codec=U16),),
                    minimum=limits.geofence_radius_min,
                    maximum=limits.geofence_radius_max,
                    unit="m",
                )
            )
        if limits.geofence_sensitivity and has_pin:
            settings.append(
                Setting(
                    "geofence_sensitivity",
                    (_variant(4734, 7, 8, available=0, requires_pin=True),),
                    options=("low", "medium", "high"),
                    maximum=6,
                    option_values=(2, 3, 4),
                )
            )
    if limits and limits.reverse_max:
        settings.append(
            Setting(
                "reversing_distance",
                (_variant(4716, 0, 1, codec=U16),),
                minimum=limits.reverse_min,
                maximum=limits.reverse_max,
                unit="mm",
                step=10,
            )
        )
    if limits and limits.angle_max:
        for key, opcode in (
            ("sector_1_start_angle", 2),
            ("sector_1_end_angle", 4),
            ("sector_2_start_angle", 6),
            ("sector_2_end_angle", 8),
        ):
            settings.append(
                Setting(
                    key,
                    (_variant(4716, opcode, opcode + 1, codec=U16),),
                    minimum=limits.angle_min,
                    maximum=limits.angle_max,
                    unit="°",
                )
            )
        settings.append(
            Setting(
                "sector_1_proportion", (_variant(4716, 10, 11),), maximum=100, unit="%"
            )
        )
    if bounds is not None:
        settings.append(
            Setting(
                "drive_past_wire",
                (_variant(4712, 0, 1, codec=U16),),
                minimum=bounds[0],
                maximum=bounds[1],
                unit="mm",
                step=10,
            )
        )
    if limits is not None and limits.boundary_corridor_read >= 0:
        opcode = limits.boundary_corridor_read
        codec = (
            Struct("<xB") if opcode == GET_LEGACY_BOUNDARY_CORRIDOR.operation else U8
        )
        settings.append(
            Setting(
                "boundary_corridor",
                (_variant(4706, opcode, opcode + 1, codec=codec),),
                minimum=1,
                maximum=9,
            )
        )
    for index in range(1, (guide_count or 0) + 1):
        settings.append(
            Setting(
                f"guide_{index}_corridor",
                (_variant(4706, 2, 3, arguments=U8.pack(index + 1)),),
                maximum=9,
                translation_key="guide_corridor",
                index=index,
            )
        )
    for index in range(1, STARTING_POINT_COUNTS.get(model, 0) + 1):
        point_settings = [
            ("enabled", 6, 7, U8, 1, None),
            ("distance", 10, 11, U16, 500, "m"),
            ("proportion", 12, 13, U8, 100, "%"),
        ]
        if limits is not None and limits.passage_cutting_read:
            opcode = limits.passage_cutting_read
            point_settings.append(("passage_cutting", opcode, opcode + 1, U8, 1, None))
        for name, get, set_, codec, maximum, unit in point_settings:
            settings.append(
                Setting(
                    f"starting_point_{index}_{name}",
                    (_variant(4706, get, set_, codec=codec, arguments=U8.pack(index)),),
                    maximum=maximum,
                    unit=unit,
                    translation_key=f"starting_point_{name}",
                    index=index,
                )
            )
        if guide_count is not None:
            wire_options = (
                "boundary_right",
                "boundary_left",
                "guide_1",
                "guide_2",
                "guide_3",
            )[: 2 + guide_count]
            settings.append(
                Setting(
                    f"starting_point_{index}_wire",
                    (_variant(4706, 8, 9, arguments=U8.pack(index)),),
                    options=wire_options,
                    maximum=len(wire_options) - 1,
                    translation_key="starting_point_wire",
                    index=index,
                )
            )
    for search_type, key in enumerate(
        ("boundary", "guide_1", "guide_2", "guide_3", "direct")
    ):
        settings.append(
            Setting(
                f"station_search_{key}",
                (_variant(4708, 1, 2, available=0, arguments=U8.pack(search_type)),),
            )
        )
        if key != "direct":
            settings.append(
                Setting(
                    f"station_search_{key}_delay",
                    (
                        _variant(
                            4708,
                            3,
                            4,
                            available=0,
                            codec=U32,
                            arguments=U8.pack(search_type),
                        ),
                    ),
                    maximum=1800,
                    unit="s",
                    step=60,
                )
            )
    return tuple(settings)


THEFT_STATUS_READINGS = {
    "theft_motion_alarm": Reading(Command(4736, 24), U8),
    "theft_geofence_alarm": Reading(Command(4736, 25), U8),
    "theft_stop_alarm": Reading(Command(4736, 26), U8),
}

READINGS = {
    "charging": Reading(Command(4106, 21), U8),
    "remaining_charging_time": Reading(Command(4106, 22), U32),
    "battery_voltage": Reading(Command(4106, 1), U16),
    "battery_current": Reading(Command(4106, 8), I16),
    "battery_temperature": Reading(Command(4106, 9), I16),
    "operation_mode": Reading(Command(4586, 1), U8),
    "restriction_reason": Reading(Command(4658, 0), U8),
    "next_start": Reading(Command(4658, 1), U32),
    "drive_past_wire": Reading(Command(4712, 0), U16),
    "reversing_distance": Reading(Command(4716, 0), U16),
    "spot_cut_available": Reading(Command(4710, 0), U8),
    "is_daylight": Reading(IS_DAYLIGHT, U8),
    "in_charging_station": Reading(Command(4692, 27), U8),
    **THEFT_STATUS_READINGS,
}

STATISTIC_KEYS = (
    "total_running_time",
    "total_cutting_time",
    "total_charging_time",
    "total_searching_time",
    "collisions",
    "charging_cycles",
    "blade_usage_time",
)
