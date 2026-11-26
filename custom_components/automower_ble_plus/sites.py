"""Read and manage installation profiles over BLE."""

import asyncio
from dataclasses import dataclass

from .features import U8, U32
from .indexed import async_indexed_page, decode_name, encode_name
from .transport import AutomowerClient, CommandRejected
from .wire import Command

_AVAILABLE = Command(5900, 17)
_COUNT = Command(5900, 1)
_ENTRY = Command(5900, 3)
_CURRENT = Command(5900, 12)
_SITE_NAME = Command(5900, 5)
_ACTIVATE = Command(5900, 14)
_CREATE_EMPTY = Command(5900, 24)
_CREATE_COPY = Command(5900, 4)
_DELETE = Command(5900, 15)
_SUBSCRIBE = Command(5900, 0)
_GET_NAME = Command(5896, 2)
_SET_NAME = Command(5896, 1)
_MAP_FIELDS = (
    ("site_map_available", Command(5578, 40)),
    ("zone_map_available", Command(5578, 38)),
    ("generated_map_available", Command(5578, 43)),
)


@dataclass(frozen=True, slots=True)
class InstallationSelection:
    """The profiles and currently active ID reported by the mower."""

    active_id: int
    profiles: tuple[tuple[int, str], ...]


async def _async_available(client: AutomowerClient) -> bool:
    try:
        return U8.unpack(await client.command(_AVAILABLE))[0] == 1
    except CommandRejected as err:
        if not err.unsupported:
            raise
        return False


async def _async_require_available(client: AutomowerClient) -> None:
    if not await _async_available(client):
        raise ValueError("Installation profiles are unavailable on this mower")


async def async_active_installation_id(client: AutomowerClient) -> int | None:
    """Return the current profile ID on models with installation profiles."""
    if not await _async_available(client):
        return None
    return U32.unpack(await client.command(_CURRENT))[0]


async def async_empty_installation_id(client: AutomowerClient) -> int | None:
    """Identify the active installation when its firmware confirms no maps."""
    active = await async_active_installation_id(client)
    if active is None:
        return None
    for _, operation in _MAP_FIELDS:
        try:
            if U8.unpack(await client.command(operation))[0] != 0:
                return None
        except CommandRejected as err:
            if not err.unsupported:
                raise
            return None
    return active if active == await async_active_installation_id(client) else None


async def async_get_installations(
    client: AutomowerClient, offset: int, limit: int, *, include_maps: bool = True
) -> dict:
    """Return a page of installation profiles and available map indicators."""
    available = await _async_available(client)
    sites: list[dict] = []
    result = {
        "available": available,
        "total": 0,
        "offset": offset,
        "installations": sites,
    }
    if not available:
        return result
    count = U32.unpack(await client.command(_COUNT))[0]
    result["total"] = count
    current = U32.unpack(await client.command(_CURRENT))[0] if count else None
    result["active_id"] = current
    async for _, raw in async_indexed_page(client, _ENTRY, count, offset, limit):
        site_id = U32.unpack_from(raw)[0]
        sites.append(
            {
                "id": site_id,
                "name": decode_name(raw[U32.size :]),
                "active": site_id == current,
            }
        )
    if include_maps:
        for name, operation in _MAP_FIELDS:
            try:
                result[name] = U8.unpack(await client.command(operation))[0] == 1
            except CommandRejected as err:
                if not err.unsupported:
                    raise
    return result


async def async_get_installation_selection(
    client: AutomowerClient,
) -> InstallationSelection | None:
    """Read a selection for Home Assistant when the mower has multiple profiles."""
    result = await async_get_installations(client, 0, 100, include_maps=False)
    if not 2 <= result["total"] <= 100:
        return None
    return InstallationSelection(
        result["active_id"],
        tuple((site["id"], site["name"]) for site in result["installations"]),
    )


async def async_activate_installation(
    client: AutomowerClient, site_id: int, expected_active_id: int | None
) -> None:
    """Activate a saved installation and verify the mower's active ID."""
    await _async_require_available(client)
    current = U32.unpack(await client.command(_CURRENT))[0]
    if current == site_id:
        return
    if expected_active_id is not None and current != expected_active_id:
        raise ValueError(
            "The active installation changed. Check the current installation before retrying"
        )
    identity = U32.pack(site_id)
    decode_name(await client.command(_SITE_NAME, identity))
    await client.command(_ACTIVATE, identity)
    try:
        async with asyncio.timeout(15):
            while U32.unpack(await client.command(_CURRENT))[0] != site_id:
                await asyncio.sleep(0.5)
    except TimeoutError as err:
        raise ValueError("The mower did not confirm the active installation") from err


async def async_rename_installation(
    client: AutomowerClient, site_id: int, name: str
) -> None:
    """Rename the active installation and read it back by its current identity."""
    payload = encode_name(name)
    await _async_require_available(client)
    active = U32.unpack(await client.command(_CURRENT))[0]
    if active != site_id:
        raise ValueError(f"Installation {site_id} is not active")
    if decode_name(await client.command(_GET_NAME)) == name:
        return
    await client.command(_SET_NAME, payload)
    actual = decode_name(await client.command(_GET_NAME))
    active = U32.unpack(await client.command(_CURRENT))[0]
    if active != site_id or actual != name:
        raise ValueError("The mower did not confirm the active installation name")


async def async_create_installation(
    client: AutomowerClient, copy_current: bool
) -> dict:
    """Create a fresh installation or a copy and return the assigned ID."""
    await _async_require_available(client)
    if copy_current and not U32.unpack(await client.command(_COUNT))[0]:
        raise ValueError("There is no current installation to copy")
    operation = _CREATE_COPY if copy_current else _CREATE_EMPTY
    site_id = U32.unpack(await client.command(operation))[0]
    return {"site_id": site_id}


async def async_delete_installation(
    client: AutomowerClient, site_id: int, expected_name: str
) -> None:
    """Delete the confirmed inactive installation and verify its result event."""
    await _async_require_available(client)
    active = U32.unpack(await client.command(_CURRENT))[0]
    if active == site_id:
        raise ValueError("Switch to another installation before deleting this one")
    identity = U32.pack(site_id)
    actual_name = decode_name(await client.command(_SITE_NAME, identity))
    if actual_name != expected_name:
        raise ValueError("The installation name has changed")
    await client.command(_SUBSCRIBE)
    event = await client.command_and_wait_for_event(_DELETE, (1,), identity, timeout=15)
    if event.payload != identity:
        raise ValueError("The mower did not confirm deletion of this installation")
