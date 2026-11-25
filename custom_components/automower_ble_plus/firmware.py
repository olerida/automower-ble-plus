"""Resolve firmware-specific BLE capabilities using the mower's main node version."""

import json
import re
from dataclasses import dataclass, replace
from functools import cache
from pathlib import Path

_LEGACY = re.compile(r"App-G3_devicetype-([0-9]+)\.([0-9]+)(?![0-9])")
_FAMILY = re.compile(r"^([0-9]+\.[0-9]+)_Main-App-")


@dataclass(frozen=True, slots=True)
class FirmwareProfile:
    """The settings applicable to a single firmware profile."""

    reverse_min: int
    reverse_max: int
    angle_min: int
    angle_max: int
    blade_usage_read: int
    blade_usage_reset: int
    geofence_enable: int = 0
    geofence_radius_min: int = 0
    geofence_radius_max: int = 0
    geofence_sensitivity: int = 0
    work_area_height_max: int = 0
    spot_cut_trigger: int = 0
    loop_sensor_positions: str = ""
    station_pairing_subscription: int = 0
    installation_create_empty: int = 0
    message_events: int = 0
    vision_obstacle_profile: int = 0
    boundary_corridor_read: int = -1
    passage_cutting_read: int = 0


@cache
def _catalog() -> dict:
    return json.loads(Path(__file__).with_name("firmware_profiles.json").read_text())


def get_message_key(code: int) -> str | None:
    """Return an identifier when the app's TIF files agree on its meaning."""
    return _catalog()["message_keys"].get(str(code))


def get_firmware_profile(
    model_id: tuple[int, int], node_version: str | None
) -> FirmwareProfile | None:
    """Look up the exact build, the app's family, or a matching G3 model."""
    catalog = _catalog()
    if not node_version:
        return None
    exact = True
    if node_version in catalog["release"]:
        profile = catalog["release"][node_version]
    elif match := _LEGACY.search(node_version):
        device, variant = map(int, match.groups())
        if model_id != (device, variant):
            return None
        profile = catalog["legacy"].get(f"{device}.{variant}")
    elif match := _FAMILY.match(node_version):
        profile = catalog["family"].get(match[1])
        exact = False
    else:
        return None
    if profile is None:
        return None
    result = FirmwareProfile(*catalog["profiles"][profile])
    # These operations depend on the exact firmware profile.
    return (
        result
        if exact
        else replace(
            result,
            work_area_height_max=0,
            spot_cut_trigger=0,
            loop_sensor_positions="",
            station_pairing_subscription=0,
            installation_create_empty=0,
            vision_obstacle_profile=0,
        )
    )
