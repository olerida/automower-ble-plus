"""Read model-supported mowing areas from the Missions BLE family."""

from math import isclose
from struct import error as StructError

from .features import (
    BOUNDARY_EDGE_DETECTION_AVAILABLE,
    I16,
    IS_DAYLIGHT,
    U8,
    U32,
    VISION_OBSTACLE_AVOIDANCE_AVAILABLE,
    VISION_POSITIONING_AVAILABLE,
    Reading,
)
from .indexed import async_indexed_page, decode_name, encode_name
from .mapfiles import async_verify_mowing_area
from .schedule import async_get_schedule
from .transport import AutomowerClient, CommandRejected, MowerConnectionError
from .wire import Command

_AVAILABLE = Command(5604, 20)
_COUNT = Command(5604, 2)
_MAX_COUNT = Command(5604, 38)
_ENTRY = Command(5604, 3)
_CREATE = Command(5604, 1)
_DELETE = Command(5604, 5)
_TYPE = Command(5604, 13)
_ENABLED = Command(5604, 31)
_SCHEDULABLE = Command(5604, 18)
_SET_SCHEDULABLE = Command(5604, 19)
_GET_NAME = Command(5604, 7)
_SET_NAME = Command(5604, 8)
_SET_ENABLED = Command(5604, 32)
_GET_MOWING_AREA = Command(5604, 11)
_SET_MOWING_AREA = Command(5604, 12)
_CURRENT_MISSION = Command(5604, 16)
_PROGRESS = Command(5604, 15)
_CLEAR_PROGRESS = Command(5604, 28)
_WORK_AREA_STATUS = {
    "progress_percent": Reading(_PROGRESS, U8),
    "last_completed_epoch_seconds": Reading(Command(5604, 14), U32),
    "last_abandoned_epoch_seconds": Reading(Command(5604, 36), U32),
    "last_scheduled_start_epoch_seconds": Reading(Command(5604, 44), U32),
}
_PRIORITY_KEY = "priority"
_PRIORITY = (Reading(Command(5604, 71), U8), Command(5604, 72))
AREA_PRIORITIES = {0: "lowest", 1: "low", 2: "medium", 3: "high", 4: "highest"}
_SYSTEMATIC_AVAILABLE = Reading(Command(5582, 0), U8)
_MOWING_PATTERNS = {1: "random", 2: "systematic"}
_SYSTEMATIC_SETTINGS = {
    "orientation": (Reading(Command(5582, 3), I16), Command(5582, 4)),
    "orientation_shift": (Reading(Command(5582, 5), I16), Command(5582, 6)),
    "boundary_mowing": (Reading(Command(5582, 10), U8), Command(5582, 9)),
}
_HEIGHT_SETTINGS = {
    "cutting_height": (Reading(Command(5604, 9), U8), Command(5604, 10)),
    "use_global_cutting_height": (Reading(Command(5604, 23), U8), Command(5604, 24)),
}
_AREA_ASSISTANCE_FLAGS = {
    VISION_POSITIONING_AVAILABLE: {
        "vision_positioning": (48, 47),
        "use_global_vision_positioning": (60, 59),
    },
    IS_DAYLIGHT: {
        "daylight_only": (50, 49),
        "use_global_daylight_only": (62, 61),
    },
    BOUNDARY_EDGE_DETECTION_AVAILABLE: {
        "boundary_edge_detection": (52, 51),
        "use_global_boundary_edge_detection": (64, 63),
    },
    Command(5356, 2): {
        "obstacle_avoidance": (54, 53),
        "use_global_obstacle_avoidance": (66, 65),
        "obstacle_avoidance_at_boundary": (56, 55),
        "use_global_obstacle_avoidance_at_boundary": (68, 67),
    },
    VISION_OBSTACLE_AVOIDANCE_AVAILABLE: {
        "use_global_vision_obstacle_profile": (70, 69),
    },
}
AREA_ASSISTANCE_BOOLEAN_SETTINGS = frozenset(
    key for group in _AREA_ASSISTANCE_FLAGS.values() for key in group
)
AREA_VISION_PROFILES = {
    0: "default_pro",
    1: "risk_objects_only",
    2: "residential_garden",
}
_VISION_PROFILE_KEY = "vision_obstacle_profile"
_OPTIONS = {
    "mowing_pattern": _MOWING_PATTERNS,
    _VISION_PROFILE_KEY: AREA_VISION_PROFILES,
    _PRIORITY_KEY: AREA_PRIORITIES,
}
_ASSISTANCE_KEYS = {*AREA_ASSISTANCE_BOOLEAN_SETTINGS, _VISION_PROFILE_KEY}
_BOOLEAN_SETTINGS = {
    "boundary_mowing",
    "use_global_cutting_height",
    *AREA_ASSISTANCE_BOOLEAN_SETTINGS,
}
_ANGLE_SETTINGS = {"orientation", "orientation_shift", "current_orientation"}


