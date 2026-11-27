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
    vision_operations = {}
    follow_wire_operations = {}
    mower_operations = {}
    connect_enabled_no_pin = 0
    reset_operations = set()
    message_events = 0
    aim_map_regeneration = 0
    epos_service_operations = {}
    epos_operations = {}
    mission_operations = {}
    mission_schedule = 0
    target_height_status = 0
    map_operations = {}
    raw_operations = {}
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
        if method["family"] == "SiteMap" and protocol.get("msgType") == "5578":
            map_operations[method["command"]] = protocol.get("subCmd")
        if method["family"] == "NativeAttributes" and protocol.get("msgType") == "5786":
            raw_operations[method["command"]] = protocol.get("subCmd"), method
        if method["family"] == "FollowWire" and protocol.get("msgType") == "4706":
            follow_wire_operations[method["command"]] = protocol.get("subCmd")
        if method["family"] == "MowerApp" and protocol.get("msgType") == "4586":
            mower_operations[method["command"]] = protocol.get("subCmd")
        if method["family"] == "EposService" and protocol.get("msgType") == "6778":
            epos_service_operations[method["command"]] = protocol.get("subCmd")
        if method["family"] == "Epos" and protocol.get("msgType") == "6170":
            epos_operations[method["command"]] = protocol.get("subCmd")
        if method["family"] == "Missions" and protocol.get("msgType") == "5604":
            mission_operations[method["command"]] = protocol.get("subCmd")
        if (
            method["family"] == "Calendar"
            and method["command"] == "AddTaskWithMission"
            and protocol.get("msgType") == "4690"
            and protocol.get("subCmd") == "16"
        ):
            mission_schedule = 1
        if (
            method["family"] == "CuttingHeight"
            and method["command"] == "GetDownCuttingStatus"
            and protocol.get("msgType") == "4422"
            and protocol.get("subCmd") == "12"
            and not method["inParams"]
            and method["outParams"]
            == [
                {"name": "ongoing", "type": "bool"},
                {"name": "startTime", "type": "tUnixTime"},
                {"name": "duration", "type": "tDuration"},
            ]
        ):
            target_height_status = 1
        if (
            method["family"] == "AutomowerConnect"
            and method["command"] == "SetEnabledNoPin"
            and protocol.get("msgType") == "4738"
            and protocol.get("subCmd") == "17"
            and method["inParams"] == [{"name": "enabled", "type": "bool"}]
        ):
            connect_enabled_no_pin = 1
        if (
            method["family"] == "System"
            and protocol.get("msgType") == "4698"
            and not method.get("inParams")
            and not method.get("outParams")
        ):
            reset_operations.add((method["command"], protocol.get("subCmd")))
        if (
            method["family"] == "ObstacleAvoidanceVision"
            and protocol.get("msgType") == "7338"
        ):
            vision_operations[method["command"]] = protocol.get("subCmd")
        if (
            method["family"] == "Messages"
            and method["command"] == "SubscribeEvents"
            and protocol.get("msgType") == "4730"
            and protocol.get("subCmd") == "4"
        ):
            message_events = 1
        if (
            method["family"] == "Lona"
            and method["command"] == "StartNewDataCollection"
            and protocol.get("msgType") == "5758"
            and protocol.get("subCmd") == "6"
            and not method["inParams"]
            and not method["outParams"]
        ):
            aim_map_regeneration = 1
        if (
            method["family"] == "Missions"
            and protocol.get("msgType") == "5604"
            and method["command"] in ("GetCuttingHeight", "SetCuttingHeight")
        ):
            area_height_methods[method["command"]] = (protocol.get("subCmd"), method)
    types = {item["name"]: item for item in profile.get("types", ())}
    planner_reason_9 = {
        "IPLANNER_RESTRICTION_REASON_MISSION_ABANDONDED": 1,
        "IPLANNER_RESTRICTION_REASON_NO_DAYLIGHT": 2,
    }.get(
        next(
            (
                item["key"]
                for item in types.get("tIPlanner_RestrictionReason", {}).get(
                    "enums", ()
                )
                if item["value"] == "9"
            ),
            "",
        ),
        0,
    )

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
    docking_pattern = all(
        station_operations.get(name) == operation
        for name, operation in (
            ("RunInstallationPattern", "21"),
            ("GetInstallationState", "23"),
            ("GetInstallationResult", "24"),
        )
    )
    docking_pattern_stop = (
        docking_pattern and station_operations.get("StopInstallationPattern") == "35"
    )
    reset_user_settings = 0
    if ("ResetToUserDefault", "8") in reset_operations:
        reset_user_settings = (
            42 if ("ResetToUserDefaultAndPowerCycle", "42") in reset_operations else 8
        )
    create_empty = int(
        site_operations.get("GetAvailable") == "17"
        and site_operations.get("CreateNewSite") == "24"
    )
    vision_profile = int(
        vision_operations.get("GetProfile") == "6"
        and vision_operations.get("SetProfile") == "5"
        and {
            enum["key"]: enum["value"]
            for enum in types.get("tIObstacleAvoidanceVision_SupportedProfile", {}).get(
                "enums", ()
            )
        }
        == {
            "IOBSTACLEAVOIDANCEVISION_CFG_PROFILE_STANDARD": "0",
            "IOBSTACLEAVOIDANCEVISION_CFG_PROFILE_HUMAN_AND_WILDLIFE": "1",
        }
    )
    boundary_corridor = (
        follow_wire_operations.get("GetBoundaryCorridor"),
        follow_wire_operations.get("SetBoundaryCorridor"),
    )
    boundary_corridor_read = (
        int(boundary_corridor[0])
        if boundary_corridor in (("0", "1"), ("24", "25"))
        else -1
    )
    passage_cutting = (
        follow_wire_operations.get("GetPassageCuttingEnabled"),
        follow_wire_operations.get("SetPassageCuttingEnabled"),
    )
    passage_cutting_read = (
        int(passage_cutting[0])
        if passage_cutting in (("24", "25"), ("26", "27"))
        else 0
    )
    wire_test_states = ""
    if all(
        follow_wire_operations.get(name) == operation
        for name, operation in (
            ("TestStartingPoint", "15"),
            ("GetCurrentDistance", "16"),
            ("TestFollowIn", "17"),
        )
    ):
        if (
            follow_wire_operations.get("TestAbort") == "18"
            and follow_wire_operations.get("GetTestState") == "23"
        ):
            states = types.get("tFollowWireTif_TestState", {}).get("enums", ())
            ordered = sorted(states, key=lambda state: int(state["value"]))
            if [int(state["value"]) for state in ordered] == list(range(len(ordered))):
                wire_test_states = " ".join(
                    state["key"].removeprefix("FOLLOWWIRE_TIF_TEST_STATE_").lower()
                    for state in ordered
                )
        elif "TestAbort" not in follow_wire_operations:
            wire_test_states = "legacy"
    poi_mode = types.get("tIMowerApp_MowerMode", {})
    park_at_poi = int(
        poi_mode.get("type") == "uint8"
        and any(
            enum["key"] == "IMOWERAPP_MODE_POI" and enum["value"] == "4"
            for enum in poi_mode.get("enums", ())
        )
        and all(
            mower_operations.get(name) == operation
            for name, operation in (
                ("SetMode", "0"),
                ("GetMode", "1"),
                ("StartTrigger", "4"),
            )
        )
    )
    epos_cloud_origin_setup = int(
        all(
            epos_service_operations.get(name) == operation
            for name, operation in (
                ("GetEnabled", "4"),
                ("SetEnabled", "5"),
                ("GetHasOrigin", "15"),
                ("GetIsOriginValid", "16"),
                ("GetIsOriginSettable", "17"),
                ("SetOriginToCurrentPosition", "18"),
            )
        )
        and (
            epos_service_operations.get("GetAvailable2") == "23"
            or epos_service_operations.get("GetAvailable") == "3"
        )
    )
    epos_boundary_switch = int(
        all(
            epos_operations.get(name) == operation
            for name, operation in (
                ("GetAvailable", "1"),
                ("GetEnabled", "2"),
                ("SetEnabled", "3"),
            )
        )
    )
    zone_map_writable = int(
        any(
            attribute["family"] == "SiteMap"
            and attribute["name"] == "ZoneMap"
            and {"read", "write"}.issubset(attribute.get("operations", ()))
            and attribute.get("params")
            == [{"name": "map", "type": "tIRoboticTypes_Svg"}]
            and attribute["protocol"].get("familyId") == "5578"
            and attribute["protocol"].get("attributeId") == "103"
            and attribute["protocol"].get("rawOnly") is True
            for attribute in profile.get("attributes-v2", ())
        )
    )
    aim_work_areas = int(
        zone_map_writable
        and all(
            mission_operations.get(name) == operation
            for name, operation in (
                ("CreateMission", "1"),
                ("GetNumberOfMissions", "2"),
                ("GetIdListEntry", "3"),
                ("DeleteMission", "5"),
                ("GetName", "7"),
                ("GetMowingAreaId", "11"),
                ("SetMowingAreaId", "12"),
                ("GetSchedulable", "18"),
                ("SetSchedulable", "19"),
                ("GetAvailable", "20"),
            )
        )
    )
    checksum_opcode, checksum_method = raw_operations.get("GetRawChecksum", (None, {}))
    raw_maps = {
        (attribute["name"], protocol.get("attributeId"))
        for attribute in profile.get("attributes", ())
        if attribute["family"] == "SiteMap"
        and (protocol := attribute.get("protocol", {})).get("familyId") == "5578"
        and protocol.get("rawOnly") is True
    }
    legacy_aim_maps = int(
        map_operations.get("GetAvailable") == "0"
        and {"GetGeneratedMapAvailable", "GetZoneMapAvailable"}.isdisjoint(
            map_operations
        )
        and {("GeneratedMap", "101"), ("ZoneMap", "103")} <= raw_maps
        and raw_operations.get("GetRaw", (None,))[0] == "7"
        and raw_operations.get("GetNext", (None,))[0] == "8"
        and checksum_opcode == "15"
        and checksum_method.get("inParams")
        == [
            {"name": "familyId", "type": "tIAttribute_FamilyId"},
            {"name": "attributeId", "type": "tIAttribute_AttributeId"},
            {"name": "transactionId", "type": "tIAttribute_TransactionId"},
        ]
        and checksum_method.get("outParams") == [{"name": "crc32", "type": "uint32"}]
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
        message_events,
        vision_profile,
        boundary_corridor_read,
        passage_cutting_read,
        wire_test_states,
        int(docking_pattern),
        int(docking_pattern_stop),
        reset_user_settings,
        park_at_poi,
        connect_enabled_no_pin,
        aim_map_regeneration,
        epos_cloud_origin_setup,
        epos_boundary_switch,
        zone_map_writable,
        aim_work_areas,
        legacy_aim_maps,
        mission_schedule,
        target_height_status,
        planner_reason_9,
    )
    return (
        result
        if any(result[:-17])
        or boundary_corridor_read >= 0
        or passage_cutting_read
        or wire_test_states
        or docking_pattern
        or reset_user_settings
        or park_at_poi
        or connect_enabled_no_pin
        or aim_map_regeneration
        or epos_cloud_origin_setup
        or epos_boundary_switch
        or zone_map_writable
        or legacy_aim_maps
        or mission_schedule
        or target_height_status
        or planner_reason_9
        else None
    )


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
