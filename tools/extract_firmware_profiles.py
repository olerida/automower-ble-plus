"""Extract firmware-specific BLE limits and operation identifiers from Automower Connect."""

import argparse
import json
from pathlib import Path
from zipfile import ZipFile

FAMILY = "LeaveChargingStation"
FAMILY_ID = "4716"


def capabilities(profile: dict) -> tuple[int, ...] | None:
    methods = {}
    blade_operations = {}
    geofence_operations = {}
    area_height_methods = {}
    spot_cut_operations = {}
    for method in profile.get("methods", ()):
        protocol = {item["key"]: item["value"] for item in method["protocol"]}
        if method["family"] == FAMILY and protocol.get("msgType") == FAMILY_ID:
            methods[method["command"]] = protocol.get("subCmd")
        if method["family"] == "Statistics" and protocol.get("msgType") == "4726":
            blade_operations[method["command"]] = protocol.get("subCmd")
        if method["family"] == "GeoFence" and protocol.get("msgType") == "4734":
            geofence_operations[method["command"]] = protocol.get("subCmd")
        if method["family"] == "SpotCutting" and protocol.get("msgType") == "4710":
            spot_cut_operations[method["command"]] = protocol.get("subCmd")
        if (
            method["family"] == "Missions"
            and protocol.get("msgType") == "5604"
            and method["command"] in ("GetCuttingHeight", "SetCuttingHeight")
        ):
            area_height_methods[method["command"]] = (protocol.get("subCmd"), method)
    types = {item["name"]: item for item in profile.get("types", ())}

    def value_range(name: str) -> tuple[int, int]:
        bounds = types.get(name, {}).get("range", {})
        low, high = bounds.get("start"), bounds.get("stop")
        if isinstance(low, int) and isinstance(high, int) and 0 <= low < high <= 65535:
            return low, high
        return 0, 0

    reverse = (0, 0)
    angles = (0, 0)
    if (
        methods.get("GetReversingDistance") == "0"
        and methods.get("SetReversingDistance") == "1"
    ):
        reverse = value_range(f"tI{FAMILY}_ReversingDistance")
    angle_methods = {
        "GetMinAngleSector1": "2",
        "SetMinAngleSector1": "3",
        "GetMaxAngleSector1": "4",
        "SetMaxAngleSector1": "5",
        "GetMinAngleSector2": "6",
        "SetMinAngleSector2": "7",
        "GetMaxAngleSector2": "8",
        "SetMaxAngleSector2": "9",
        "GetSector1Proportion": "10",
        "SetSector1Proportion": "11",
    }
    if all(methods.get(name) == opcode for name, opcode in angle_methods.items()):
        low, high = value_range(f"tI{FAMILY}_Angle")
        if max(45, low) < min(315, high):
            angles = max(45, low), min(315, high)
    blade = (
        blade_operations.get("GetCuttingBladeUsageTime"),
        blade_operations.get("ResetCuttingBladeUsageTime"),
    )
    blade_ids = (
        tuple(map(int, blade)) if blade in (("7", "8"), ("13", "14")) else (0, 0)
    )
    enable = 0
    if geofence_operations.get("GetEnabled") == "2":
        if geofence_operations.get("SetEnabledNoPin") == "15":
            enable = 15
        elif geofence_operations.get("SetEnabled") == "3":
            enable = 3
    radius = (0, 0)
    if (
        geofence_operations.get("GetRadius") == "10"
        and geofence_operations.get("SetRadius") == "11"
    ):
        radius = value_range("tGeoFenceTif_GeoFenceRadius")
    sensitivity = int(
        geofence_operations.get("GetSensitivity") == "7"
        and geofence_operations.get("SetSensitivity") == "8"
    )
    area_height_max = 0
    get_id, get_height = area_height_methods.get("GetCuttingHeight", (None, None))
    set_id, set_height = area_height_methods.get("SetCuttingHeight", (None, None))
    if get_id == "9" and set_id == "10":
        get_type = get_height["outParams"][0]["type"]
        set_type = set_height["inParams"][1]["type"]
        if get_type == set_type == "tPercentage":
            area_height_max = 100
        elif get_type == set_type == "tICuttingHeight_HeightPrescaled":
            area_height_max = 9
    spot_cut_trigger = int(spot_cut_operations.get("StartTrigger") == "7")
    result = (
        *reverse,
        *angles,
        *blade_ids,
        enable,
        *radius,
        sensitivity,
        area_height_max,
        spot_cut_trigger,
    )
    return result if any(result) else None


def extract(apk: Path) -> dict:
    with ZipFile(apk) as archive:
        index = json.loads(archive.read("assets/hash_list.json"))
        members = set(archive.namelist())
        by_file = {}
        tables = {"release": {}, "family": {}, "legacy": {}}
        profiles = []
        for table, name in (("release", "files"), ("family", "latest")):
            for version, key in sorted(index[name].items()):
                raw = f"assets/tif-models/{key}.json"
                named = f"assets/tif-models/{version}.json"
                file = raw if raw in members else named
                if file not in members:
                    continue
                if file not in by_file:
                    by_file[file] = capabilities(json.loads(archive.read(file)))
                if (profile := by_file[file]) is not None:
                    if profile not in profiles:
                        profiles.append(profile)
                    tables[table][version] = profiles.index(profile)
                else:
                    tables[table][version] = None
        for device, variants in sorted(index["fallback"].items()):
            for variant, family in sorted(variants.items()):
                if family in tables["family"]:
                    tables["legacy"][f"{device}.{variant}"] = tables["family"][family]
    return {"profiles": profiles, **tables}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apk", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_text(json.dumps(extract(args.apk), indent=2) + "\n")
