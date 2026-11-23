"""Test the app's firmware matching and legacy model identity checks."""

from custom_components.automower_ble_plus.installation import (
    InstallationLimits,
    get_installation_limits,
)


def test_legacy_profile_requires_the_mower_to_report_the_same_model():
    assert get_installation_limits(
        (5, 0), "App-G3_devicetype-5.0"
    ) == InstallationLimits(150, 3000, 45, 315)
    assert get_installation_limits((23, 1), "App-G3_devicetype-5.0") is None


def test_exact_release_precedes_family_and_unknown_versions_stay_unknown():
    assert get_installation_limits(
        (26, 1), "40.1_Main-App-P25_26.19"
    ) == InstallationLimits(150, 3000, 0, 0)
    assert get_installation_limits(
        (26, 1), "40.1_Main-App-P25_other"
    ) == InstallationLimits(600, 5000, 0, 0)
    assert get_installation_limits((5, 0), "999.1_Main-App-Unrecognized") is None
