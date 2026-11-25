"""Validate Automower map fragment layouts and commit behavior."""

import gzip
from hashlib import md5
from io import BytesIO
from struct import pack, unpack_from
from unittest.mock import AsyncMock
from zlib import crc32

import pytest
from PIL import Image

from custom_components.automower_ble_plus.map_preview import render_map_png
from custom_components.automower_ble_plus.mapfiles import (
    MapKind,
    async_get_map,
    async_read_map_kind,
    async_replace_map,
    async_verify_mowing_area,
)
from custom_components.automower_ble_plus.transport import CommandRejected
from custom_components.automower_ble_plus.wire import Command

SVG = '<svg><polygon id="lawn" stroke="green" fill="green" points="0,0 1500,0 0,1200"/></svg>'


def test_preview_renders_polygons_paths_and_disabled_areas_as_png():
    svg = """<svg xmlns="http://www.w3.org/2000/svg">
      <polygon fill="green" points="0,0 4000,0 4000,4000 0,4000"/>
      <polygon fill="red" points="1250,1250 2750,1250 2750,2750 1250,2750"/>
      <polygon fill="red" display="none" points="2900,100 3800,100 3800,1000 2900,1000"/>
      <polyline stroke="blue" stroke-width="200" points="0,0 2000,800"/>
      <polyline stroke="yellow" points="2000,800 2000,1000"/>
    </svg>"""
    with Image.open(BytesIO(render_map_png(svg))) as image:
        assert image.format == "PNG"
        assert image.size == (800, 580)
        assert image.getpixel((400, 290)) == (234, 198, 187)
        assert image.getpixel((300, 130)) == (169, 201, 167)
        assert image.getpixel((580, 470)) == (224, 225, 220)
        assert image.getpixel((400, 444)) == (173, 114, 27)

    with pytest.raises(ValueError, match="unsupported SVG elements"):
        render_map_png('<svg><image href="https://example.com/map.png"/></svg>')


@pytest.mark.asyncio
async def test_zone_map_reads_attribute_103_and_checks_firmware_availability():
    client = AsyncMock()
    raw = gzip.compress(SVG.encode())
    client.command.side_effect = [
        b"\x01",
        b"\x01",
        pack("<IIBI", crc32(raw), len(raw), 1, len(raw)) + raw,
    ]
    assert await async_read_map_kind(client, MapKind.ZONES) == {
        "available": True,
        "map_type": "zones",
        "svg": SVG,
    }
    calls = client.command.await_args_list
    assert calls[1].args == (Command(5578, 38),)
    assert unpack_from("<HH", calls[2].args[1]) == (5578, 103)


@pytest.mark.asyncio
async def test_read_map_reassembles_gzip_chunks_and_checks_crc():
    client = AsyncMock()
    svg_bytes = SVG.encode()
    blob = gzip.compress(svg_bytes)
    digest = md5(svg_bytes, usedforsecurity=False).digest()
    for corrupt in (False, True):
        client.command.reset_mock()
        client.command.side_effect = [
            b"\x01",
            b"\x01",
            digest,
            pack("<IIBI", crc32(blob) ^ int(corrupt), len(blob), 0, 20) + blob[:20],
            pack("<BI", 1, len(blob) - 20) + blob[20:],
        ]
        if corrupt:
            with pytest.raises(ValueError, match="length or CRC"):
                await async_get_map(client, False)
        else:
            assert await async_get_map(client, False) == {
                "available": True,
                "generated": False,
                "checksum": digest.hex(),
                "svg": SVG,
            }
        calls = client.command.await_args_list
        family, attr, transaction = unpack_from("<HHI", calls[3].args[1])
        assert (family, attr) == (5578, 100)
        assert calls[4].args == (Command(5786, 8), pack("<I", transaction))


@pytest.mark.asyncio
@pytest.mark.parametrize("reject_next", [False, True])
async def test_upload_publishes_svg_digest_only_after_last_chunk(reject_next):
    client = AsyncMock()
    old = bytes(16)
    blob = bytearray()
    header = None
    published = None
    commands = []

    async def send(command, payload=b""):
        nonlocal header, published
        commands.append(command)
        match (command.family, command.operation):
            case (5578, 0):
                return b"\x01"
            case (5578, 23):
                return published or old
            case (5786, 13):
                return pack("<I", 17)
            case (5786, 11):
                header = unpack_from("<HHIIIBI", payload)
                blob.extend(payload[21:])
                assert (header[0], header[1], header[5], header[6]) == (
                    5578,
                    100,
                    0,
                    17,
                )
            case (5786, 12):
                if reject_next:
                    raise CommandRejected(command, 5)
                transaction, last, size = unpack_from("<IBI", payload)
                assert header[2] == transaction
                blob.extend(payload[9:])
                assert size == len(payload[9:])
                assert bool(last) == (len(blob) == header[4])
            case (5578, 24):
                assert (header[3], header[4]) == (crc32(blob), len(blob))
                assert gzip.decompress(blob) == SVG.encode()
                published = payload
            case _:
                pytest.fail(f"Unexpected command {command}")
        return b""

    client.command.side_effect = send
    if reject_next:
        with pytest.raises(CommandRejected):
            await async_replace_map(client, SVG, old.hex())
        assert Command(5578, 24) not in commands
        client.disconnect.assert_awaited_once()
    else:
        digest = md5(SVG.encode(), usedforsecurity=False).digest()
        assert await async_replace_map(client, SVG, old.hex()) == {
            "checksum": digest.hex()
        }
        assert published == digest


@pytest.mark.asyncio
async def test_upload_checks_geometry_and_previous_checksum_before_writing():
    client = AsyncMock()
    with pytest.raises(ValueError, match="unsupported SVG elements"):
        await async_replace_map(
            client, '<svg><script src="https://example.com/a"/></svg>', bytes(16).hex()
        )
    client.command.assert_not_awaited()
    client.command.side_effect = [b"\x01", bytes([1] * 16)]
    with pytest.raises(ValueError, match="active map changed"):
        await async_replace_map(client, SVG, bytes(16).hex())
    assert [call.args[0] for call in client.command.await_args_list] == [
        Command(5578, 0),
        Command(5578, 23),
    ]


@pytest.mark.asyncio
async def test_mission_can_only_link_to_a_mowing_polygon_on_the_confirmed_revision(
    monkeypatch,
):
    client = AsyncMock()
    checksum = bytes(16)
    state = {"available": True, "checksum": checksum.hex(), "svg": SVG}
    monkeypatch.setattr(
        "custom_components.automower_ble_plus.mapfiles.async_get_map",
        AsyncMock(return_value=state),
    )
    client.command.return_value = checksum
    await async_verify_mowing_area(client, "lawn", checksum.hex())
    state["svg"] = SVG.replace('fill="green"', 'fill="red"')
    with pytest.raises(ValueError, match="selected mowing polygon"):
        await async_verify_mowing_area(client, "lawn", checksum.hex())
    state["svg"] = SVG
    client.command.return_value = bytes([1] * 16)
    with pytest.raises(ValueError, match="changed while reading"):
        await async_verify_mowing_area(client, "lawn", checksum.hex())
