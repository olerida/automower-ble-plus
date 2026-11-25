"""Interpret active mower faults and their identifiers."""

from automower_ble.error_codes import ErrorCodes

from .features import U8, U32, Reading
from .firmware import get_message_key
from .wire import Command

FAULT_READINGS = {
    "error_code": Reading(Command(4586, 6), U32),
    "error_confirmable": Reading(Command(4586, 8), U8),
}
CONFIRM_ERROR = Command(4586, 9)

FAULT_STATES = {
    0: "none",
    1: "outside_working_area",
    2: "no_loop_signal",
    3: "wrong_loop_signal",
    4: "front_loop_sensor_failure",
    5: "rear_loop_sensor_failure",
    6: "left_loop_sensor_failure",
    7: "right_loop_sensor_failure",
    8: "wrong_pin",
    9: "trapped",
    10: "overturned",
    11: "low_battery",
    12: "empty_battery",
    13: "drive_failure",
    14: "lifted",
    15: "lifted",
    16: "stuck_docked",
    17: "station_blocked",
    18: "rear_collision_sensor_failure",
    19: "front_collision_sensor_failure",
    20: "right_wheel_blocked",
    21: "left_wheel_blocked",
    22: "right_wheel_drive_failure",
    23: "left_wheel_drive_failure",
    24: "cutting_drive_failure",
    25: "cutting_system_blocked",
    29: "steep_slope",
    30: "charging_system_failure",
    31: "stop_button_failure",
    32: "tilt_sensor_failure",
    33: "tilted",
    38: "electronic_problem",
}

SEVERITY_NAMES = ("unknown", "fatal", "error", "warning", "info", "debug", "software")


def message_description(code: int) -> str | None:
    """Retain familiar messages and describe newer unambiguous codes."""
    if code == 0:
        return "No message"
    try:
        return ErrorCodes(code).name.replace("_", " ").title()
    except ValueError:
        key = get_message_key(code)
        return key.replace("_", " ").capitalize() if key else None
