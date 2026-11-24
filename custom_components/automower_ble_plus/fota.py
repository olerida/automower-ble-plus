"""Identify and manage vendor firmware already offered by the mower."""

from dataclasses import dataclass
from enum import IntEnum

from .features import GET_FIRMWARE, U8, Reading
from .transport import AutomowerClient, CommandRejected
from .wire import Command


class UpdatePhase(IntEnum):
    """Firmware update phases shared by the mower and charging station."""

    DISABLED = 0
    IDLE = 1
    DOWNLOADING = 2
    PAUSED = 3
    READY = 4
    INSTALLING = 5
    SCHEDULED = 6


@dataclass(frozen=True, slots=True)
class FirmwareTarget:
    """HCP operations for one firmware recipient."""

    family: int
    state_operation: int
    version_operation: int
    check_operation: int
    install_operation: int
    installed: Command
    progress_operation: int | None = None


FIRMWARE_TARGETS = {
    "mower_firmware_update": FirmwareTarget(5296, 6, 9, 8, 10, GET_FIRMWARE, 36),
    "station_firmware_update": FirmwareTarget(6244, 4, 7, 6, 8, Command(6552, 13)),
}


@dataclass(frozen=True, slots=True)
class FirmwareStatus:
    """The installed package and the update reported by the device."""

    phase: UpdatePhase
    installed: str
    offered: str | None
    progress: int | None


async def async_read_firmware_status(
    client: AutomowerClient, target: FirmwareTarget
) -> FirmwareStatus | None:
    """Read the device's own update state and versions."""
    available = Reading(Command(target.family, 1), U8)
    if available.decode(await client.command(available.command)) != 1:
        return None
    phase_reader = Reading(Command(target.family, target.state_operation), U8)
    phase = UpdatePhase(phase_reader.decode(await client.command(phase_reader.command)))
    installed = (
        (await client.command(target.installed))
        .partition(b"\0")[0]
        .decode("ascii")
        .strip()
    )
    if not installed:
        return None

    offered = None
    if phase not in (UpdatePhase.DISABLED, UpdatePhase.IDLE):
        offered = (
            await client.command(Command(target.family, target.version_operation))
        ).partition(b"\0")[0].decode("ascii").strip() or None

    progress = None
    if phase == UpdatePhase.DOWNLOADING and target.progress_operation is not None:
        reader = Reading(Command(target.family, target.progress_operation), U8)
        try:
            reported = reader.decode(await client.command(reader.command))
        except CommandRejected as err:
            if not err.unsupported:
                raise
        else:
            if reported <= 100:
                progress = reported
    return FirmwareStatus(phase, installed, offered, progress)