async def _async_available(client: AutomowerClient) -> bool:
    try:
        return U8.unpack(await client.command(_AVAILABLE))[0] == 1
    except CommandRejected as err:
        if not err.unsupported:
            raise
        return True  # Older profiles expose the list without an availability command.


async def async_mission_ids(client: AutomowerClient) -> set[int]:
    """Read the IDs of the missions in the active installation."""
    count = U32.unpack(await client.command(_COUNT))[0]
    return {
        U32.unpack_from(raw)[0]
        async for _, raw in async_indexed_page(client, _ENTRY, count, 0, count)
    }


async def async_require_work_area_capacity(
    client: AutomowerClient, name: str
) -> set[int]:
    """Check that a mapped work area can have a mission before uploading its polygon."""
    encode_name(name)
    if not await _async_available(client):
        raise ValueError("This mower reports that work areas are unavailable")
    mission_ids = await async_mission_ids(client)
    try:
        maximum = U32.unpack(await client.command(_MAX_COUNT))[0]
    except CommandRejected as err:
        if not err.unsupported:
            raise
    else:
        if len(mission_ids) >= maximum:
            raise ValueError("The mower reports that its work area list is full")
    return mission_ids


async def async_get_linked_work_areas(
    client: AutomowerClient, area_id: str
) -> list[tuple[int, str]]:
    """Find every mission that references the selected polygon."""
    if not await _async_available(client):
        return []
    try:
        mission_ids = await async_mission_ids(client)
    except CommandRejected as err:
        if err.command == _COUNT and err.unsupported:
            return []
        raise
    linked = []
    for mission_id in sorted(mission_ids):
        identity = U32.pack(mission_id)
        raw = await client.command(_GET_MOWING_AREA, identity)
        if raw.partition(b"\0")[0].decode("ascii") == area_id:
            linked.append(
                (mission_id, decode_name(await client.command(_GET_NAME, identity)))
            )
    return linked


async def async_get_work_areas(
    client: AutomowerClient, offset: int, limit: int
) -> dict:
    """Return the names and identifiers needed for area-specific schedules."""
    entries: list[dict] = []
    result = {"total": 0, "offset": offset, "work_areas": entries}
    if not await _async_available(client):
        return result
    try:
        count = U32.unpack(await client.command(_COUNT))[0]
    except CommandRejected as err:
        if err.unsupported:
            return result
        raise
    result["total"] = count
    async for _, raw in async_indexed_page(client, _ENTRY, count, offset, limit):
        mission_id = U32.unpack_from(raw)[0]
        name = decode_name(raw[U32.size :])
        area = {"id": mission_id, "name": name}
        for key, command in (
            ("type", _TYPE),
            ("enabled", _ENABLED),
            ("schedulable", _SCHEDULABLE),
        ):
            try:
                value = U8.unpack(await client.command(command, U32.pack(mission_id)))[
                    0
                ]
            except CommandRejected as err:
                if err.unsupported:
                    continue
                raise
            area[key] = value if key == "type" else value == 1
        entries.append(area)
    return result


async def async_update_work_area(
    client: AutomowerClient, mission_id: int, *, name: str | None, enabled: bool | None
) -> None:
    """Update one setting for a stored area, then read the value back."""
    if (name is None) == (enabled is None):
        raise ValueError("Provide either a name or an enabled state")
    if not await _async_available(client):
        raise ValueError("This mower reports that work areas are unavailable")
    identity = U32.pack(mission_id)
    if name is not None:
        getter, setter = _GET_NAME, _SET_NAME
        payload = encode_name(name)
        expected = name
        decode = decode_name
    else:
        getter, setter = _ENABLED, _SET_ENABLED
        payload = U8.pack(enabled)
        expected = U8.unpack(payload)
        decode = U8.unpack
    if decode(await client.command(getter, identity)) == expected:
        return
    await client.command(setter, identity + payload)
    if decode(await client.command(getter, identity)) != expected:
        raise ValueError("The mower returned a different work area value after writing")


