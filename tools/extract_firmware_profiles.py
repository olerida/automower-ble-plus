"""Extract firmware-specific BLE limits and operation identifiers from Automower Connect."""

import argparse
import json
from pathlib import Path
from zipfile import ZipFile

FAMILY = "LeaveChargingStation"
FAMILY_ID = "4716"


def capabilities(profile: dict) -> tuple[int | str, ...] | None:
    methods = {}
    blade_operations = {}
    geofence_operations = {}
    area_height_methods = {}
    spot_cut_operations = {}
    loop_operations = {}
    station_operations = {}
    site_operations = {}
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
        if method["family"] == "LoopSystem" and protocol.get("msgType") == "4462":
            loop_operations[method["command"]] = protocol.get("subCmd")
        if method["family"] == "ChargingStation" and protocol.get("msgType") == "4692":
            station_operations[method["command"]] = protocol.get("subCmd")
        if method["family"] == "SiteManager" and protocol.get("msgType") == "5900":
            site_operations[method["command"]] = protocol.get("subCmd")
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
    positions = ""
    if (
        loop_operations.get("GetInstalledLoops") == "29"
        and loop_operations.get("GetLoopSignals") == "13"
    ):
        sensors = types.get("tILoopSystem_Sensor", {}).get("enums", ())
        ordered = sorted(sensors, key=lambda sensor: int(sensor["value"]))
        if [int(sensor["value"]) for sensor in ordered] == list(range(len(ordered))):
            positions = " ".join(
                sensor["key"].removeprefix("LOOPSYSTEM_SENSOR_").lower()
                for sensor in ordered
            )
    station_subscription = 0
    event_channel = station_operations.get("SubscribeEventChannel")
    station_events = {
        event["name"]: {item["key"]: item["value"] for item in event["protocol"]}.get(
            "eventId"
        )
        for event in profile.get("events", ())
        if event["family"] == "ChargingStation"
    }
    if (
        station_operations.get("InitiateNewPairing") == "2"
        and event_channel in ("1", "12")
        and station_events.get("PairingFailed") == "1"
        and station_events.get("PairingSuccessful") == "2"
    ):
        station_subscription = int(event_channel)
    create_empty = int(
        site_operations.get("GetAvailable") == "17"
        and site_operations.get("CreateNewSite") == "24"
    )
    result = (
        *reverse,
        *angles,
        *blade_ids,
        enable,
        *radius,
        sensitivity,
        area_height_max,
        spot_cut_trigger,
        positions,
        station_subscription,
        create_empty,
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
        codes: dict[int, set[str]] = {}
        for file in sorted(members):
            if not file.startswith("assets/tif-models/") or not file.endswith(".json"):
                continue
            for type_info in json.loads(archive.read(file)).get("types", ()):
                if type_info["name"] == "tIMessages_MessageCode":
                    for item in type_info.get("enums", ()):
                        key = item["key"].removeprefix("MSG_ID_").lower()
                        codes.setdefault(int(item["value"]), set()).add(key)
    messages = {
        code: next(iter(keys)) for code, keys in sorted(codes.items()) if len(keys) == 1
    }
    return {"profiles": profiles, **tables, "message_keys": messages}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apk", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_text(json.dumps(extract(args.apk), indent=2) + "\n")
