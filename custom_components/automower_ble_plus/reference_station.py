"""Discover and pair EPOS reference stations through the mower."""

import asyncio
from dataclasses import dataclass, field
from enum import IntEnum
from struct import Struct

from .features import U8, U32, Reading
from .transport import AutomowerClient, CommandRejected
from .wire import Command, Event

_REFERENCE_STATION = 5650
REFERENCE_STATION_EVENTS = Command(_REFERENCE_STATION, 0)
GET_REFERENCE_STATION_ID = Reading(Command(_REFERENCE_STATION, 1), U32)
_SET_STATION_ID = Command(_REFERENCE_STATION, 2)
_SCAN = Command(_REFERENCE_STATION, 3)
_SET_INSTALLED_POSITION = Command(_REFERENCE_STATION, 7)
STOP_REFERENCE_SCAN = Command(_REFERENCE_STATION, 9)
_AVAILABLE = Reading(Command(_REFERENCE_STATION, 15), U8)
_STATION_DISCOVERED = Struct("<Iiii")
_SCAN_SECONDS = 60
REFERENCE_SCAN_TIMEOUT = _SCAN_SECONDS + 10


class ScanStatus(IntEnum):
    """Reference station discovery status."""

    SCANNING = 0
    COMPLETE = 1
    STOPPED = 2
    FAILED = 3


SCAN_OPTIONS = tuple(status.name.lower() for status in ScanStatus)


@dataclass(slots=True)
class ReferenceScan:
    """All stations reported by one firmware scan."""

    done: asyncio.Event = field(default_factory=asyncio.Event)
    station_ids: set[int] = field(default_factory=set)

    def handle_event(self, event: Event) -> None:
        """Collect the IDs and finish on a verified scan-complete event."""
        if self.done.is_set() or event.family != _REFERENCE_STATION:
            return
        if event.event_id == 1 and len(event.payload) == _STATION_DISCOVERED.size:
            station_id, *_ = _STATION_DISCOVERED.unpack(event.payload)
            if 0 < station_id < 0xFFFFFFFF:
                self.station_ids.add(station_id)
        elif event.event_id == 2 and not event.payload:
            self.done.set()


async def async_get_reference_station_id(client: AutomowerClient) -> int | None:
    """Return the installed ID, zero for unpaired, or None for unsupported hardware."""
    try:
        available = _AVAILABLE.decode(await client.command(_AVAILABLE.command))
    except CommandRejected as err:
        if not err.unsupported:
            raise
        # The original ReferenceStation firmwares only expose the installed ID.
    except ValueError:
        return None
    else:
        if available != 1:
            return None
    try:
        station_id = GET_REFERENCE_STATION_ID.decode(
            await client.command(GET_REFERENCE_STATION_ID.command)
        )
    except CommandRejected as err:
        if not err.unsupported:
            raise
        return None
    except ValueError:
        return None
    return 0 if station_id == 0xFFFFFFFF else station_id


async def async_start_reference_scan(client: AutomowerClient) -> None:
    """Subscribe and request the same scan duration used by Automower Connect."""
    await client.command(REFERENCE_STATION_EVENTS)
    await client.command(_SCAN, U32.pack(_SCAN_SECONDS))


async def async_pair_reference_station(
    client: AutomowerClient, station_id: int
) -> None:
    """Set the chosen station, confirm its ID and register its current position."""
    await client.command(_SET_STATION_ID, U32.pack(station_id))
    actual = GET_REFERENCE_STATION_ID.decode(
        await client.command(GET_REFERENCE_STATION_ID.command)
    )
    if actual != station_id:
        raise ValueError("The mower did not store the selected reference station ID")
    await client.command(_SET_INSTALLED_POSITION)