async def async_create_work_area(
    client: AutomowerClient, name: str, area_id: str, expected_checksum: str
) -> dict:
    """Create a named work area linked to an existing mowing polygon."""
    encoded_name = encode_name(name)
    if (
        not area_id
        or not area_id.isascii()
        or not area_id.isprintable()
        or len(area_id) > 65514
    ):
        raise ValueError("Enter a valid mowing polygon ID")
    if not await _async_available(client):
        raise ValueError("This mower reports that work areas are unavailable")
    existing = await async_mission_ids(client)
    await async_verify_mowing_area(client, area_id, expected_checksum)
    mission_id = U32.unpack(await client.command(_CREATE, encoded_name))[0]
    if mission_id in existing:
        raise ValueError("The mower returned an existing work area ID")
    identity = U32.pack(mission_id)
    try:
        await client.command(
            _SET_MOWING_AREA, identity + area_id.encode("ascii") + b"\0"
        )
        await client.command(_SET_SCHEDULABLE, identity + b"\x01")
        actual_name = decode_name(await client.command(_GET_NAME, identity))
        actual_area = (
            (await client.command(_GET_MOWING_AREA, identity))
            .partition(b"\0")[0]
            .decode("ascii")
        )
        schedulable = U8.unpack(await client.command(_SCHEDULABLE, identity))[0]
        if (actual_name, actual_area, schedulable) != (name, area_id, 1):
            raise ValueError("The mower did not confirm the new work area")
    except (CommandRejected, MowerConnectionError, ValueError, StructError) as err:
        if client.is_connected():
            try:
                await client.command(_DELETE, identity)
                reverted = mission_id not in await async_mission_ids(client)
            except CommandRejected, MowerConnectionError, StructError:
                reverted = False
            if reverted:
                raise ValueError(f"Work area creation was reverted: {err}") from err
        raise ValueError(
            f"Check work area {mission_id} after the interrupted creation: {err}"
        ) from err
    return {
        "mission_id": mission_id,
        "name": actual_name,
        "mowing_area_id": actual_area,
    }


async def async_delete_work_area(
    client: AutomowerClient, mission_id: int, expected_name: str
) -> None:
    """Delete the named mission and check that the mower removed its ID."""
    if not await _async_available(client):
        raise ValueError("This mower reports that work areas are unavailable")
    identity = U32.pack(mission_id)
    if decode_name(await client.command(_GET_NAME, identity)) != expected_name:
        raise ValueError("The work area name has changed")
    if any(task.mission_id == mission_id for task in await async_get_schedule(client)):
        raise ValueError(
            "Remove the schedule intervals assigned to this work area before deleting it"
        )
    await client.command(_DELETE, identity)
    if mission_id in await async_mission_ids(client):
        raise ValueError("The mower still reports this work area after deletion")


async def async_get_work_area_status(
    client: AutomowerClient, mission_id: int | None
) -> dict:
    """Return the active area and the status of the selected mowing task."""
    if not await _async_available(client):
        return {"available": False}
    result: dict = {"available": True}
    try:
        current = U32.unpack(await client.command(_CURRENT_MISSION))[0]
    except CommandRejected as err:
        if not err.unsupported:
            raise
        current = None
    if current is not None:
        result["current_id"] = current
    area = current if mission_id is None else mission_id
    if area is None:
        return result
    identity = U32.pack(area)
    try:
        name = decode_name(await client.command(_GET_NAME, identity))
    except CommandRejected as err:
        if mission_id is None and err.unsupported:
            return result
        raise
    result.update(mission_id=area, name=name)
    for key, reading in _WORK_AREA_STATUS.items():
        try:
            value = reading.decode(await client.command(reading.command, identity))
        except CommandRejected as err:
            if err.unsupported:
                continue
            raise
        if (key == "progress_percent" and value <= 100) or (
            key != "progress_percent" and value not in (0, 0xFFFFFFFF)
        ):
            result[key] = value
    return result


async def async_reset_work_area_progress(
    client: AutomowerClient, mission_id: int
) -> None:
    """Clear one work area's saved progress and read its counter back."""
    if not await _async_available(client):
        raise ValueError("This mower reports that work areas are unavailable")
    identity = U32.pack(mission_id)
    progress = U8.unpack(await client.command(_PROGRESS, identity))[0]
    if progress > 100:
        raise ValueError("The mower did not report a valid work area progress")
    if progress:
        await client.command(_CLEAR_PROGRESS, identity)
        if U8.unpack(await client.command(_PROGRESS, identity))[0] != 0:
            raise ValueError(
                "The mower did not confirm clearing the work area progress"
            )


