"""Manage one authenticated BLE session per mower."""

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import replace
from datetime import timedelta
from struct import error as StructError

from automower_ble.models import MowerModels
from automower_ble.protocol import (
    ModeOfOperation,
    MowerActivity,
    MowerState,
    ResponseResult,
)
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, CONF_PIN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, LOGGER
from .features import (
    GET_ACTIVITY,
    GET_BATTERY,
    GET_FIRMWARE,
    GET_GUIDE_COUNT,
    GET_MODEL,
    GET_POSITION,
    GET_POSITION_AVAILABLE,
    GET_STATE,
    GET_STATISTICS,
    MODEL,
    POSITION,
    POSITION_SCALE,
    READINGS,
    STATISTIC_KEYS,
    STATISTICS,
    THEFT_STATUS_READINGS,
    U8,
    U16,
    U32,
    Reading,
    Setting,
    SettingVariant,
    settings_for_model,
)
from .firmware import FirmwareProfile, get_firmware_profile
from .fota import (
    FIRMWARE_TARGETS,
    FirmwareStatus,
    UpdatePhase,
    async_read_firmware_status,
)
from .geofence import (
    GEOFENCE_AVAILABLE,
    GEOFENCE_CENTER_SETTABLE,
    async_get_geofence,
    async_recenter_geofence,
)
from .history import async_get_message_history
from .mapfiles import async_get_map, async_replace_map
from .models import STARTING_POINT_COUNTS
from .schedule import (
    ScheduleTask,
    async_get_schedule,
    async_get_schedule_limits,
    async_replace_schedule,
)
from .sites import (
    InstallationSelection,
    async_activate_installation,
    async_create_installation,
    async_delete_installation,
    async_get_installation_selection,
    async_get_installations,
    async_rename_installation,
)
from .station import async_get_loop_signals, async_pair_charging_station
from .transport import AutomowerClient, CommandRejected, MowerConnectionError
from .wire import Command
from .work_areas import (
    async_get_area_mowing_settings,
    async_get_work_areas,
    async_set_area_mowing_setting,
    async_update_work_area,
)

type MowerEntry = ConfigEntry[MowerCoordinator]
type MowerData = dict[str, int | FirmwareStatus | InstallationSelection]
type CommandBatch = Iterable[tuple[Command, bytes]]

_CLEAR_OVERRIDE = Command(4658, 6)
_SET_MODE = Command(4586, 0)
_MOW_FOR = Command(4658, 3)
_MOW_AREA = Command(4658, 9)
_PARK_FOR = Command(4658, 4)
_PARK_UNTIL_NEXT_START = Command(4658, 5)
_GET_TASK_COUNT = Command(4690, 4)
_START = Command(4586, 4)
_PAUSE = Command(4586, 5)
_START_SPOT_CUTTING = Command(4710, 7)
_SECTOR_MAXIMUM = {
    Command(4716, 3): Command(4716, 4),
    Command(4716, 7): Command(4716, 8),
}
_SECTOR_MINIMUM = {
    Command(4716, 5): Command(4716, 2),
    Command(4716, 9): Command(4716, 6),
}


