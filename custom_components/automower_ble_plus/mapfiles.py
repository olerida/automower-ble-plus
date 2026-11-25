"""Read and write the mower's SVG maps over the HCP Native Attributes family."""

import asyncio
import gzip
import re
from hashlib import md5
from io import BytesIO
from secrets import randbelow
from struct import Struct
from xml.etree import ElementTree
from zlib import crc32
from zlib import error as ZlibError

from .features import U8, U32
from .transport import AutomowerClient, CommandRejected
from .wire import Command

_MAP_FAMILY = 5578
_SITE_MAP = 100
_GENERATED_MAP = 101
_MAPS_AVAILABLE = Command(_MAP_FAMILY, 0)
_SITE_MAP_AVAILABLE = Command(_MAP_FAMILY, 40)
_GENERATED_AVAILABLE = Command(_MAP_FAMILY, 43)
_GET_CHECKSUM = Command(_MAP_FAMILY, 23)
_SET_CHECKSUM = Command(_MAP_FAMILY, 24)
_GET_RAW = Command(5786, 7)
_GET_NEXT = Command(5786, 8)
_SET_RAW = Command(5786, 11)
_SET_NEXT = Command(5786, 12)
_MAX_PAYLOAD = Command(5786, 13)
_ATTRIBUTE = Struct("<HHI")
_FIRST_CHUNK = Struct("<II")
_CHUNK = Struct("<BI")
_MAX_MAP_SIZE = 16 * 1024 * 1024
_MAX_FRAME_CHUNK = 0xFF00
_SVG_NAMESPACE = "{http://www.w3.org/2000/svg}"
_POINT = re.compile(r"-?[0-9]{1,19},-?[0-9]{1,19}\Z")
_SHAPE_ATTRIBUTES = {
    "id",
    "title",
    "stroke",
    "fill",
    "points",
    "stroke-width",
    "display",
    "rewildingzone",
}


def _chunk(raw: bytes, position: int = 0) -> tuple[bool, bytes]:
    end, size = _CHUNK.unpack_from(raw, position)
    data = raw[position + _CHUNK.size :]
    if end not in (0, 1) or size != len(data) or (not end and size == 0):
        raise ValueError("Invalid map chunk length or end marker")
    return bool(end), data


def _decode_svg(data: bytes) -> str:
    if data.startswith(b"\x1f\x8b"):
        try:
            with gzip.GzipFile(fileobj=BytesIO(data)) as stream:
                data = stream.read(_MAX_MAP_SIZE + 1)
        except (OSError, EOFError, ZlibError) as err:
            raise ValueError("The mower returned an invalid compressed map") from err
    if len(data) > _MAX_MAP_SIZE:
        raise ValueError("The map exceeds the supported SVG size")
    return data.decode("utf-8")


def _parse_svg(svg: str) -> tuple[bytes, ElementTree.Element]:
    """Validate the polygons and polylines produced by Automower Connect."""
    raw = svg.encode("utf-8")
    if len(raw) > _MAX_MAP_SIZE or b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        raise ValueError("The map must be a self-contained Automower SVG")
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as err:
        raise ValueError("The map SVG is invalid") from err
    if root.tag.removeprefix(_SVG_NAMESPACE) != "svg" or not len(root):
        raise ValueError("The map must contain SVG polygons or polylines")
    if root.attrib.keys() - {"version", "viewBox", "width", "height"}:
        raise ValueError("The map has unsupported SVG root attributes")
    ids = set()
    for shape in root:
        kind = shape.tag.removeprefix(_SVG_NAMESPACE)
        identity = shape.get("id")
        if (
            kind not in ("polygon", "polyline")
            or len(shape)
            or shape.attrib.keys() - _SHAPE_ATTRIBUTES
        ):
            raise ValueError("The map has unsupported SVG elements or attributes")
        if not identity or identity in ids:
            raise ValueError("Every map feature must have a unique ID")
        ids.add(identity)
        points = shape.get("points", "").split()
        if len(points) < (3 if kind == "polygon" else 2) or any(
            not _POINT.fullmatch(point) for point in points
        ):
            raise ValueError(f"Invalid coordinates for map feature {identity}")
        if any(
            not -(1 << 63) <= int(value) < (1 << 63)
            for point in points
            for value in point.split(",")
        ):
            raise ValueError(
                f"Coordinates outside the mower's range for map feature {identity}"
            )
        if any("url(" in shape.get(key, "").lower() for key in ("fill", "stroke")):
            raise ValueError("Map features cannot reference external resources")
    return raw, root


def _prepare_map(svg: str) -> tuple[bytes, bytes]:
    raw, _ = _parse_svg(svg)
    return gzip.compress(raw, mtime=0), md5(raw, usedforsecurity=False).digest()


