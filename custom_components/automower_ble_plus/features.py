"""Model-dependent BLE capabilities exposed as Home Assistant entities."""

from dataclasses import dataclass
from struct import Struct
from struct import error as StructError

from .firmware import FirmwareProfile
from .models import BOUNDARY_RANGES, STARTING_POINT_COUNTS
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


@dataclass(frozen=True, slots=True)
class SettingVariant:
    """One firmware variant of a readable and writable setting."""

    read: Reading
    write: Command
    availability: Command | None = None


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

    def encode(self, variant: SettingVariant, value: int) -> bytes:
        """Check the app's value range before sending a new setting."""
        if not self.minimum <= value <= self.maximum:
            raise ValueError(
                f"{self.key} must be between {self.minimum} and {self.maximum}"
            )
        if (value - self.minimum) % self.step:
            raise ValueError(f"{self.key} must be set in steps of {self.step}")
        return variant.read.arguments + variant.read.codec.pack(value)


def _variant(
    family: int,
    get: int,
    set_: int,
    *,
    available: int | None = None,
    codec: Struct = U8,
    arguments: bytes = b"",
) -> SettingVariant:
    return SettingVariant(
        Reading(Command(family, get), codec, arguments),
        Command(family, set_),
        Command(family, available) if available is not None else None,
    )


_SETTINGS = (
    Setting("eco_mode", (_variant(4692, 6, 5),)),
    Setting("mower_house", (_variant(4692, 4, 3),)),
    Setting("weather_timer", (_variant(4460, 4, 5, available=2),)),
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
    Setting("boundary_corridor", (_variant(4706, 24, 25),), maximum=9),
    Setting("automatic_passage_handling", (_variant(4706, 4, 5),)),
    Setting(
        "station_search_range",
        (_variant(4708, 5, 6),),
        options=("minimum", "medium", "maximum"),
        maximum=2,
    ),
)


def settings_for_model(
    model: tuple[int, int],
    guide_count: int | None,
    limits: FirmwareProfile | None = None,
) -> tuple[Setting, ...]:
    """Select settings with verified value ranges for this mower model."""
    bounds = BOUNDARY_RANGES.get(model)
    settings = list(_SETTINGS)
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
    for index in range(1, STARTING_POINT_COUNTS.get(model, 0) + 1):
        for name, get, set_, codec, maximum, unit in (
            ("enabled", 6, 7, U8, 1, None),
            ("distance", 10, 11, U16, 500, "m"),
            ("proportion", 12, 13, U8, 100, "%"),
            ("passage_cutting", 26, 27, U8, 1, None),
        ):
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


READINGS = {
    "charging": Reading(Command(4106, 21), U8),
    "remaining_charging_time": Reading(Command(4106, 22), U32),
    "battery_voltage": Reading(Command(4106, 1), U16),
    "battery_current": Reading(Command(4106, 8), I16),
    "battery_temperature": Reading(Command(4106, 9), I16),
    "operation_mode": Reading(Command(4586, 1), U8),
    "error_code": Reading(Command(4586, 6), U32),
    "restriction_reason": Reading(Command(4658, 0), U8),
    "next_start": Reading(Command(4658, 1), U32),
    "drive_past_wire": Reading(Command(4712, 0), U16),
    "reversing_distance": Reading(Command(4716, 0), U16),
    "spot_cut_available": Reading(Command(4710, 0), U8),
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
