"""Validate Automower map fragment layouts and commit behavior."""

import gzip
from hashlib import md5, sha256
from io import BytesIO
from struct import pack, unpack_from
from unittest.mock import AsyncMock
from zlib import crc32

import pytest
from automower_ble.protocol import ResponseResult
from PIL import Image

from custom_components.automower_ble_plus.map_features import (
    add_map_feature,
    describe_map_features,
    remove_map_feature,
    update_map_feature_settings,
    update_map_geometry,
)
from custom_components.automower_ble_plus.map_preview import render_map_png
from custom_components.automower_ble_plus.mapfiles import (
    MapKind,
    async_get_map,
    async_read_map_kind,
    async_read_map_status,
    async_replace_map,
    async_verify_mowing_area,
)
from custom_components.automower_ble_plus.transport import CommandRejected
from custom_components.automower_ble_plus.wire import Command

SVG = '<svg><polygon id="lawn" stroke="green" fill="green" points="0,0 1500,0 0,1200"/></svg>'


def test_create_and_remove_poi_path_in_empty_namespaced_map():
    empty = '<svg xmlns="http://www.w3.org/2000/svg"/>'
    path = [{"x_mm": -3000, "y_mm": -10}, {"x_mm": 5000, "y_mm": 15}]
    path_type = "poi_path"
    changed = add_map_feature(empty, "poi", path_type, "Limpieza", path, 2000, 2000)
    features = describe_map_features(changed, MapKind.SITE, True, 2000)
    assert len(features) == 1
    assert features[0]["points"] == path
    assert features[0]["type"] == path_type
    with pytest.raises(ValueError, match="already has a point of interest path"):
        add_map_feature(changed, "another", path_type, "Duplicado", path, 2000, 2000)
    assert remove_map_feature(changed, "poi") == (empty, path_type)


def test_map_geometry_update_preserves_namespace_and_other_features():
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg">'
        '<polygon id="lawn" title="Jardín" fill="green" points="0,0 1500,0 0,1200"/>'
        '<polygon id="wild" title="Flores" fill="red" display="none" '
        'rewildingzone="true" points="0,0 100,0 0,100"/>'
        '<polyline id="dock" stroke="yellow" points="0,0 100,0"/></svg>'
    )
    initial = describe_map_features(svg, MapKind.SITE, True)
    assert initial[1]["type"] == "rewilding_zone"
    assert not initial[1]["enabled"]
    assert not initial[2]["editable"]
    assert update_map_geometry(svg, "lawn", initial[0]["points"]) == svg
    points = [
        {"x_mm": 0, "y_mm": 0},
        {"x_mm": 1400, "y_mm": 0},
        {"x_mm": 0, "y_mm": 1200},
    ]
    updated = update_map_geometry(svg, "lawn", points)
    assert updated == svg.replace("1500,0", "1400,0")
    assert describe_map_features(updated, MapKind.SITE, True) == [
        {**initial[0], "points": points},
        *initial[1:],
    ]
    with pytest.raises(ValueError, match="does not support editing"):
        update_map_geometry(svg, "dock", points[:2])


def test_map_settings_preserve_geometry_and_enforce_model_path_width():
    svg = SVG.replace(
        "</svg>",
        '<polyline id="pass" title="Original" stroke="blue" '
        'stroke-width="3000" points="0,0 1000,0"/></svg>',
    )
    changed = update_map_feature_settings(
        svg, "pass", "Árbol & valla", False, 1200, 1200
    )
    work, path = describe_map_features(changed, MapKind.SITE, True, 1200)
    assert work["id"] == "lawn"
    assert "editable_settings" not in work
    assert path == {
        "id": "pass",
        "type": "transport_path",
        "name": "Árbol & valla",
        "enabled": False,
        "editable": True,
        "points": [{"x_mm": 0, "y_mm": 0}, {"x_mm": 1000, "y_mm": 0}],
        "width_mm": 1200,
        "width_limits_mm": {"min": 1200, "max": 5000},
        "editable_settings": ["name", "enabled", "width_mm"],
    }
    assert (
        update_map_feature_settings(changed, "pass", None, False, 1200, 1200) == changed
    )
    restored = update_map_feature_settings(
        changed, "pass", "Original", True, 3000, 2000
    )
    assert restored == svg
    with pytest.raises(ValueError, match="3000 to 5000 mm"):
        update_map_feature_settings(svg, "pass", None, None, 2000, 3000)
    with pytest.raises(ValueError, match="associated mission"):
        update_map_feature_settings(svg, "lawn", "Nueva", True, None, 1200)
    with pytest.raises(ValueError, match="does not support editing"):
        update_map_feature_settings(
            svg.replace('id="pass"', 'id="main_area_0"'),
            "main_area_0",
            "Nueva",
            None,
            None,
            1200,
        )


