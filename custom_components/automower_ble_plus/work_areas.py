"""Read model-supported mowing areas from the Missions BLE family."""

from math import isclose

from .features import I16, U8, U32, Reading
from .indexed import async_indexed_page, decode_name, encode_name
from .transport import AutomowerClient, CommandRejected
from .wire import Command

_AVAILABLE = Command(5604, 20)
_COUNT = Command(5604, 2)
_ENTRY = Command(5604, 3)
_TYPE = Command(5604, 13)
_ENABLED = Command(5604, 31)
_SCHEDULABLE = Command(5604, 18)
_GET_NAME = Command(5604, 7)
_SET_NAME = Command(5604, 8)
_SET_ENABLED = Command(5604, 32)
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
_BOOLEAN_SETTINGS = {"boundary_mowing", "use_global_cutting_height"}
_ANGLE_SETTINGS = {"orientation", "orientation_shift", "current_orientation"}


async def _async_available(client: AutomowerClient) -> bool:
    try:
        return U8.unpack(await client.command(_AVAILABLE))[0] == 1
    except CommandRejected as err:
        if not err.unsupported:
            raise
        return True  # Older profiles expose the list without an availability command.


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


async def _async_area_mowing_fields(
    client: AutomowerClient, identity: bytes, height_max: int
) -> dict[str, tuple[Reading, Command | None]]:
    if not await _async_available(client):
        raise ValueError("This mower reports that work areas are unavailable")
    mission_type = Reading(_TYPE, U8).decode(await client.command(_TYPE, identity))
    fields: dict[str, tuple[Reading, Command | None]] = {}
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
            settings[key] = value == 1
        elif key == "mowing_pattern":
            if value not in _MOWING_PATTERNS:
                continue
            settings[key] = _MOWING_PATTERNS[value]
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
    fields = await _async_area_mowing_fields(client, identity, height_max)
    if key not in fields or (setter := fields[key][1]) is None:
        raise ValueError(f"This work area does not support changing {key}")
    reader, _ = fields[key]
    if key in _ANGLE_SETTINGS:
        encoded = round(value * 10)
        if not -3600 <= encoded <= 3600 or not isclose(encoded, value * 10):
            raise ValueError(
                "Provide an angle between −360 and 360 in tenths of a degree"
            )
    elif key == "mowing_pattern":
        encoded = next(
            (
                number
                for number, pattern in _MOWING_PATTERNS.items()
                if pattern == value
            ),
            None,
        )
        if encoded is None:
            raise ValueError("Select a known mowing pattern")
    else:
        encoded = value
        if key == "cutting_height":
            minimum = 0 if height_max == 100 else 1
            if not minimum <= value <= height_max:
                raise ValueError("The cutting height is outside the firmware range")
    current = reader.decode(await client.command(reader.command, identity))
    if current != encoded:
        await client.command(setter, identity + reader.codec.pack(encoded))
        if reader.decode(await client.command(reader.command, identity)) != encoded:
            raise ValueError(
                "The mower returned a different work area value after writing"
            )
