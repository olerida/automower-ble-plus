"""Read installation profiles and update the current name over BLE."""

from .features import U8, U32
from .indexed import async_indexed_page, decode_name, encode_name
from .transport import AutomowerClient, CommandRejected
from .wire import Command

_AVAILABLE = Command(5900, 17)
_COUNT = Command(5900, 1)
_ENTRY = Command(5900, 3)
_CURRENT = Command(5900, 12)
_GET_NAME = Command(5896, 2)
_SET_NAME = Command(5896, 1)
_MAP_FIELDS = (
    ("site_map_available", Command(5578, 40)),
    ("zone_map_available", Command(5578, 38)),
    ("generated_map_available", Command(5578, 43)),
)


async def _async_available(client: AutomowerClient) -> bool:
    try:
        return U8.unpack(await client.command(_AVAILABLE))[0] == 1
    except CommandRejected as err:
        if not err.unsupported:
            raise
        return False


async def async_get_installations(
    client: AutomowerClient, offset: int, limit: int
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
    for name, operation in _MAP_FIELDS:
        try:
            result[name] = U8.unpack(await client.command(operation))[0] == 1
        except CommandRejected as err:
            if not err.unsupported:
                raise
    return result


async def async_rename_installation(
    client: AutomowerClient, site_id: int, name: str
) -> None:
    """Rename the active installation and read it back by its current identity."""
    payload = encode_name(name)
    if not await _async_available(client):
        raise ValueError("Installation profiles are unavailable on this mower")
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