class MowerCoordinator(DataUpdateCoordinator[MowerData]):
    """Coordinate authenticated requests, motion commands, and available settings."""

    def __init__(self, hass: HomeAssistant, entry: MowerEntry) -> None:
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=60),
            always_update=False,
        )
        self.address: str = entry.data[CONF_ADDRESS]
        self.client = AutomowerClient(self.address, entry.data.get(CONF_PIN))
        self.device_info: DeviceInfo
        self.firmware_profile: FirmwareProfile | None = None
        self.settings: tuple[Setting, ...] = ()
        self.setting_variants: dict[str, SettingVariant] = {}
        self._operation_lock = asyncio.Lock()
        self._unsupported: set[str] = set()
        self._optional_index = 0

    async def _async_connect(self) -> None:
        if self.client.is_connected():
            return
        device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if device is None:
            raise MowerConnectionError("The mower is outside Bluetooth range")
        result = await self.client.connect(device)
        if result in (ResponseResult.INVALID_PIN, ResponseResult.NOT_ALLOWED):
            raise ConfigEntryAuthFailed("The mower rejected its operator PIN")
        if result != ResponseResult.OK:
            raise MowerConnectionError(
                f"The mower returned {result.name} while connecting"
            )

    async def _async_setup(self) -> None:
        guide_count = None
        node_version = None
        try:
            async with self._operation_lock:
                await self._async_connect()
                model_id = MODEL.unpack(await self.client.command(GET_MODEL))
                try:
                    firmware = (
                        (await self.client.command(GET_FIRMWARE))
                        .rstrip(b"\0")
                        .decode("ascii")
                    )
                except CommandRejected, MowerConnectionError, UnicodeError:
                    firmware = None
                if model_id in STARTING_POINT_COUNTS:
                    try:
                        guide_count = min(
                            await self._read(Reading(GET_GUIDE_COUNT, U8)), 3
                        )
                    except CommandRejected, MowerConnectionError, ValueError:
                        pass
                if self.client.is_connected():
                    try:
                        node_version = await self.client.main_node_version()
                    except CommandRejected, MowerConnectionError, UnicodeError:
                        pass
        except (
            MowerConnectionError,
            CommandRejected,
            StructError,
        ) as err:
            raise UpdateFailed("Could not identify the mower") from err
        identity = MowerModels.get(model_id)
        self.firmware_profile = await self.hass.async_add_executor_job(
            get_firmware_profile, model_id, node_version
        )
        self.settings = settings_for_model(
            model_id,
            guide_count,
            self.firmware_profile,
            has_pin=self._configured_pin is not None,
        )
        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, self.address)},
            name=self.config_entry.title,
            manufacturer=identity.manufacturer if identity else "Husqvarna",
            model=identity.model if identity else "Automower",
            model_id=f"{model_id[0]}.{model_id[1]}",
            sw_version=firmware or None,
        )

    async def _async_update_data(self) -> MowerData:
        try:
            async with self._operation_lock:
                await self._async_connect()
                battery = await self._read(Reading(GET_BATTERY, U8))
                state = await self._read(Reading(GET_STATE, U8))
                activity = await self._read(Reading(GET_ACTIVITY, U8))
                return {
                    **(self.data or {}),
                    "battery": battery,
                    "state": state,
                    "activity": activity,
                }
        except (MowerConnectionError, CommandRejected, ValueError) as err:
            raise UpdateFailed("Could not read the mower status") from err

    async def _read(self, reading: Reading) -> int:
        return reading.decode(
            await self.client.command(reading.command, reading.arguments)
        )

    async def _probe_setting(self, setting: Setting) -> int | None:
        previous = self.setting_variants.get(setting.key)
        variants = (
            (previous,) + tuple(v for v in setting.variants if v != previous)
            if previous
            else setting.variants
        )
        has_implemented_variant = False
        for variant in variants:
            try:
                if variant.availability is not None and not await self._read(
                    Reading(variant.availability, U8, variant.read.arguments)
                ):
                    has_implemented_variant = True
                    continue
                value = await self._read(variant.read)
            except CommandRejected as err:
                if err.unsupported:
                    continue
                raise
            if setting.minimum <= value <= setting.maximum:
                self.setting_variants[setting.key] = variant
                return value
            return None
        self.setting_variants.pop(setting.key, None)
        if not has_implemented_variant:
            self._unsupported.add(setting.key)
        return None

    async def async_refresh_optional(self, _now=None) -> None:
        """Discover controls and refresh telemetry without holding up core polling."""
        if not self.last_update_success:
            return
        probes = [(setting.key, setting) for setting in self.settings]
        setting_keys = {setting.key for setting in self.settings}
        probes.extend(
            (key, reading)
            for key, reading in READINGS.items()
            if key not in setting_keys
        )
        probes.extend(
            (("statistics", None), ("position", None), ("installation_selection", None))
        )
        probes.extend((key, None) for key in FIRMWARE_TARGETS)
        if self._configured_pin is not None:
            probes.append(("geofence_center_settable", None))
        if (profile := self.firmware_profile) and profile.blade_usage_reset:
            probes.append(
                (
                    "blade_reset_available",
                    Reading(Command(4726, profile.blade_usage_read), U32),
                )
            )
        for _ in probes:
            key, item = probes[self._optional_index]
            self._optional_index = (self._optional_index + 1) % len(probes)
            if key in self._unsupported:
                continue
            async with self._operation_lock:
                if not self.client.is_connected():
                    return
                values = dict(self.data)
                try:
                    if isinstance(item, Setting):
                        if (value := await self._probe_setting(item)) is not None:
                            values[key] = value
                        else:
                            values.pop(key, None)
                    elif isinstance(item, Reading):
                        value = await self._read(item)
                        if key == "blade_reset_available":
                            values.update(
                                blade_reset_available=1, blade_usage_time=value
                            )
                        else:
                            values[key] = value
                    elif key == "statistics":
                        raw = await self.client.command(GET_STATISTICS)
                        values.update(
                            zip(STATISTIC_KEYS, STATISTICS.unpack(raw), strict=True)
                        )
                    elif key == "geofence_center_settable":
                        available = (
                            await self._read(Reading(GEOFENCE_AVAILABLE, U8)) == 1
                        )
                        values[key] = (
                            await self._read(Reading(GEOFENCE_CENTER_SETTABLE, U8))
                            if available
                            else 0
                        )
                    elif key in FIRMWARE_TARGETS:
                        status = await async_read_firmware_status(
                            self.client, FIRMWARE_TARGETS[key]
                        )
                        if status is None:
                            values.pop(key, None)
                        else:
                            values[key] = status
                    elif key == "installation_selection":
                        selection = await async_get_installation_selection(self.client)
                        if selection is None:
                            values.pop(key, None)
                        else:
                            values[key] = selection
                    else:
                        values.pop("latitude", None)
                        values.pop("longitude", None)
                        if await self._read(Reading(GET_POSITION_AVAILABLE, U8)) == 1:
                            longitude, latitude = POSITION.unpack(
                                await self.client.command(GET_POSITION)
                            )
                            if (
                                (longitude or latitude)
                                and abs(latitude) <= 90 * POSITION_SCALE
                                and abs(longitude) <= 180 * POSITION_SCALE
                            ):
                                values.update(latitude=latitude, longitude=longitude)
                except CommandRejected as err:
                    if err.unsupported:
                        self._unsupported.add(key)
                        if key in FIRMWARE_TARGETS:
                            values.pop(key, None)
                except ValueError, StructError:
                    LOGGER.debug("Unexpected response while reading %s", key)
                except MowerConnectionError:
                    return
                if values != self.data:
                    self.async_set_updated_data(values)

    async def async_keep_alive(self, _now=None) -> None:
        """Keep the link open between updates when no other request is in flight."""
        if self._operation_lock.locked() or not self.client.is_connected():
            return
        async with self._operation_lock:
            try:
                await self.client.keep_alive()
            except CommandRejected, MowerConnectionError:
                LOGGER.debug("Automower keepalive failed")
                return
            data = dict(self.data)
            for key, reading in THEFT_STATUS_READINGS.items():
                if key not in data:
                    continue
                try:
                    data[key] = await self._read(reading)
                except CommandRejected as err:
                    if err.unsupported:
                        self._unsupported.add(key)
                        data.pop(key, None)
                except ValueError:
                    LOGGER.debug("Unexpected response while reading %s", key)
                except MowerConnectionError:
                    return
            if data != self.data:
                self.async_set_updated_data(data)

    def firmware_status(self, key: str) -> FirmwareStatus | None:
        """Return the latest firmware update information for this recipient."""
        status = self.data.get(key)
        return status if isinstance(status, FirmwareStatus) else None

    async def async_firmware_action(
        self, key: str, *, check: bool = False, expected_version: str | None = None
    ) -> None:
        """Check for vendor firmware or install the package confirmed by the user."""
        target = FIRMWARE_TARGETS[key]
        async with self._operation_lock:
            try:
                await self._async_connect()
                status = await async_read_firmware_status(self.client, target)
                if status is None:
                    raise ValueError("This device does not report firmware updates")
                if check:
                    if status.phase != UpdatePhase.IDLE:
                        raise ValueError("The firmware updater is not idle")
                    await self.client.command(
                        Command(target.family, target.check_operation)
                    )
                    return

                if status.phase != UpdatePhase.READY or not expected_version:
                    raise ValueError("There is no downloaded firmware ready to install")
                if (
                    status.offered != expected_version
                    or status.installed == expected_version
                ):
                    raise ValueError("The offered firmware version has changed")
                activity = await self._read(Reading(GET_ACTIVITY, U8))
                if activity not in (MowerActivity.PARKED, MowerActivity.CHARGING):
                    raise ValueError(
                        "Park the mower in its charging station before updating"
                    )
                await self.client.command(
                    Command(target.family, target.install_operation)
                )
            except (CommandRejected, MowerConnectionError, ValueError) as err:
                raise HomeAssistantError(
                    f"Firmware update request failed: {err}"
                ) from err
            self.async_set_updated_data(
                {**self.data, key: replace(status, phase=UpdatePhase.INSTALLING)}
            )

    async def async_set_setting(self, setting: Setting, value: int) -> None:
        """Change a supported setting and retrieve the value stored by the mower."""
        async with self._operation_lock:
            variant = self.setting_variants.get(setting.key)
            if variant is None:
                raise HomeAssistantError(
                    "This mower does not report this setting as available"
                )
            try:
                await self._async_connect()
                payload = setting.encode(variant, value)
                if variant.requires_pin:
                    payload = self._pin_payload() + payload
                if (
                    maximum := _SECTOR_MAXIMUM.get(variant.write)
                ) and value > await self._read(Reading(maximum, U16)):
                    raise ValueError(
                        "The start angle must be at or below the end angle"
                    )
                if (
                    minimum := _SECTOR_MINIMUM.get(variant.write)
                ) and value < await self._read(Reading(minimum, U16)):
                    raise ValueError(
                        "The end angle must be at or above the start angle"
                    )
                await self.client.command(variant.write, payload)
                actual = await self._read(variant.read)
            except (CommandRejected, MowerConnectionError, ValueError) as err:
                raise HomeAssistantError(f"Could not set {setting.key}: {err}") from err
            self.async_set_updated_data({**self.data, setting.key: actual})

    @property
    def _configured_pin(self) -> int | None:
        pin = self.config_entry.data.get(CONF_PIN)
        return int(pin) if pin is not None and pin != "" else None

    def _pin_payload(self) -> bytes:
        pin = self._configured_pin
        if pin is None:
            raise ValueError("This operation needs the configured mower PIN")
        return U16.pack(pin)

    async def _async_run(self, commands: CommandBatch) -> None:
        for command, payload in commands:
            await self.client.command(command, payload)

    async def _async_clear_override(self) -> None:
        try:
            await self.client.command(_CLEAR_OVERRIDE)
        except CommandRejected as err:
            if err.result != ResponseResult.NOT_AVAILABLE:
                raise

    async def _async_start(self) -> None:
        try:
            await self.client.command(_START)
        except CommandRejected as err:
            if err.result != ResponseResult.UNKNOWN_ERROR:
                raise
            # Some mowers acknowledge StartTrigger with UNKNOWN_ERROR even when started.
            await asyncio.sleep(2)
            state = await self._read(Reading(GET_STATE, U8))
            activity = await self._read(Reading(GET_ACTIVITY, U8))
            if state != MowerState.IN_OPERATION or activity not in (
                MowerActivity.GOING_OUT,
                MowerActivity.MOWING,
            ):
                raise

    async def _async_mow_for(
        self,
        duration: int,
        mission_id: int | None = None,
        *,
        mode: ModeOfOperation = ModeOfOperation.AUTO,
    ) -> None:
        if mode == ModeOfOperation.AUTO:
            await self._async_clear_override()
        await self.client.command(_SET_MODE, U8.pack(mode))
        if mission_id is None:
            await self.client.command(_MOW_FOR, U32.pack(duration))
        else:
            await self.client.command(
                _MOW_AREA, U32.pack(duration) + U32.pack(mission_id)
            )
        await self._async_start()

    async def async_mower_action(
        self, action: str, duration: int | None = None, *, mission_id: int | None = None
    ) -> None:
        """Run a named mowing action and publish the next reported state."""
        async with self._operation_lock:
            try:
                await self._async_connect()
                if action == "start":
                    if self.data["state"] == MowerState.PAUSED:
                        await self._async_start()
                    else:
                        await self._async_mow_for(10800)
                elif action == "park":
                    count = U32.unpack(await self.client.command(_GET_TASK_COUNT))[0]
                    if count:
                        await self.client.command(_PARK_UNTIL_NEXT_START)
                    else:
                        await self.client.command(_SET_MODE, b"\x02")
                elif action == "pause":
                    await self.client.command(_PAUSE)
                elif action == "resume_schedule":
                    await self._async_clear_override()
                    await self.client.command(_SET_MODE, b"\0")
                elif action == "park_indefinitely":
                    await self.client.command(_SET_MODE, b"\x02")
                elif action == "reset_blade_usage":
                    await self._async_reset_blade_usage()
                elif action == "recenter_geofence":
                    await async_recenter_geofence(self.client, self._pin_payload())
                elif action == "mow_secondary_area":
                    ModeOfOperation(await self._read(READINGS["operation_mode"]))
                    if mission_id is not None:
                        if duration is None:
                            raise ValueError(
                                "Specify a duration when selecting a work area"
                            )
                        await self._async_check_work_area(mission_id)
                    await self._async_mow_for(
                        duration or 0, mission_id, mode=ModeOfOperation.MANUAL
                    )
                elif action in ("mow_for", "park_for") and duration is not None:
                    if action == "mow_for":
                        if mission_id is not None:
                            await self._async_check_work_area(mission_id)
                        await self._async_mow_for(duration, mission_id)
                    else:
                        await self.client.command(_PARK_FOR, U32.pack(duration))
                elif (
                    action == "spot_cut"
                    and self.firmware_profile is not None
                    and self.firmware_profile.spot_cut_trigger
                    and self.data.get("spot_cut_available") == 1
                ):
                    await self.client.command(_PAUSE)
                    await self._async_clear_override()
                    await self._async_run(
                        (
                            (_SET_MODE, b"\0"),
                            (_START_SPOT_CUTTING, b""),
                            (_MOW_FOR, U32.pack(1800)),
                        )
                    )
                    await self._async_start()
                else:
                    raise ValueError(f"Unsupported mower action: {action}")
            except (
                CommandRejected,
                MowerConnectionError,
                ValueError,
                StructError,
            ) as err:
                raise HomeAssistantError(
                    f"Could not execute mower action {action}: {err}"
                ) from err
        await self.async_request_refresh()

    async def _async_reset_blade_usage(self) -> None:
        profile = self.firmware_profile
        if not profile or not profile.blade_usage_reset:
            raise ValueError("The firmware does not advertise a blade counter reset")
        counter = Reading(Command(4726, profile.blade_usage_read), U32)
        await self._read(counter)
        await self.client.command(Command(4726, profile.blade_usage_reset))
        usage = await self._read(counter)
        self.async_set_updated_data({**(self.data or {}), "blade_usage_time": usage})

    async def async_read_schedule(self) -> dict:
        """Read the complete weekly schedule with the mower's limits."""
        async with self._operation_lock:
            try:
                await self._async_connect()
                limits = await async_get_schedule_limits(self.client)
                tasks = await async_get_schedule(self.client)
            except (
                MowerConnectionError,
                CommandRejected,
                ValueError,
                StructError,
            ) as err:
                raise HomeAssistantError(
                    f"Could not read the mower schedule: {err}"
                ) from err
        return {
            "max_intervals": limits[0],
            "max_intervals_per_day": limits[1],
            "intervals": [task.as_response() for task in tasks],
        }

    async def async_write_schedule(self, tasks: Sequence[ScheduleTask]) -> None:
        """Send a requested replacement and verify what the mower stored."""
        async with self._operation_lock:
            try:
                await self._async_connect()
                await async_replace_schedule(self.client, tasks)
            except (
                MowerConnectionError,
                CommandRejected,
                ValueError,
                StructError,
            ) as err:
                raise HomeAssistantError(
                    f"Could not confirm the mower schedule update: {err}"
                ) from err
        await self.async_request_refresh()

    async def async_read_history(self, offset: int, limit: int) -> dict:
        """Read one page of the mower's local messages."""
        async with self._operation_lock:
            try:
                await self._async_connect()
                return await async_get_message_history(self.client, offset, limit)
            except (
                MowerConnectionError,
                CommandRejected,
                ValueError,
                StructError,
            ) as err:
                raise HomeAssistantError(
                    f"Could not read the mower message history: {err}"
                ) from err

    async def _async_check_work_area(self, mission_id: int) -> None:
        # Checking the specific area is safe even when the area list is large.
        area_type = Command(5604, 13)
        U8.unpack(await self.client.command(area_type, U32.pack(mission_id)))

    async def async_read_work_areas(self, offset: int, limit: int) -> dict:
        """Read one page of named work areas from a compatible mower."""
        async with self._operation_lock:
            try:
                await self._async_connect()
                return await async_get_work_areas(self.client, offset, limit)
            except (
                MowerConnectionError,
                CommandRejected,
                ValueError,
                StructError,
            ) as err:
                raise HomeAssistantError(
                    f"Could not read the mower work areas: {err}"
                ) from err

    async def async_shutdown(self) -> None:
        """Release the Bluetooth connection and the coordinator listeners."""
        await super().async_shutdown()
        async with self._operation_lock:
            await self.client.disconnect()

    async def async_change_work_area(
        self, mission_id: int, *, name: str | None, enabled: bool | None
    ) -> None:
        """Change one supported work area setting and check the result."""
        async with self._operation_lock:
            try:
                await self._async_connect()
                await async_update_work_area(
                    self.client, mission_id, name=name, enabled=enabled
                )
            except (
                MowerConnectionError,
                CommandRejected,
                ValueError,
                StructError,
            ) as err:
                raise HomeAssistantError(
                    f"Could not update the work area: {err}"
                ) from err

    async def async_read_work_area_mowing(self, mission_id: int) -> dict:
        """Read the settings for an individual mowing area."""
        profile = self.firmware_profile
        async with self._operation_lock:
            try:
                await self._async_connect()
                return await async_get_area_mowing_settings(
                    self.client,
                    mission_id,
                    profile.work_area_height_max if profile else 0,
                )
            except (
                MowerConnectionError,
                CommandRejected,
                ValueError,
                StructError,
            ) as err:
                raise HomeAssistantError(
                    f"Could not read work area mowing settings: {err}"
                ) from err

    async def async_write_work_area_mowing(
        self, mission_id: int, changes: dict
    ) -> None:
        """Write one mowing setting for an individual area."""
        profile = self.firmware_profile
        async with self._operation_lock:
            try:
                await self._async_connect()
                await async_set_area_mowing_setting(
                    self.client,
                    mission_id,
                    profile.work_area_height_max if profile else 0,
                    changes,
                )
            except (
                MowerConnectionError,
                CommandRejected,
                ValueError,
                StructError,
            ) as err:
                raise HomeAssistantError(
                    f"Could not update work area mowing settings: {err}"
                ) from err

    async def async_read_installations(self, offset: int, limit: int) -> dict:
        """Read the installation profiles via the current authenticated connection."""
        async with self._operation_lock:
            try:
                await self._async_connect()
                result = await async_get_installations(self.client, offset, limit)
                if result["available"]:
                    profile = self.firmware_profile
                    result["can_create_empty"] = bool(
                        profile and profile.installation_create_empty
                    )
                return result
            except (
                MowerConnectionError,
                CommandRejected,
                ValueError,
                StructError,
            ) as err:
                raise HomeAssistantError(
                    f"Could not read the mower installations: {err}"
                ) from err

    async def async_set_installation(
        self, site_id: int, expected_active_id: int | None = None
    ) -> None:
        """Switch profiles and refresh the entities with the mower's new state."""
        await self._async_change_installations(
            async_activate_installation, site_id, expected_active_id
        )
        await self.async_refresh_optional()

    async def async_change_installation_name(self, site_id: int, name: str) -> None:
        """Update the active installation where the mower allows profile editing."""
        await self._async_change_installations(async_rename_installation, site_id, name)

    async def async_create_installation(self, copy_current: bool) -> dict:
        """Request a new installation and return the mower's assigned ID."""
        profile = self.firmware_profile
        if not copy_current and not (profile and profile.installation_create_empty):
            raise HomeAssistantError(
                "This firmware does not expose empty installation creation"
            )
        return await self._async_change_installations(
            async_create_installation, copy_current
        )

    async def async_delete_installation(self, site_id: int, expected_name: str) -> None:
        """Remove an inactive installation after checking its identity."""
        await self._async_change_installations(
            async_delete_installation, site_id, expected_name
        )

    async def _async_change_installations[T](
        self, operation: Callable[..., Awaitable[T]], *args
    ) -> T:
        async with self._operation_lock:
            try:
                await self._async_connect()
                result = await operation(self.client, *args)
            except (
                MowerConnectionError,
                CommandRejected,
                ValueError,
                StructError,
            ) as err:
                raise HomeAssistantError(
                    f"Could not update installations: {err}"
                ) from err
            data = dict(self.data)
            data.pop("installation_selection", None)
            try:
                selection = await async_get_installation_selection(self.client)
            except MowerConnectionError, CommandRejected, ValueError, StructError:
                LOGGER.debug("Could not refresh the installation selection")
            else:
                if selection is not None:
                    data["installation_selection"] = selection
            self.async_set_updated_data(data)
            return result

    async def async_read_map(self, generated: bool) -> dict:
        """Read map geometry from the mower's native BLE attributes."""
        async with self._operation_lock:
            try:
                await self._async_connect()
                return await async_get_map(self.client, generated)
            except (
                MowerConnectionError,
                CommandRejected,
                ValueError,
                StructError,
            ) as err:
                raise HomeAssistantError(
                    f"Could not read the mower map: {err}"
                ) from err

    async def async_read_geofence(self) -> dict:
        """Read the available local GeoFence settings."""
        async with self._operation_lock:
            try:
                await self._async_connect()
                return await async_get_geofence(self.client)
            except (
                MowerConnectionError,
                CommandRejected,
                ValueError,
                StructError,
            ) as err:
                raise HomeAssistantError(
                    f"Could not read the mower GeoFence: {err}"
                ) from err

    async def async_read_loop_signals(self) -> dict:
        """Read the installation's wire-loop measurements over Bluetooth."""
        profile = self.firmware_profile
        if profile is None or not profile.loop_sensor_positions:
            return {"available": False}
        async with self._operation_lock:
            try:
                await self._async_connect()
                return await async_get_loop_signals(
                    self.client, profile.loop_sensor_positions
                )
            except (MowerConnectionError, CommandRejected, StructError) as err:
                raise HomeAssistantError(f"Could not read loop signals: {err}") from err

    async def async_pair_station(self) -> None:
        """Pair with the physical station currently holding the mower."""
        profile = self.firmware_profile
        if profile is None or not profile.station_pairing_subscription:
            raise HomeAssistantError("This mower does not expose station pairing")
        async with self._operation_lock:
            try:
                await self._async_connect()
                await async_pair_charging_station(
                    self.client, profile.station_pairing_subscription
                )
            except (
                MowerConnectionError,
                CommandRejected,
                ValueError,
                StructError,
            ) as err:
                raise HomeAssistantError(
                    f"Could not pair the charging station: {err}"
                ) from err

    async def async_write_map(self, svg: str, expected_checksum: str) -> dict:
        """Replace the prior map revision confirmed by the user."""
        async with self._operation_lock:
            try:
                await self._async_connect()
                return await async_replace_map(self.client, svg, expected_checksum)
            except (
                MowerConnectionError,
                CommandRejected,
                ValueError,
                StructError,
            ) as err:
                raise HomeAssistantError(
                    f"Could not update the mower map: {err}"
                ) from err
