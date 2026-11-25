"""Test the app's firmware matching and legacy model identity checks."""

from custom_components.automower_ble_plus.firmware import (
    FirmwareProfile,
    get_firmware_profile,
)


def test_legacy_profile_requires_the_mower_to_report_the_same_model():
    assert get_firmware_profile((5, 0), "App-G3_devicetype-5.0") == FirmwareProfile(
        150,
        3000,
        45,
        315,
        7,
        8,
        3,
        0,
        0,
        1,
        station_pairing_subscription=1,
        boundary_corridor_read=0,
        passage_cutting_read=24,
        wire_test_states="legacy",
        reset_user_settings=8,
    )
    assert get_firmware_profile((23, 1), "App-G3_devicetype-5.0") is None


def test_exact_release_precedes_family_and_unknown_versions_stay_unknown():
    assert get_firmware_profile((26, 1), "40.1_Main-App-P25_26.19") == FirmwareProfile(
        150,
        3000,
        0,
        0,
        7,
        8,
        3,
        0,
        0,
        0,
        9,
        loop_sensor_positions="front_right front_center front_left rear_right rear_center rear_left",
        station_pairing_subscription=12,
        message_events=1,
        boundary_corridor_read=24,
        wire_test_states="idle in_operation done error aborted",
        reset_user_settings=8,
    )
    assert get_firmware_profile((26, 1), "40.1_Main-App-P25_other") == FirmwareProfile(
        600,
        5000,
        0,
        0,
        7,
        8,
        15,
        100,
        5000,
        0,
        message_events=1,
        boundary_corridor_read=24,
        passage_cutting_read=26,
        reset_user_settings=8,
    )
    assert get_firmware_profile((5, 0), "999.1_Main-App-Unrecognized") is None


def test_manual_spot_cut_requires_a_profile_that_declares_the_trigger():
    assert get_firmware_profile((5, 0), "App-G3_devicetype-5.0").spot_cut_trigger == 0
    assert (
        get_firmware_profile((26, 1), "40.3_Main-App-P16_29.47").spot_cut_trigger == 1
    )
