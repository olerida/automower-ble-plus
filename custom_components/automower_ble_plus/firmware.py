"""Resolve firmware-specific BLE capabilities using the mower's main node version."""

import json
import re
from dataclasses import dataclass, replace
from functools import cache
from pathlib import Path

_LEGACY = re.compile(r"App-G3_devicetype-([0-9]+)\.([0-9]+)(?![0-9])")
_FAMILY = re.compile(r"^([0-9]+\.[0-9]+)_Main-App-")
_EPOS_FIXED_DEVICE_TYPES = frozenset({24, 26, 27, 40})
_EPOS_FIXED_VISION_MODELS = frozenset({(31, 4), (31, 5), (39, 7), (39, 8)})


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
    wire_test_states: str = ""
    docking_pattern: int = 0
    docking_pattern_stop: int = 0
    reset_user_settings: int = 0
    park_at_poi: int = 0
    connect_enabled_no_pin: int = 0
    aim_map_regeneration: int = 0
    epos_cloud_origin_setup: int = 0
    epos_boundary_switch: int = 0
    zone_map_writable: int = 0
    aim_work_areas: int = 0
    legacy_aim_maps: int = 0
    mission_schedule: int = 0


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
    if result.epos_boundary_switch and (
        model_id[0] in _EPOS_FIXED_DEVICE_TYPES or model_id in _EPOS_FIXED_VISION_MODELS
    ):
        result = replace(result, epos_boundary_switch=0)
    if exact:
        return result
    # Infer protocol support by family only when every known release agrees.
    family = match[1]
    releases = tuple(
        FirmwareProfile(*catalog["profiles"][index]) if index is not None else None
        for version, index in catalog["release"].items()
        if version.startswith(f"{family}_Main-App-")
    )
    return replace(
        result,
        work_area_height_max=0,
        spot_cut_trigger=0,
        loop_sensor_positions="",
        station_pairing_subscription=0,
        installation_create_empty=0,
        vision_obstacle_profile=0,
        wire_test_states="",
        docking_pattern_stop=0,
        reset_user_settings=8 if result.reset_user_settings else 0,
        park_at_poi=int(
            result.park_at_poi
            and all(profile and profile.park_at_poi for profile in releases)
        ),
        connect_enabled_no_pin=int(
            result.connect_enabled_no_pin
            and all(profile and profile.connect_enabled_no_pin for profile in releases)
        ),
        aim_map_regeneration=int(
            result.aim_map_regeneration
            and all(profile and profile.aim_map_regeneration for profile in releases)
        ),
        epos_cloud_origin_setup=int(
            result.epos_cloud_origin_setup
            and all(profile and profile.epos_cloud_origin_setup for profile in releases)
        ),
        epos_boundary_switch=int(
            result.epos_boundary_switch
            and all(profile and profile.epos_boundary_switch for profile in releases)
        ),
        zone_map_writable=int(
            result.zone_map_writable
            and all(profile and profile.zone_map_writable for profile in releases)
        ),
        aim_work_areas=int(
            result.aim_work_areas
            and all(profile and profile.aim_work_areas for profile in releases)
        ),
        legacy_aim_maps=int(
            result.legacy_aim_maps
            and all(profile and profile.legacy_aim_maps for profile in releases)
        ),
        mission_schedule=int(
            result.mission_schedule
            and all(profile and profile.mission_schedule for profile in releases)
        ),
    )
