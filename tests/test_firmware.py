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
        target_height_status=1,
        statistics_subscription=10,
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
        mission_schedule=1,
        statistics_subscription=10,
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
        mission_schedule=1,
        statistics_subscription=10,
    )
    assert get_firmware_profile((5, 0), "999.1_Main-App-Unrecognized") is None


def test_manual_spot_cut_requires_a_profile_that_declares_the_trigger():
    assert get_firmware_profile((5, 0), "App-G3_devicetype-5.0").spot_cut_trigger == 0
    assert (
        get_firmware_profile((26, 1), "40.3_Main-App-P16_29.47").spot_cut_trigger == 1
    )


def test_parking_at_poi_requires_the_operation_mode_in_the_firmware():
    assert not get_firmware_profile((5, 0), "App-G3_devicetype-5.0").park_at_poi
    assert not get_firmware_profile((26, 1), "40.1_Main-App-P25_26.19").park_at_poi
    assert get_firmware_profile((26, 1), "40.1_Main-App-P25_32.22").park_at_poi
    assert not get_firmware_profile((26, 1), "40.1_Main-App-P25_other").park_at_poi


def test_aim_map_regeneration_requires_a_firmware_with_the_start_command():
    assert get_firmware_profile((28, 2), "40.3_Main-App-P16_34.18").aim_map_regeneration
    assert not get_firmware_profile(
        (28, 2), "40.3_Main-App-P16_29.47"
    ).aim_map_regeneration
    assert not get_firmware_profile(
        (28, 2), "40.3_Main-App-P16_unknown"
    ).aim_map_regeneration


def test_epos_boundary_switch_obeys_the_app_model_and_firmware_rules():
    version = "40.16_Main-App-P21NA_79.3"
    assert get_firmware_profile((31, 1), version).epos_boundary_switch
    assert not get_firmware_profile(
        (5, 0), "App-G3_devicetype-5.0"
    ).epos_boundary_switch
    assert not get_firmware_profile((26, 1), version).epos_boundary_switch
    assert not get_firmware_profile(
        (31, 4), "40.26_Main-App-P21V_95.1"
    ).epos_boundary_switch
    assert not get_firmware_profile(
        (39, 7), "40.27_Main-App-P17V_unknown"
    ).epos_boundary_switch


def test_aim_work_area_capability_requires_zone_mission_support():
    assert get_firmware_profile((28, 2), "40.3_Main-App-P16_81.4").aim_work_areas
    assert not get_firmware_profile((28, 2), "40.3_Main-App-P16_34.18").aim_work_areas
    assert not get_firmware_profile((5, 0), "App-G3_devicetype-5.0").aim_work_areas


def test_legacy_aim_map_detection_requires_a_matching_firmware_build():
    early = get_firmware_profile((28, 2), "40.3_Main-App-P16_34.18")
    assert early.legacy_aim_maps
    assert not early.zone_map_writable
    assert get_firmware_profile((26, 1), "40.1_Main-App-P25_34.24").legacy_aim_maps
    assert not get_firmware_profile((28, 2), "40.3_Main-App-P16_81.4").legacy_aim_maps
    assert not get_firmware_profile(
        (28, 2), "40.3_Main-App-P16_unknown"
    ).legacy_aim_maps
    assert not get_firmware_profile((5, 0), "App-G3_devicetype-5.0").legacy_aim_maps


def test_target_height_status_distinguishes_ongoing_from_enabled():
    assert get_firmware_profile((5, 0), "App-G3_devicetype-5.0").target_height_status
    assert not get_firmware_profile(
        (29, 1), "40.4_Main-App-P2Z_28.8"
    ).target_height_status


def test_pattern_presets_require_firmware_with_systematic_direction_control():
    assert get_firmware_profile((31, 1), "40.16_Main-App-P21NA_112.3").pattern_presets
    assert not get_firmware_profile((5, 0), "App-G3_devicetype-5.0").pattern_presets
