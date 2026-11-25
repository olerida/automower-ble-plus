"""Check wire diagnostics and station pairing guards."""

from struct import pack
from unittest.mock import AsyncMock, call

import pytest

from custom_components.automower_ble_plus.station import (
    async_get_loop_signals,
    async_pair_charging_station,
)
from custom_components.automower_ble_plus.transport import CommandRejected
from custom_components.automower_ble_plus.wire import Command


@pytest.mark.asyncio
async def test_loop_diagnostics_preserve_signed_signals_and_omit_uninstalled_loops():
    client = AsyncMock()
    client.command.side_effect = [
        b"\x01\x00\x00\x01\x00\x00",
        pack("<6h", -122, 33, 8, 224, 9, 10),
        b"\x63",
        pack("<6h", 83, 55, 9, -17, 10, 11),
        CommandRejected(Command(4462, 14), 7),
    ]
    assert await async_get_loop_signals(client, "front_center rear_center") == {
        "available": True,
        "installed_loops": ["A", "G1"],
        "sensors": {
            "front_center": {"signals": {"A": -122, "G1": 224}, "signal_quality": 99},
            "rear_center": {"signals": {"A": 83, "G1": -17}},
        },
    }
    assert client.command.await_args_list == [
        call(Command(4462, 29)),
        call(Command(4462, 13), b"\0"),
        call(Command(4462, 14), b"\0"),
        call(Command(4462, 13), b"\x01"),
        call(Command(4462, 14), b"\x01"),
    ]


@pytest.mark.asyncio
async def test_unverified_loop_layout_is_not_probed():
    client = AsyncMock()
    assert await async_get_loop_signals(client, "") == {"available": False}
    client.command.assert_not_awaited()


@pytest.mark.asyncio
async def test_station_pairing_requires_mower_in_station_even_if_activity_is_parked():
    client = AsyncMock()
    client.command.side_effect = [b"\0"]
    with pytest.raises(ValueError, match="charging station"):
        await async_pair_charging_station(client, 12)
    client.command.assert_awaited_once_with(Command(4692, 27))
    client.command_and_wait_for_event.assert_not_awaited()