async def _async_read_raw_map(client: AutomowerClient, attribute: int) -> bytes:
    transaction = randbelow(0xFFFFFFFF) + 1
    packet = await client.command(
        _GET_RAW, _ATTRIBUTE.pack(_MAP_FAMILY, attribute, transaction)
    )
    expected_crc, total = _FIRST_CHUNK.unpack_from(packet)
    if total > _MAX_MAP_SIZE:
        raise ValueError("The mower reports an oversized map")
    end, data = _chunk(packet, _FIRST_CHUNK.size)
    buffer = bytearray(data)
    while not end and len(buffer) < total:
        end, data = _chunk(await client.command(_GET_NEXT, U32.pack(transaction)))
        buffer.extend(data)
    if not end or len(buffer) != total or crc32(buffer) != expected_crc:
        raise ValueError("The downloaded map failed its length or CRC check")
    return bytes(buffer)


async def _async_checksum(client: AutomowerClient) -> bytes:
    checksum = await client.command(_GET_CHECKSUM)
    if len(checksum) != 16:
        raise ValueError("The mower returned an invalid map checksum")
    return checksum


async def _async_available(client: AutomowerClient, command: Command) -> bool:
    try:
        return U8.unpack(await client.command(command))[0] == 1
    except CommandRejected as err:
        if not err.unsupported:
            raise
        return False


async def async_get_map(client: AutomowerClient, generated: bool) -> dict:
    """Read the site's SVG or the generated map when the mower advertises it."""
    if not await _async_available(client, _MAPS_AVAILABLE):
        return {"available": False}
    availability = _GENERATED_AVAILABLE if generated else _SITE_MAP_AVAILABLE
    attribute = _GENERATED_MAP if generated else _SITE_MAP
    available = await _async_available(client, availability)
    result = {"available": available, "generated": generated}
    if not generated:
        result["checksum"] = (await _async_checksum(client)).hex()
    if available:
        raw = await _async_read_raw_map(client, attribute)
        result["svg"] = await asyncio.to_thread(_decode_svg, raw)
    return result


async def async_verify_mowing_area(
    client: AutomowerClient, area_id: str, expected_checksum: str
) -> None:
    """Verify that the current map revision contains the requested mowing polygon."""
    if not re.fullmatch(r"[0-9a-fA-F]{32}", expected_checksum):
        raise ValueError("Enter the checksum returned for the active map")
    current = await async_get_map(client, generated=False)
    if not current["available"]:
        raise ValueError("The mower does not report an active site map")
    if current["checksum"] != expected_checksum.lower():
        raise ValueError(
            "The active map changed. Read it again before creating an area"
        )
    _, root = await asyncio.to_thread(_parse_svg, current["svg"])
    if not any(
        shape.tag.removeprefix(_SVG_NAMESPACE) == "polygon"
        and shape.get("id") == area_id
        and shape.get("fill", "").lower() == "green"
        and shape.get("rewildingzone", "").lower() != "true"
        for shape in root
    ):
        raise ValueError("The active map does not contain the selected mowing polygon")
    if (await _async_checksum(client)).hex() != expected_checksum.lower():
        raise ValueError("The active map changed while reading it")


async def async_replace_map(
    client: AutomowerClient, svg: str, expected_checksum: str
) -> dict:
    """Upload a confirmed revision and publish its checksum after the last chunk."""
    if not re.fullmatch(r"[0-9a-fA-F]{32}", expected_checksum):
        raise ValueError("Enter the checksum returned for the active map")
    compressed, digest = await asyncio.to_thread(_prepare_map, svg)
    if not await _async_available(client, _MAPS_AVAILABLE):
        raise ValueError("The mower does not advertise map support")
    current = await _async_checksum(client)
    if current == digest:
        return {"checksum": digest.hex()}
    if current != bytes.fromhex(expected_checksum):
        raise ValueError("The active map changed. Read it again before updating")
    maximum = U32.unpack(await client.command(_MAX_PAYLOAD))[0]
    chunk_size = min(maximum, _MAX_FRAME_CHUNK)
    if chunk_size < 1 or len(compressed) > _MAX_MAP_SIZE:
        raise ValueError("The map exceeds the supported upload size")
    transaction = randbelow(0xFFFFFFFF) + 1
    first_prefix = _ATTRIBUTE.pack(_MAP_FAMILY, _SITE_MAP, transaction)
    first_prefix += _FIRST_CHUNK.pack(crc32(compressed), len(compressed))
    try:
        for offset in range(0, len(compressed), chunk_size):
            data = compressed[offset : offset + chunk_size]
            last = offset + len(data) == len(compressed)
            operation = _SET_NEXT if offset else _SET_RAW
            prefix = U32.pack(transaction) if offset else first_prefix
            await client.command(
                operation, prefix + _CHUNK.pack(last, len(data)) + data
            )
        await client.command(_SET_CHECKSUM, digest)
    except BaseException:
        await client.disconnect()
        raise
    if await _async_checksum(client) != digest:
        raise ValueError("The mower did not confirm the uploaded map checksum")
    return {"checksum": digest.hex()}