def test_zone_map_editing_targets_exclusions_and_preserves_other_shapes():
    zone = '<polygon id="flowers" title="Flores" fill="red" stroke="red" points="0,0 30,0 0,25"/>'
    svg = SVG.replace("</svg>", zone + "</svg>")
    work_area, exclusion = describe_map_features(svg, MapKind.ZONES, True)
    assert not work_area["editable"]
    assert exclusion["editable_settings"] == ["name", "enabled"]
    with pytest.raises(ValueError, match="does not support editing"):
        remove_map_feature(svg, "lawn", kind=MapKind.ZONES)
    assert remove_map_feature(svg, "flowers", kind=MapKind.ZONES) == (
        SVG,
        "stay_out_zone",
    )


@pytest.mark.parametrize(
    "coordinates",
    [
        [(0, 0), (10, 10), (0, 10), (10, 0)],
        [(0, 0), (0, 100), (0, 50), (100, 50)],
        [(0, 0), (10, 0), (20, 0)],
    ],
)
def test_map_geometry_rejects_crossing_overlapping_or_degenerate_polygons(coordinates):
    with pytest.raises(ValueError, match="crosses or overlaps itself"):
        update_map_geometry(
            SVG, "lawn", [{"x_mm": x, "y_mm": y} for x, y in coordinates]
        )


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
        "checksum": sha256(SVG.encode()).hexdigest(),
    }
    calls = client.command.await_args_list
    assert calls[1].args == (Command(5578, 38),)
    assert unpack_from("<HH", calls[2].args[1]) == (5578, 103)


@pytest.mark.asyncio
@pytest.mark.parametrize("modern_reply", [b"\x00", ResponseResult.NOT_AVAILABLE])
async def test_legacy_aim_fallback_respects_an_explicit_unavailable_map(modern_reply):
    client = AsyncMock()
    command = Command(5578, 38)
    reply = (
        modern_reply
        if isinstance(modern_reply, bytes)
        else CommandRejected(command, modern_reply)
    )
    client.command.side_effect = [b"\x01", reply]
    result = await async_read_map_kind(client, MapKind.ZONES, legacy_aim_maps=True)
    assert result == {"available": False, "map_type": "zones"}
    assert [call.args for call in client.command.await_args_list] == [
        (Command(5578, 0),),
        (command,),
    ]


@pytest.mark.asyncio
async def test_legacy_aim_status_omits_empty_map_and_keeps_the_other_map():
    client = AsyncMock()
    client.command.side_effect = [
        b"\x01",
        CommandRejected(Command(5578, 40), ResponseResult.INVALID_ID),
        CommandRejected(Command(5578, 43), ResponseResult.INVALID_ID),
        pack("<I", 42),
        CommandRejected(Command(5578, 38), ResponseResult.INVALID_ID),
        pack("<I", 0),
        pack("<IIBI", 0, 0, 1, 0),
        pack("<I", 0),
    ]
    assert await async_read_map_status(client, legacy_aim_maps=True) == {
        MapKind.GENERATED: "0000002a"
    }
    calls = client.command.await_args_list
    assert calls[3].args[0] == Command(5786, 15)
    assert unpack_from("<HH", calls[3].args[1]) == (5578, 101)
    assert calls[6].args[0] == Command(5786, 7)
    assert unpack_from("<HH", calls[6].args[1]) == (5578, 103)


@pytest.mark.asyncio
async def test_legacy_aim_rejects_map_changed_during_download():
    client = AsyncMock()
    raw = gzip.compress(SVG.encode())
    client.command.side_effect = [
        b"\x01",
        CommandRejected(Command(5578, 38), ResponseResult.INVALID_ID),
        pack("<I", 7),
        pack("<IIBI", crc32(raw), len(raw), 1, len(raw)) + raw,
        pack("<I", 13),
    ]
    with pytest.raises(ValueError, match="map changed during the download"):
        await async_read_map_kind(client, MapKind.ZONES, legacy_aim_maps=True)


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
        "custom_components.automower_ble_plus.mapfiles.async_read_map_kind",
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