async def _async_area_mowing_fields(
    client: AutomowerClient,
    identity: bytes,
    height_max: int,
    requested: str | None = None,
) -> dict[str, tuple[Reading, Command | None]]:
    if not await _async_available(client):
        raise ValueError("This mower reports that work areas are unavailable")
    mission_type = Reading(_TYPE, U8).decode(await client.command(_TYPE, identity))
    fields: dict[str, tuple[Reading, Command | None]] = {}
    if requested is None or requested == _PRIORITY_KEY:
        fields[_PRIORITY_KEY] = _PRIORITY
        if requested is not None:
            return fields
    if requested is None or requested in _ASSISTANCE_KEYS:
        fields.update(await _async_area_assistance_fields(client, requested))
        if requested is not None:
            return fields
    if height_max:
        fields.update(_HEIGHT_SETTINGS)
    if mission_type not in _MOWING_PATTERNS:
        return fields
    try:
        available = _SYSTEMATIC_AVAILABLE.decode(
            await client.command(_SYSTEMATIC_AVAILABLE.command)
        )
    except CommandRejected as err:
        if not err.unsupported:
            raise
        return fields
    if available == 1:
        fields["mowing_pattern"] = (Reading(_TYPE, U8), Command(5604, 17))
        if mission_type == 2:
            fields.update(_SYSTEMATIC_SETTINGS)
            fields["current_orientation"] = (Reading(Command(5582, 7), I16), None)
    return fields


async def _async_area_assistance_fields(
    client: AutomowerClient, requested: str | None
) -> dict[str, tuple[Reading, Command]]:
    """Probe the hardware behind per-area assistance settings."""
    fields = {}
    for availability, setting_ids in _AREA_ASSISTANCE_FLAGS.items():
        vision_profile = availability == VISION_OBSTACLE_AVOIDANCE_AVAILABLE
        if (
            requested is not None
            and requested not in setting_ids
            and not (vision_profile and requested == _VISION_PROFILE_KEY)
        ):
            continue
        try:
            supported = Reading(availability, U8).decode(
                await client.command(availability)
            )
        except CommandRejected as err:
            if err.unsupported:
                continue
            raise
        if supported != 1 and not (availability == IS_DAYLIGHT and supported == 0):
            continue
        fields.update(
            {
                key: (Reading(Command(5604, read), U8), Command(5604, write))
                for key, (read, write) in setting_ids.items()
            }
        )
        if vision_profile:
            fields[_VISION_PROFILE_KEY] = (
                Reading(Command(5604, 58), U32),
                Command(5604, 57),
            )
    return fields


async def async_get_area_mowing_settings(
    client: AutomowerClient, mission_id: int, height_max: int
) -> dict:
    """Read the mowing settings supported in a single work area."""
    identity = U32.pack(mission_id)
    fields = await _async_area_mowing_fields(client, identity, height_max)
    settings = {}
    editable = []
    for key, (reader, setter) in fields.items():
        try:
            value = reader.decode(await client.command(reader.command, identity))
        except CommandRejected as err:
            if err.unsupported:
                continue
            raise
        if key in _ANGLE_SETTINGS:
            settings[key] = value / 10
        elif key in _BOOLEAN_SETTINGS:
            if value not in (0, 1):
                continue
            settings[key] = value == 1
        elif key in _OPTIONS:
            options = _OPTIONS[key]
            if value not in options:
                continue
            settings[key] = options[value]
        else:
            settings[key] = value
        if setter is not None:
            editable.append(key)
    if "cutting_height" in settings:
        settings["cutting_height_unit"] = "%" if height_max == 100 else "level"
    return {"mission_id": mission_id, "settings": settings, "editable": editable}


async def async_set_area_mowing_setting(
    client: AutomowerClient, mission_id: int, height_max: int, changes: dict
) -> None:
    """Write one supported setting and verify the value stored by the mower."""
    if len(changes) != 1:
        raise ValueError("Provide exactly one work area setting")
    key, value = next(iter(changes.items()))
    identity = U32.pack(mission_id)
    fields = await _async_area_mowing_fields(client, identity, height_max, key)
    if key not in fields or (setter := fields[key][1]) is None:
        raise ValueError(f"This work area does not support changing {key}")
    reader, _ = fields[key]
    if key in _ANGLE_SETTINGS:
        encoded = round(value * 10)
        if not -3600 <= encoded <= 3600 or not isclose(encoded, value * 10):
            raise ValueError(
                "Provide an angle between −360 and 360 in tenths of a degree"
            )
    elif key in _OPTIONS:
        encoded = next(
            (number for number, label in _OPTIONS[key].items() if label == value),
            None,
        )
        if encoded is None:
            raise ValueError(f"Select a supported option for {key}")
    else:
        encoded = value
        if key == "cutting_height":
            minimum = 0 if height_max == 100 else 1
            if not minimum <= value <= height_max:
                raise ValueError("The cutting height is outside the firmware range")
    current = reader.decode(await client.command(reader.command, identity))
    if (key in _BOOLEAN_SETTINGS and current not in (0, 1)) or (
        key in _OPTIONS and current not in _OPTIONS[key]
    ):
        raise ValueError(f"The mower did not report a supported value for {key}")
    if current != encoded:
        await client.command(setter, identity + reader.codec.pack(encoded))
        if reader.decode(await client.command(reader.command, identity)) != encoded:
            raise ValueError(
                "The mower returned a different work area value after writing"
            )
