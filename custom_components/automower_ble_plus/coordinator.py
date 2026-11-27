"""Manage one authenticated BLE session per mower."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Sequence
from contextlib import asynccontextmanager, suppress
from dataclasses import replace
from datetime import timedelta
from functools import partial
from struct import error as StructError
from time import monotonic
from uuid import uuid4

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
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .aim import (
    AIM_EVENTS,
    AIM_KEYS,
    AIM_READY,
    START_AIM_MAPPING,
    async_aim_map_ready,
    async_read_aim_status,
    is_aim_update,
)
from .appdrive import (
    APPDRIVE_ABORT,
    APPDRIVE_DOCK,
    APPDRIVE_DRIVE,
    APPDRIVE_EVENTS,
    APPDRIVE_INPUT_TIMEOUT,
    APPDRIVE_MANOEUVRE_TIMEOUT,
    APPDRIVE_MANOEUVRES,
    APPDRIVE_STATE,
    APPDRIVE_STATUS_KEYS,
    APPDRIVE_SUPPORTED,
    AppDriveControl,
    AppDriveManoeuvre,
    AppDriveManoeuvreStatus,
    AppDriveState,
    async_read_appdrive_status,
    async_remote_control_available,
    drive_payload,
)
from .connect_module import (
    CONNECT_EVENTS,
    CONNECT_STATUS_KEYS,
    async_read_connect_module,
    is_connect_update,
)
from .const import DOMAIN, LOGGER
from .epos import (
    EPOS_AVAILABLE,
    EPOS_CLOUD_EVENTS,
    EPOS_ENABLED,
    EPOS_EVENTS,
    EPOS_STATUS_KEYS,
    RUN_DOCKING_PATTERN,
    SET_CLOUD_ORIGIN,
    SET_EPOS_ENABLED,
    STOP_DOCKING_PATTERN,
    async_enable_epos_cloud,
    async_epos_cloud_accepts_origin_setup,
    async_epos_enabled,
    async_epos_origin_confirmed,
    async_epos_origin_settable,
    async_read_docking_status,
    async_read_epos_status,
    async_set_dock_by_wire,
    is_epos_update,
)
from .faults import CONFIRM_ERROR, FAULT_READINGS
from .features import (
    CONNECT_ENABLED_KEY,
    CONNECT_FAMILY,
    GET_ACTIVITY,
    GET_BATTERY,
    GET_FIRMWARE,
    GET_GUIDE_COUNT,
    GET_MODEL,
    GET_STATE,
    GET_STATISTICS,
    MINIMUM_LEGACY_BOUNDARY_CORRIDOR,
    MODEL,
    READINGS,
    SET_LEGACY_BOUNDARY_CORRIDOR,
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
from .history import async_get_message_history, decode_message_event
from .identity import (
    GET_MOWER_NAME,
    GET_SERIAL_NUMBER,
    SET_MOWER_NAME,
    decode_mower_name,
    encode_mower_name,
)
from .map_features import (
    CREATABLE_MAP_TYPES,
    MAX_PATH_WIDTH,
    add_map_feature,
    describe_map_features,
    has_parking_destination,
    remove_map_feature,
    update_map_feature_settings,
    update_map_geometry,
)
from .map_preview import render_map_png
from .mapfiles import (
    MAP_EVENT_KINDS,
    MAP_EVENTS,
    MAP_PREVIEW_KEYS,
    MapKind,
    async_read_map_kind,
    async_read_map_status,
    async_replace_map,
    async_replace_zone_map,
    async_verify_mowing_area,
)
from .models import DEFAULT_MAP_PATH_WIDTH_MINIMUM, MAP_PATH_WIDTH_MINIMUMS
from .mower_clock import MOWER_CLOCK, async_sync_mower_clock
from .planner import PlannerOverride, async_read_planner_override
from .position import (
    GPS_COORDINATE_KEYS,
    GPS_POSITION_EVENTS,
    GPS_POSITION_REPORT,
    LOCAL_POSITION_KEYS,
    async_describe_position,
    async_read_gps_position,
    async_read_local_pose,
    decode_gps_position,
)
from .reference_station import (
    REFERENCE_SCAN_TIMEOUT,
    REFERENCE_STATION_EVENTS,
    STOP_REFERENCE_SCAN,
    ReferenceScan,
    ScanStatus,
    async_get_reference_station_id,
    async_pair_reference_station,
    async_start_reference_scan,
)
from .schedule import (
    ScheduleTask,
    WeeklySchedule,
    async_get_schedule,
    async_get_schedule_limits,
    async_get_weekly_schedule,
    async_replace_schedule,
    work_area_schedule_revision,
)
from .sites import (
    InstallationSelection,
    async_activate_installation,
    async_active_installation_id,
    async_create_installation,
    async_delete_installation,
    async_empty_installation_id,
    async_get_installation_selection,
    async_get_installations,
    async_rename_installation,
)
from .station import async_get_loop_signals, async_pair_charging_station
from .transport import AutomowerClient, CommandRejected, MowerConnectionError
from .vision import VISION_STATUS_KEYS, async_read_vision_telemetry
from .wifi import (
    REMOVE_WIFI,
    SET_WIFI_ENABLED,
    START_WIFI_SCAN,
    STOP_WIFI_SCAN,
    TRY_ADD_WIFI,
    TRY_SAVED_WIFI,
    WIFI_AVAILABLE,
    WIFI_ENABLED,
    WIFI_FAMILY,
    WIFI_STATUS_EVENTS,
    WIFI_STATUS_KEYS,
    WIFI_TIMEOUT,
    WifiJoin,
    WifiNetwork,
    WifiScan,
    async_read_saved_wifi,
    async_read_wifi_status,
    encode_ssid,
    encode_wifi_password,
)
from .wire import Command, Event
from .wire_test import WireTest, WireTestPhase, WireTestRoute
from .work_areas import (
    async_confirm_schedulable_area,
    async_create_work_area,
    async_delete_work_area,
    async_get_area_mowing_settings,
    async_get_linked_work_areas,
    async_get_work_area_status,
    async_get_work_areas,
    async_mission_ids,
    async_require_work_area_capacity,
    async_reset_work_area_progress,
    async_set_area_mowing_setting,
    async_update_work_area,
    async_work_areas_available,
)

type MowerEntry = ConfigEntry[MowerCoordinator]
type MowerData = dict[
    str,
    int
    | str
    | FirmwareStatus
    | InstallationSelection
    | PlannerOverride
    | WeeklySchedule,
]
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
_RESET_USER_SETTINGS = Command(4698, 8)
_RESET_USER_SETTINGS_AND_RESTART = Command(4698, 42)
_IS_OPERATOR_LOGGED_IN = Command(4664, 3)
_SET_OPERATOR_PIN = Command(4664, 5)
_START_SPOT_CUTTING = Command(4710, 7)
_MOWER_EVENTS = Command(4586, 7)
_MESSAGE_EVENTS = Command(4730, 4)
_CALENDAR_EVENTS = Command(4690, 0)
_CALENDAR_TIME_UPDATED = 0
_CALENDAR_TASKS_UPDATED = 1
_MAP_PREVIEW_RECHECK_SECONDS = 900
_AIM_REGENERATION_RECHECK_SECONDS = 60
_EPOS_ORIGIN_POLL_SECONDS = 5
_EPOS_ORIGIN_TIMEOUT = 300
_DOCKING_INSTALLATION_START_GRACE = 15
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
        self.client.event_callback = self._async_on_event
        self.device_info: DeviceInfo
        self.firmware_profile: FirmwareProfile | None = None
        self._site_map_snapshot: tuple[str, str, bool] | None = None
        self._map_path_min_width = DEFAULT_MAP_PATH_WIDTH_MINIMUM
        self.settings: tuple[Setting, ...] = ()
        self.setting_variants: dict[str, SettingVariant] = {}
        self._operation_lock = asyncio.Lock()
        self._unsupported: set[str] = set()
        self._optional_index = 0
        self._unavailable_subscriptions: set[Command] = set()
        self._active_subscriptions: set[Command] = set()
        self._subscription_retry: dict[Command, float] = {}
        self._event_refresh_task: asyncio.Task | None = None
        self._calendar_update_pending = False
        self._connect_refresh_pending = False
        self._epos_refresh_pending = False
        self._epos_boundary_change_pending = False
        self._epos_origin_lock = asyncio.Lock()
        self._aim_refresh_pending = False
        self._aim_map_requested_until = 0.0
        self._calendar_time: int | None = None
        self.wire_test: WireTest | None = None
        self._reference_scan: ReferenceScan | None = None
        self._reference_scan_task: asyncio.Task[None] | None = None
        self.reference_candidates: tuple[int, ...] = ()
        self._docking_installation_start_until = 0.0
        self._reset_request_sent = False
        self._operator_logged_in = False
        self._pin_change_unconfirmed = False
        self._wifi_operation_lock = asyncio.Lock()
        self._wifi_event_listener: Callable[[Event], None] | None = None
        self._appdrive_manoeuvre_task: asyncio.Task[None] | None = None
        self._appdrive_control: AppDriveControl | None = None
        self._appdrive_stop_error: tuple[str, str] | None = None
        self._gps_tracking = False
        self._gps_resync_pending = False
        self._gps_report_pending: dict[str, int] | None = None
        self._gps_report_task: asyncio.Task[None] | None = None
        self._map_generation = 0
        self._map_event_payloads: dict[int, bytes] = {}
        self._map_refresh_pending = False

    @callback
    def _async_handle_map_update(self, event: Event) -> None:
        if event.event_id not in MAP_EVENT_KINDS or len(event.payload) != (
            16 if event.event_id == 0 else U32.size
        ):
            return
        if self._map_event_payloads.get(event.event_id) == event.payload:
            return
        self._map_event_payloads[event.event_id] = event.payload
        self._map_refresh_pending = True
        self._async_schedule_event_refresh()

    @callback
    def async_set_gps_tracking(self, enabled: bool) -> None:
        """Enable position notifications for the device tracker."""
        self._gps_tracking = enabled
        self._gps_resync_pending = enabled
        if not enabled:
            self._gps_report_pending = None

    @property
    def appdrive_manoeuvre(self) -> AppDriveManoeuvre | None:
        """Return the appDrive manoeuvre Home Assistant is tracking."""
        data = self.data or {}
        return next(
            (
                manoeuvre
                for manoeuvre in APPDRIVE_MANOEUVRES
                if data.get(manoeuvre.status_key) == AppDriveManoeuvreStatus.IN_PROGRESS
            ),
            None,
        )

    @property
    def appdrive_active(self) -> bool:
        """Whether appDrive reports a session that may be stopped."""
        return (
            self._appdrive_control is not None
            or self.appdrive_manoeuvre is not None
            or (self.data or {}).get("appdrive_state")
            in (
                AppDriveState.IN_OPERATION,
                AppDriveState.PERFORMING_ACTION,
                AppDriveState.PENDING_START,
            )
        )

    @property
    def can_configure_wifi(self) -> bool:
        """Whether the mower reported an available Wi-Fi interface."""
        return self.data is not None and "wifi_enabled" in self.data

    @property
    def can_change_pin(self) -> bool:
        """Whether the firmware and operator session support PIN maintenance."""
        return self.firmware_profile is not None and self._operator_logged_in

    @property
    def can_regenerate_aim_map(self) -> bool:
        """Offer regeneration for a completed map on a known firmware."""
        data = self.data or {}
        return (
            self.firmware_profile is not None
            and self.firmware_profile.aim_map_regeneration == 1
            and "aim_map_regenerate" not in self._unsupported
            and monotonic() >= self._aim_map_requested_until
            and data.get("aim_enabled") == 1
            and data.get("aim_map_state") == AIM_READY
        )

    @property
    def epos_origin_installation_id(self) -> int | None:
        """Return the empty profile eligible for cloud origin setup."""
        site_id = (self.data or {}).get("epos_origin_installation_id")
        if not self._epos_origin_lock.locked() and isinstance(site_id, int):
            return site_id
        return None

    @property
    def can_change_epos_boundary(self) -> bool:
        """Offer perimeter changes only where the app and firmware allow them."""
        profile = self.firmware_profile
        return (
            profile is not None
            and profile.epos_boundary_switch == 1
            and not self._epos_boundary_change_pending
            and "epos_boundary_switch" not in self._unsupported
            and (self.data or {}).get("epos_enabled") in (0, 1)
        )

    @property
    def can_reset_user_settings(self) -> bool:
        """Whether the identified firmware offers a user-settings reset."""
        profile = self.firmware_profile
        return (
            not self._reset_request_sent
            and profile is not None
            and profile.reset_user_settings in (8, 42)
        )

    @property
    def docking_installation_active(self) -> bool:
        """Whether a docking-point procedure is starting or running."""
        return (
            self.data.get("epos_installation_state") == 1
            or self.data.get("epos_docking_start_pending") == 1
        )

    @property
    def reference_scan_active(self) -> bool:
        """Whether the mower is searching for EPOS stations."""
        return self._reference_scan is not None

    @property
    def wire_test_active(self) -> bool:
        """Whether Home Assistant has an outstanding installation test."""
        return self.wire_test is not None and self.wire_test.active

    @property
    def wire_test_distance_saveable(self) -> bool:
        """Whether the last interrupted outbound test recorded a valid distance."""
        return (
            (test := self.wire_test) is not None
            and test.route.outbound
            and test.phase is WireTestPhase.ABORTED
            and test.distance is not None
            and test.route.capability in self.data
        )

    @property
    def message_signal(self) -> str:
        """Dispatcher signal scoped to this mower's entry."""
        return f"{DOMAIN}_message_{self.config_entry.entry_id}"

    @callback
    def _async_on_event(self, event: Event) -> None:
        """Refresh mower state or forward newly reported messages."""
        if event.family == _MESSAGE_EVENTS.family and event.event_id == 0:
            try:
                message = decode_message_event(event.payload)
            except ValueError, StructError:
                LOGGER.debug("Ignoring unexpected mower message event")
                return
            async_dispatcher_send(self.hass, self.message_signal, message)
        elif (
            event.family == REFERENCE_STATION_EVENTS.family
            and (scan := self._reference_scan) is not None
        ):
            scan.handle_event(event)
        elif event.family == WIFI_FAMILY and self._wifi_event_listener is not None:
            self._wifi_event_listener(event)
        elif event.family == MAP_EVENTS.family:
            self._async_handle_map_update(event)
        elif event.family == CONNECT_FAMILY and is_connect_update(event):
            self._connect_refresh_pending = True
            self._async_schedule_event_refresh()
        elif is_epos_update(event):
            self._epos_refresh_pending = True
            self._async_schedule_event_refresh()
        elif is_aim_update(event):
            self._aim_refresh_pending = True
            self._async_schedule_event_refresh()
        elif (
            self._gps_tracking
            and event.family == GPS_POSITION_EVENTS.family
            and event.event_id == GPS_POSITION_REPORT
        ):
            self._async_handle_gps_report(event.payload)
        elif event.family == APPDRIVE_EVENTS.family and (
            (event.event_id == 0 and len(event.payload) == U8.size)
            or (event.event_id in (1, 2) and not event.payload)
        ):
            self._async_schedule_event_refresh()
        elif (
            event.family == _CALENDAR_EVENTS.family
            and event.event_id in (_CALENDAR_TIME_UPDATED, _CALENDAR_TASKS_UPDATED)
            and len(event.payload) == U32.size
        ):
            if event.event_id == _CALENDAR_TIME_UPDATED:
                self._calendar_time = U32.unpack(event.payload)[0]
            self._calendar_update_pending = True
            self._async_schedule_event_refresh()
        elif self.wire_test is not None and self.wire_test.handle_event(event):
            self._async_schedule_event_refresh()
        elif event.family == _MOWER_EVENTS.family and event.event_id in (1, 2, 3):
            self._async_schedule_event_refresh()

    @callback
    def _async_handle_gps_report(self, payload: bytes) -> None:
        try:
            self._gps_report_pending = decode_gps_position(payload)
        except ValueError:
            return
        if self._gps_report_task is None or self._gps_report_task.done():
            self._gps_report_task = self.config_entry.async_create_background_task(
                self.hass,
                self._async_publish_gps_report(),
                "update Automower GPS position",
            )

    async def _async_publish_gps_report(self) -> None:
        async with self._operation_lock:
            report = self._gps_report_pending
            self._gps_report_pending = None
            if (
                report is None
                or not self._gps_tracking
                or not self.client.is_connected()
                or (self.data or {}).get("gps_position_supported") != 1
            ):
                return
            data = {
                key: value
                for key, value in self.data.items()
                if key not in GPS_COORDINATE_KEYS
            }
            data.update(report)
            if data != self.data:
                # Updating the tracker must not postpone the independent core poll.
                self.data = data
                self.async_update_listeners()

    @callback
    def _async_schedule_event_refresh(self) -> None:
        if self.data is not None and (
            self._event_refresh_task is None or self._event_refresh_task.done()
        ):
            self._event_refresh_task = self.config_entry.async_create_background_task(
                self.hass,
                self._async_refresh_after_event(),
                "refresh Automower after BLE event",
            )

    async def _async_refresh_after_event(self) -> None:
        await self.async_request_refresh()
        if (
            self._calendar_update_pending
            or self._map_refresh_pending
            or self._connect_refresh_pending
            or self._epos_refresh_pending
            or self._aim_refresh_pending
        ) and self.client.is_connected():
            # An event received during the read still needs a later snapshot.
            await self.async_request_refresh()

    async def _async_connect(self) -> None:
        if self._pin_change_unconfirmed:
            raise ConfigEntryAuthFailed("The mower's PIN change was unconfirmed")
        if self.client.is_connected():
            return
        if (scan := self._reference_scan) is not None:
            self._async_finish_reference_scan(scan, ScanStatus.FAILED)
        self._gps_report_pending = None
        self._gps_resync_pending = self._gps_tracking
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
        self._active_subscriptions.clear()
        self._map_generation += 1
        self._map_event_payloads.clear()
        self._map_refresh_pending = bool(MAP_PREVIEW_KEYS & (self.data or {}).keys())
        self._connect_refresh_pending = bool(
            CONNECT_STATUS_KEYS & (self.data or {}).keys()
        )
        self._epos_refresh_pending = bool(EPOS_STATUS_KEYS & (self.data or {}).keys())
        self._aim_refresh_pending = bool(AIM_KEYS & (self.data or {}).keys())
        if self.data and "weekly_schedule" in self.data:
            self._calendar_update_pending = True

    async def _async_subscribe_events(self) -> None:
        profile = self.firmware_profile
        commands = (
            (_MOWER_EVENTS, _CALENDAR_EVENTS)
            if profile is not None and not profile.message_events
            else (_MOWER_EVENTS, _CALENDAR_EVENTS, _MESSAGE_EVENTS)
        )
        if self.data and "appdrive_state" in self.data:
            commands += (APPDRIVE_EVENTS,)
        if (
            self._gps_tracking
            and self.data
            and self.data.get("gps_position_supported") == 1
        ):
            commands += (GPS_POSITION_EVENTS,)
        if self.data and MAP_PREVIEW_KEYS & self.data.keys():
            commands += (MAP_EVENTS,)
        if self.data and CONNECT_STATUS_KEYS & self.data.keys():
            commands += CONNECT_EVENTS
        if self.data and "epos_enabled" in self.data:
            commands += (EPOS_EVENTS,)
        if self.data and (
            "epos_cloud_enabled" in self.data
            or "epos_origin_installation_id" in self.data
        ):
            commands += EPOS_CLOUD_EVENTS
        if self.data and AIM_KEYS & self.data.keys():
            commands += (AIM_EVENTS,)
        for command in commands:
            if (
                command in self._unavailable_subscriptions
                or command in self._active_subscriptions
                or self._subscription_retry.get(command, 0) > monotonic()
            ):
                continue
            try:
                await self.client.command(command)
            except CommandRejected as err:
                if err.unsupported:
                    self._unavailable_subscriptions.add(command)
                else:
                    self._subscription_retry[command] = monotonic() + 300
            except MowerConnectionError:
                self._subscription_retry[command] = monotonic() + 300
                return
            else:
                self._active_subscriptions.add(command)

    async def _async_setup(self) -> None:
        guide_count = None
        node_version = None
        serial_number = None
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
                try:
                    guide_count = min(await self._read(Reading(GET_GUIDE_COUNT, U8)), 3)
                except CommandRejected, MowerConnectionError, ValueError:
                    pass
                if self.client.is_connected():
                    try:
                        serial_number = await self._read(
                            Reading(GET_SERIAL_NUMBER, U32)
                        )
                    except CommandRejected, MowerConnectionError, ValueError:
                        pass
                if self.client.is_connected():
                    try:
                        node_version = await self.client.main_node_version()
                    except CommandRejected, MowerConnectionError, UnicodeError:
                        pass
                if self._configured_pin is not None and self.client.is_connected():
                    with suppress(CommandRejected, MowerConnectionError):
                        self._operator_logged_in = (
                            await self.client.command(_IS_OPERATOR_LOGGED_IN) == b"\x01"
                        )
        except (
            MowerConnectionError,
            CommandRejected,
            StructError,
        ) as err:
            raise UpdateFailed("Could not identify the mower") from err
        identity = MowerModels.get(model_id)
        self._map_path_min_width = MAP_PATH_WIDTH_MINIMUMS.get(
            model_id, DEFAULT_MAP_PATH_WIDTH_MINIMUM
        )
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
        if serial_number not in (None, 0, 0xFFFFFFFF):
            self.device_info["serial_number"] = str(serial_number)

    async def _async_update_data(self) -> MowerData:
        try:
            async with self._operation_lock:
                if self._appdrive_control is not None:
                    return self.data
                await self._async_connect()
                battery = await self._read(Reading(GET_BATTERY, U8))
                state = await self._read(Reading(GET_STATE, U8))
                activity = await self._read(Reading(GET_ACTIVITY, U8))
                data = {
                    **(self.data or {}),
                    "battery": battery,
                    "state": state,
                    "activity": activity,
                }
                for key, reading in FAULT_READINGS.items():
                    if key in self._unsupported:
                        continue
                    try:
                        data[key] = await self._read(reading)
                    except CommandRejected as err:
                        if err.unsupported:
                            self._unsupported.add(key)
                        data.pop(key, None)
                    except ValueError:
                        data.pop(key, None)
                        LOGGER.debug("Unexpected response while reading %s", key)
                    except MowerConnectionError:
                        for fault_key in FAULT_READINGS:
                            data.pop(fault_key, None)
                        break
                if (
                    self.client.is_connected()
                    and "planner_override" not in self._unsupported
                ):
                    try:
                        data["planner_override"] = await async_read_planner_override(
                            self.client
                        )
                    except CommandRejected as err:
                        if err.unsupported:
                            self._unsupported.add("planner_override")
                        data.pop("planner_override", None)
                    except ValueError, MowerConnectionError:
                        data.pop("planner_override", None)
                if self.client.is_connected():
                    await self._async_subscribe_events()
                data["message_events"] = int(
                    _MESSAGE_EVENTS in self._active_subscriptions
                )
                if self._calendar_update_pending and self.client.is_connected():
                    await self._async_refresh_calendar(data)
                if self._connect_refresh_pending and self.client.is_connected():
                    self._connect_refresh_pending = False
                    with suppress(CommandRejected, MowerConnectionError):
                        await self._async_refresh_connect_module(data)
                if self._epos_refresh_pending and self.client.is_connected():
                    self._epos_refresh_pending = False
                    with suppress(CommandRejected, MowerConnectionError, ValueError):
                        await self._async_refresh_epos(data)
                if self._aim_refresh_pending and self.client.is_connected():
                    self._aim_refresh_pending = False
                    with suppress(CommandRejected, MowerConnectionError):
                        await self._async_refresh_aim(data)
                if self._gps_resync_pending and self.client.is_connected():
                    self._gps_resync_pending = False
                    with suppress(CommandRejected, MowerConnectionError, ValueError):
                        await self._async_refresh_gps(data)
                if self._map_refresh_pending and self.client.is_connected():
                    self._map_refresh_pending = False
                    with suppress(
                        CommandRejected, MowerConnectionError, ValueError, StructError
                    ):
                        await self._async_refresh_map_previews(data)
                if "appdrive_state" in data and self.client.is_connected():
                    with suppress(CommandRejected, MowerConnectionError):
                        await self._async_refresh_appdrive(data)
                await self._async_refresh_wire_test(data, state, activity)
                if (
                    self.firmware_profile
                    and self.firmware_profile.docking_pattern
                    and "epos_installation_state" in data
                    and self.client.is_connected()
                ):
                    await self._async_refresh_epos_docking(data)
                return data
        except (MowerConnectionError, CommandRejected, ValueError) as err:
            raise UpdateFailed("Could not read the mower status") from err

    async def _read(self, reading: Reading) -> int:
        return reading.decode(
            await self.client.command(reading.command, reading.arguments)
        )

    async def _async_refresh_calendar(self, data: MowerData) -> None:
        self._calendar_update_pending = False
        if self._calendar_time is not None:
            data["mower_clock"] = self._calendar_time
            self._calendar_time = None
        if "weekly_schedule" not in self._unsupported:
            try:
                data["weekly_schedule"] = await async_get_weekly_schedule(
                    self.client, self.hass.config.time_zone
                )
            except CommandRejected as err:
                if err.unsupported:
                    self._unsupported.add("weekly_schedule")
                    data.pop("weekly_schedule", None)
            except ValueError, StructError:
                data.pop("weekly_schedule", None)
            except MowerConnectionError:
                self._calendar_update_pending = True
                return
        if "next_start" in data:
            try:
                data["next_start"] = await self._read(READINGS["next_start"])
            except CommandRejected, MowerConnectionError, ValueError:
                LOGGER.debug("Could not refresh the next start after a calendar event")

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
        probes = [
            (setting.key, setting)
            for setting in self.settings
            if setting.key != CONNECT_ENABLED_KEY
        ]
        setting_keys = {setting.key for setting in self.settings}
        probes.extend(
            (key, reading)
            for key, reading in READINGS.items()
            if key not in setting_keys
        )
        probes.extend(
            (
                ("mower_clock", MOWER_CLOCK),
                ("mower_name", None),
                ("weekly_schedule", None),
                ("statistics", None),
                ("position", None),
                ("local_position", None),
                ("installation_selection", None),
                ("map_previews", None),
                ("vision", None),
                ("epos", None),
                ("aim", None),
                ("wifi", None),
                ("connect_module", None),
                ("appdrive", None),
            )
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
                if not self.client.is_connected() or self._appdrive_control is not None:
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
                        if (
                            key == "mower_clock"
                            and values.get("automatic_clock_sync") == 1
                        ):
                            try:
                                values[key] = await async_sync_mower_clock(
                                    self.client,
                                    self.hass.config.time_zone,
                                    automatic=True,
                                    current=value,
                                )
                            except CommandRejected, ValueError:
                                LOGGER.debug(
                                    "Could not synchronize the mower clock",
                                    exc_info=True,
                                )
                    elif key == "statistics":
                        raw = await self.client.command(GET_STATISTICS)
                        values.update(
                            zip(STATISTIC_KEYS, STATISTICS.unpack(raw), strict=True)
                        )
                    elif key == "mower_name":
                        values[key] = decode_mower_name(
                            await self.client.command(GET_MOWER_NAME)
                        )
                    elif key == "weekly_schedule":
                        values[key] = await async_get_weekly_schedule(
                            self.client, self.hass.config.time_zone
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
                    elif key == "map_previews":
                        await self._async_refresh_map_previews(values)
                    elif key == "vision":
                        vision = await async_read_vision_telemetry(self.client)
                        for status in VISION_STATUS_KEYS.difference(vision):
                            values.pop(status, None)
                        values.update(vision)
                    elif key == "epos":
                        await self._async_refresh_epos(values)
                    elif key == "aim":
                        await self._async_refresh_aim(values)
                    elif key == "wifi":
                        wifi = {}
                        if values.get("wifi_enabled") == 1:
                            wifi = await async_read_wifi_status(self.client)
                        elif values.get("wifi_enabled") == 0:
                            wifi = {"wifi_connected": 0}
                        for status in WIFI_STATUS_KEYS.difference(wifi):
                            values.pop(status, None)
                        values.update(wifi)
                    elif key == "appdrive":
                        await self._async_refresh_appdrive(values)
                    elif key == "connect_module":
                        await self._async_refresh_connect_module(values)
                    elif key == "position":
                        await self._async_refresh_gps(values)
                    elif key == "local_position":
                        for status in LOCAL_POSITION_KEYS:
                            values.pop(status, None)
                        pose = await async_read_local_pose(self.client)
                        if pose is None:
                            self._unsupported.add(key)
                        else:
                            values.update(
                                {
                                    status: pose[status]
                                    for status in LOCAL_POSITION_KEYS & pose.keys()
                                }
                            )
                except CommandRejected as err:
                    if err.unsupported:
                        self._unsupported.add(key)
                        if key in FIRMWARE_TARGETS or key in (
                            "mower_name",
                            "weekly_schedule",
                        ):
                            values.pop(key, None)
                except ValueError, StructError:
                    if key == "weekly_schedule":
                        values.pop(key, None)
                    LOGGER.debug("Unexpected response while reading %s", key)
                except MowerConnectionError:
                    return
                if values != self.data:
                    self.async_set_updated_data(values)

    async def async_keep_alive(self, _now=None) -> None:
        """Keep the link open between updates when no other request is in flight."""
        if (
            self._operation_lock.locked()
            or not self.client.is_connected()
            or self._appdrive_control is not None
        ):
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
        if self.wire_test_active or self.docking_installation_active:
            await self.async_request_refresh()

    async def _async_refresh_epos(self, data: MowerData) -> None:
        profile = self.firmware_profile
        status = await async_read_epos_status(
            self.client,
            docking_pattern=bool(profile and profile.docking_pattern),
            cloud_origin_setup=bool(profile and profile.epos_cloud_origin_setup),
        )
        if profile and profile.epos_cloud_origin_setup:
            with suppress(CommandRejected, StructError):
                if await async_epos_cloud_accepts_origin_setup(self.client):
                    site_id = await async_empty_installation_id(self.client)
                    if site_id is not None:
                        status["epos_origin_installation_id"] = site_id
        for key in EPOS_STATUS_KEYS.difference(status):
            data.pop(key, None)
        data.update(status)

    async def _async_require_empty_epos_profile(self, site_id: int) -> None:
        if (
            self.appdrive_active
            or not await async_epos_cloud_accepts_origin_setup(self.client)
            or await async_empty_installation_id(self.client) != site_id
        ):
            raise ValueError("EPOS setup requires the selected empty installation")

    async def _async_epos_boundary_context(self) -> tuple[int, int | None]:
        await self._async_connect()
        if await self._read(EPOS_AVAILABLE) != 1:
            raise ValueError("The mower does not report EPOS hardware")
        enabled = await self._read(EPOS_ENABLED)
        if enabled not in (0, 1):
            raise ValueError("The mower did not report its perimeter mode")
        return enabled, await async_active_installation_id(self.client)

    async def async_epos_boundary_context(self) -> tuple[int, int | None]:
        """Read the perimeter mode and installation the user will confirm."""
        async with self._operation_lock:
            if not self.can_change_epos_boundary:
                raise HomeAssistantError("This mower does not offer perimeter changes")
            try:
                return await self._async_epos_boundary_context()
            except (
                CommandRejected,
                MowerConnectionError,
                StructError,
                ValueError,
            ) as err:
                raise HomeAssistantError(
                    f"Could not read the perimeter mode: {err}"
                ) from err

    async def async_set_epos_boundary(
        self, enabled: bool, expected: tuple[int, int | None]
    ) -> bool:
        """Send one perimeter change and reload the settings affected by it."""
        async with self._operation_lock:
            if (
                not self.can_change_epos_boundary
                or self._epos_origin_lock.locked()
                or self.appdrive_active
                or self.wire_test_active
                or self.docking_installation_active
                or self.reference_scan_active
            ):
                raise HomeAssistantError("The mower cannot change perimeter mode now")
            try:
                if await self._async_epos_boundary_context() != expected:
                    raise ValueError("The perimeter or active installation changed")
                if enabled == expected[0]:
                    return True
            except (
                CommandRejected,
                MowerConnectionError,
                StructError,
                ValueError,
            ) as err:
                raise HomeAssistantError(
                    f"Could not verify the active installation: {err}"
                ) from err

            self._epos_boundary_change_pending = True
            try:
                try:
                    await self.client.command(SET_EPOS_ENABLED, U8.pack(enabled))
                except CommandRejected as err:
                    self._epos_boundary_change_pending = False
                    if err.unsupported:
                        self._unsupported.add("epos_boundary_switch")
                    raise HomeAssistantError(
                        f"The mower rejected the perimeter change: {err}"
                    ) from err
                return (
                    await self._read(EPOS_ENABLED) == enabled
                    and await async_active_installation_id(self.client) == expected[1]
                )
            except CommandRejected, MowerConnectionError, StructError, ValueError:
                return False
            finally:
                if self._epos_boundary_change_pending:
                    self.hass.config_entries.async_schedule_reload(
                        self.config_entry.entry_id
                    )

    async def async_setup_epos_origin(self, site_id: int) -> bool:
        """Enable direct corrections and establish the origin in an empty installation."""
        if self.epos_origin_installation_id != site_id:
            raise HomeAssistantError("EPOS origin setup is currently unavailable")
        async with self._epos_origin_lock:
            requested = False
            try:
                async with asyncio.timeout(_EPOS_ORIGIN_TIMEOUT):
                    async with self._operation_lock:
                        await self._async_connect()
                        await self._async_require_empty_epos_profile(site_id)
                        await async_enable_epos_cloud(self.client)
                    while True:
                        async with self._operation_lock:
                            try:
                                await self._async_connect()
                                if (
                                    await async_active_installation_id(self.client)
                                    != site_id
                                ):
                                    raise ValueError("The active installation changed")
                                if requested:
                                    if await async_epos_origin_confirmed(self.client):
                                        active = await async_active_installation_id(
                                            self.client
                                        )
                                        if active != site_id:
                                            return False
                                        data = dict(self.data)
                                        data.pop("epos_origin_installation_id", None)
                                        data.update(
                                            epos_cloud_enabled=1,
                                            epos_correction_source=1,
                                            epos_cloud_has_origin=1,
                                            epos_cloud_origin_valid=1,
                                        )
                                        self.async_set_updated_data(data)
                                        return True
                                elif await async_epos_origin_settable(self.client):
                                    await self._async_require_empty_epos_profile(
                                        site_id
                                    )
                                    requested = True
                                    await self.client.command(SET_CLOUD_ORIGIN)
                            except MowerConnectionError:
                                # A lost acknowledgement also uses the read-back path.
                                pass
                        await asyncio.sleep(_EPOS_ORIGIN_POLL_SECONDS)
            except TimeoutError:
                if requested:
                    return False
                raise
            except (
                CommandRejected,
                MowerConnectionError,
                StructError,
                ValueError,
            ) as err:
                if requested and not isinstance(err, CommandRejected):
                    return False
                raise HomeAssistantError(
                    f"Could not configure the EPOS origin: {err}"
                ) from err
            finally:
                self._epos_refresh_pending = True

    async def _async_refresh_aim(self, data: MowerData) -> None:
        status = await async_read_aim_status(self.client)
        revision_changed = data.get("aim_map_updated") != status.get("aim_map_updated")
        if revision_changed:
            self._map_refresh_pending = True
        if revision_changed or status.get("aim_map_state") not in (None, AIM_READY):
            self._aim_map_requested_until = 0.0
        for key in AIM_KEYS.difference(status):
            data.pop(key, None)
        data.update(status)

    async def _async_refresh_epos_docking(self, data: MowerData) -> None:
        try:
            status = await async_read_docking_status(self.client)
        except CommandRejected, MowerConnectionError:
            LOGGER.debug("Could not refresh the EPOS docking installation")
        else:
            if status.get("epos_installation_state") == 1 or status.get(
                "epos_installation_result"
            ) != data.get("epos_installation_result"):
                data.pop("epos_docking_start_pending", None)
            data.update(status)
        if monotonic() >= self._docking_installation_start_until:
            data.pop("epos_docking_start_pending", None)

    async def _async_refresh_appdrive(self, data: MowerData) -> None:
        status = await async_read_appdrive_status(self.client)
        for key in APPDRIVE_STATUS_KEYS.difference(status):
            data.pop(key, None)
        data.update(status)
        if (
            any(
                manoeuvre.availability_key in status
                for manoeuvre in APPDRIVE_MANOEUVRES
            )
            and "in_charging_station" in data
        ):
            with suppress(CommandRejected, ValueError):
                docked = await self._read(READINGS["in_charging_station"])
                if docked in (0, 1):
                    data["in_charging_station"] = docked

    async def _async_refresh_gps(self, data: MowerData) -> None:
        for key in GPS_COORDINATE_KEYS:
            data.pop(key, None)
        gps = await async_read_gps_position(self.client)
        data.pop("gps_position_supported", None)
        data.update(gps)

    async def _async_refresh_connect_module(self, data: MowerData) -> None:
        status = await async_read_connect_module(self.client)
        for key in CONNECT_STATUS_KEYS.difference(status):
            data.pop(key, None)
        data.update(status)
        self.setting_variants.pop(CONNECT_ENABLED_KEY, None)
        if CONNECT_ENABLED_KEY in status:
            for setting in self.settings:
                if setting.key == CONNECT_ENABLED_KEY:
                    self.setting_variants[CONNECT_ENABLED_KEY] = setting.variants[0]
                    break

    @property
    def _legacy_aim_maps(self) -> bool:
        return bool(self.firmware_profile and self.firmware_profile.legacy_aim_maps)

    async def _async_refresh_map_previews(self, data: MowerData) -> None:
        data.pop("poi_parking_available", None)
        for key in MAP_PREVIEW_KEYS:
            data.pop(key, None)
        refresh_interval = int(monotonic() // _MAP_PREVIEW_RECHECK_SECONDS)
        maps = await async_read_map_status(
            self.client, legacy_aim_maps=self._legacy_aim_maps
        )
        for kind, checksum in maps.items():
            events = tuple(
                self._map_event_payloads.get(event_id, b"").hex()
                for event_id, event_kind in MAP_EVENT_KINDS.items()
                if event_kind is kind
            )
            data[kind.preview_key] = ":".join(
                (str(self._map_generation), checksum or str(refresh_interval), *events)
            )
            if kind is MapKind.GENERATED and (updated := data.get("aim_map_updated")):
                data[kind.preview_key] += f":{updated}"
        revision = data.get(MapKind.SITE.preview_key)
        if not isinstance(revision, str) or not (
            self.firmware_profile and self.firmware_profile.park_at_poi
        ):
            self._site_map_snapshot = None
        elif self._site_map_snapshot is None or self._site_map_snapshot[0] != revision:
            self._site_map_snapshot = None
            try:
                site_map = await async_read_map_kind(
                    self.client, MapKind.SITE, check_revision=True
                )
                if (
                    site_map["available"]
                    and site_map.get("checksum", "") == maps[MapKind.SITE]
                ):
                    svg = site_map["svg"]
                    available = await self.hass.async_add_executor_job(
                        has_parking_destination, svg
                    )
                    self._site_map_snapshot = (revision, svg, available)
            except CommandRejected, MowerConnectionError, ValueError, StructError:
                LOGGER.debug("Could not check parking destination in the mower map")
        if self._site_map_snapshot is not None:
            data["poi_parking_available"] = int(self._site_map_snapshot[2])

    async def _async_refresh_wire_test(
        self, data: MowerData, state: int, activity: int
    ) -> None:
        if (test := self.wire_test) is None:
            return
        try:
            await test.async_refresh(self.client, state, activity)
        except CommandRejected, MowerConnectionError, ValueError:
            LOGGER.debug("Could not refresh the wire test", exc_info=True)
        data["wire_test_status"] = test.phase
        if test.distance is not None:
            data["wire_test_distance"] = test.distance

    async def async_start_wire_test(self, route: WireTestRoute) -> None:
        """Prepare and request one supervised wire installation test."""
        async with self._operation_lock:
            profile = self.firmware_profile
            if (
                profile is None
                or not profile.wire_test_states
                or route.capability not in self.data
            ):
                raise HomeAssistantError(
                    "This mower does not offer the selected wire test"
                )
            if self.wire_test_active:
                raise HomeAssistantError("A wire test is already active")
            test = WireTest(route, profile.wire_test_states)
            try:
                await self._async_connect()
                await test.async_prepare(self.client)
                self.wire_test = test
                await test.async_start(self.client)
            except (CommandRejected, MowerConnectionError, ValueError) as err:
                if self.wire_test is test:
                    if isinstance(err, CommandRejected) and test.active:
                        test.phase = WireTestPhase.ERROR
                    self._async_publish_wire_test(test)
                raise HomeAssistantError(
                    f"Could not start the wire test: {err}"
                ) from err
            self._async_publish_wire_test(test)
        await self.async_request_refresh()

    async def async_stop_wire_test(self) -> None:
        """Stop the wire test by its firmware's normal cancellation path."""
        async with self._operation_lock:
            if (test := self.wire_test) is None or not test.active:
                raise HomeAssistantError("There is no active wire test")
            try:
                await self._async_connect()
                await test.async_stop(self.client)
            except (CommandRejected, MowerConnectionError, ValueError) as err:
                raise HomeAssistantError(
                    f"Could not stop the wire test: {err}"
                ) from err
            self._async_publish_wire_test(test)
        await self.async_request_refresh()

    async def async_save_wire_test_distance(self) -> None:
        """Use the recorded distance as the outbound test's starting point."""
        async with self._operation_lock:
            test = self.wire_test
            if (
                not self.wire_test_distance_saveable
                or test is None
                or test.distance is None
            ):
                raise HomeAssistantError(
                    "The last test has no starting-point distance to save"
                )
            setting = next(
                setting
                for setting in self.settings
                if setting.key == test.route.capability
            )
            actual = await self._async_write_setting(setting, test.distance)
            if actual != test.distance:
                raise HomeAssistantError(
                    "The mower did not confirm the recorded distance"
                )

    @callback
    def _async_publish_wire_test(self, test: WireTest) -> None:
        data = {**self.data, "wire_test_status": test.phase}
        if test.distance is None:
            data.pop("wire_test_distance", None)
        else:
            data["wire_test_distance"] = test.distance
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

    async def async_confirm_error(self, expected_code: int) -> None:
        """Acknowledge the same fault only if the mower says it can be confirmed."""
        async with self._operation_lock:
            try:
                await self._async_connect()
                confirmable = await self._read(FAULT_READINGS["error_confirmable"])
                code = await self._read(FAULT_READINGS["error_code"])
                if confirmable != 1 or code == 0 or code != expected_code:
                    raise ValueError(
                        "The mower no longer reports this as a confirmable fault"
                    )
                await self.client.command(CONFIRM_ERROR)
            except (MowerConnectionError, CommandRejected, ValueError) as err:
                raise HomeAssistantError(
                    f"Could not acknowledge the fault: {err}"
                ) from err
        await self.async_request_refresh()

    async def async_sync_clock(self) -> None:
        """Set the mower clock and publish its verified local time."""
        async with self._operation_lock:
            try:
                await self._async_connect()
                actual = await async_sync_mower_clock(
                    self.client, self.hass.config.time_zone
                )
            except (MowerConnectionError, CommandRejected, ValueError) as err:
                raise HomeAssistantError(
                    f"Could not synchronize the mower clock: {err}"
                ) from err
            self.async_set_updated_data({**self.data, "mower_clock": actual})

    async def async_set_mower_name(self, name: str) -> None:
        """Store the requested name on the mower and confirm what it saved."""
        async with self._operation_lock:
            if "mower_name" not in self.data:
                raise HomeAssistantError("This mower does not report an editable name")
            try:
                payload = encode_mower_name(name)
                await self._async_connect()
                actual = decode_mower_name(await self.client.command(GET_MOWER_NAME))
                if actual != name:
                    await self.client.command(SET_MOWER_NAME, payload)
                    actual = decode_mower_name(
                        await self.client.command(GET_MOWER_NAME)
                    )
            except (MowerConnectionError, CommandRejected, ValueError) as err:
                raise HomeAssistantError(f"Could not rename the mower: {err}") from err
            self.async_set_updated_data({**self.data, "mower_name": actual})
            if actual != name:
                raise HomeAssistantError("The mower did not confirm the new name")

    async def async_set_setting(self, setting: Setting, value: int) -> None:
        """Change a supported setting and retrieve the value stored by the mower."""
        async with self._operation_lock:
            actual = await self._async_write_setting(setting, value)
            if setting.key == "wifi_enabled":
                await self._async_update_wifi_telemetry()
            elif setting.key == CONNECT_ENABLED_KEY:
                if actual != value:
                    raise HomeAssistantError(
                        "The mower did not confirm the Automower Connect setting"
                    )
                self._connect_refresh_pending = True
                self._async_schedule_event_refresh()
            elif setting.key == "epos_wire_assistance":
                self._epos_refresh_pending = True
                self._async_schedule_event_refresh()

    async def _async_write_setting(self, setting: Setting, value: int) -> int:
        """Write and read back a setting while the operation lock is held."""
        variant = self.setting_variants.get(setting.key)
        if variant is None:
            raise HomeAssistantError(
                "This mower does not report this setting as available"
            )
        try:
            await self._async_connect()
            payload = setting.encode(variant, value)
            if (
                setting.key == CONNECT_ENABLED_KEY
                and variant.availability is not None
                and await self._read(Reading(variant.availability, U8)) != 1
            ):
                raise ValueError(
                    "The mower does not report an available Automower Connect module"
                )
            if variant.write == SET_LEGACY_BOUNDARY_CORRIDOR:
                minimum_corridor = await self._read(MINIMUM_LEGACY_BOUNDARY_CORRIDOR)
                if value < minimum_corridor:
                    raise ValueError(
                        "The corridor width must be at least its stored minimum"
                    )
                payload = U8.pack(minimum_corridor) + payload[1:]
            if variant.requires_pin:
                pin = self._pin_payload()
                payload = payload + pin if variant.pin_after_value else pin + payload
            if (
                maximum := _SECTOR_MAXIMUM.get(variant.write)
            ) and value > await self._read(Reading(maximum, U16)):
                raise ValueError("The start angle must be at or below the end angle")
            if (
                minimum := _SECTOR_MINIMUM.get(variant.write)
            ) and value < await self._read(Reading(minimum, U16)):
                raise ValueError("The end angle must be at or above the start angle")
            await self.client.command(variant.write, payload)
            actual = await self._read(variant.read)
        except (CommandRejected, MowerConnectionError, ValueError) as err:
            raise HomeAssistantError(f"Could not set {setting.key}: {err}") from err
        self.async_set_updated_data({**self.data, setting.key: actual})
        return actual

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
                if self._appdrive_control is not None:
                    if action != "pause":
                        raise ValueError("Release the appDrive control first")
                    with suppress(HomeAssistantError):
                        await self._async_finish_appdrive_control()
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
                elif action == "park_at_point_of_interest":
                    await self._async_park_at_point_of_interest()
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

    async def _async_park_at_point_of_interest(self) -> None:
        if not (self.firmware_profile and self.firmware_profile.park_at_poi):
            raise ValueError(
                "The mower firmware does not support parking at a point of interest"
            )
        installation_id = await async_active_installation_id(self.client)
        site_map = await async_read_map_kind(
            self.client, MapKind.SITE, check_revision=True
        )
        if not site_map["available"] or not await self.hass.async_add_executor_job(
            has_parking_destination, site_map["svg"]
        ):
            raise ValueError("The active map needs an enabled point-of-interest path")
        if installation_id != await async_active_installation_id(self.client):
            raise ValueError(
                "The active installation changed while reading the parking destination"
            )
        await self.client.command(_SET_MODE, U8.pack(ModeOfOperation.POI))
        if await self._read(READINGS["operation_mode"]) != ModeOfOperation.POI:
            raise ValueError("The mower did not confirm point-of-interest mode")
        await self.client.command(_START)
        self.async_set_updated_data(
            {**self.data, "operation_mode": int(ModeOfOperation.POI)}
        )

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
                tasks = await async_get_schedule(self.client, maximum=limits[0])
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
            if isinstance(schedule := self.data.get("weekly_schedule"), WeeklySchedule):
                self.async_set_updated_data(
                    {
                        **self.data,
                        "weekly_schedule": replace(schedule, tasks=tuple(tasks)),
                    }
                )
        await self.async_request_refresh()

    async def async_edit_schedule(
        self, edit: Callable[[WeeklySchedule], Sequence[ScheduleTask]]
    ) -> None:
        """Apply an event edit to a fresh snapshot while holding the BLE lock."""
        async with self._operation_lock:
            try:
                await self._async_connect()
                current = await async_get_weekly_schedule(
                    self.client, self.hass.config.time_zone
                )
                tasks = tuple(edit(current))
                if tasks != current.tasks:
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
            self.async_set_updated_data(
                {
                    **self.data,
                    "weekly_schedule": replace(current, tasks=tasks),
                }
            )

    async def async_work_area_schedule(
        self,
        feature_id: str,
        expected_checksum: str,
        expected_installation_id: int | None,
        *,
        kind: MapKind,
        intervals: Sequence[ScheduleTask] | None = None,
        expected_schedule_revision: str | None = None,
    ) -> dict:
        """Read or edit one area's intervals for a confirmed installation and mission."""
        async with self._async_map_revision(
            expected_checksum, expected_installation_id, kind=kind
        ):
            if not (self.firmware_profile and self.firmware_profile.mission_schedule):
                raise ValueError(
                    "The mower firmware does not support work area schedules"
                )
            linked = await async_get_linked_work_areas(
                self.client, feature_id, require_available=True
            )
            if len(linked) != 1:
                raise ValueError(
                    "The selected area must have one linked mowing mission"
                )
            mission_id, name = linked[0]

            async def confirm() -> None:
                await self._async_confirm_map_installation(expected_installation_id)
                await async_confirm_schedulable_area(
                    self.client, mission_id, feature_id
                )

            await confirm()
            schedule = await async_get_weekly_schedule(
                self.client, self.hass.config.time_zone
            )
            area_tasks = tuple(
                task for task in schedule.tasks if task.mission_id == mission_id
            )
            revision = work_area_schedule_revision(
                mission_id, schedule.time_zone, area_tasks
            )
            if intervals is not None:
                if revision != expected_schedule_revision:
                    raise ValueError(
                        "The work area schedule changed. Read it again before saving"
                    )
                replacement = tuple(
                    replace(task, mission_id=mission_id) for task in intervals
                )
                if replacement != area_tasks:
                    tasks = tuple(
                        task for task in schedule.tasks if task.mission_id != mission_id
                    )
                    tasks += replacement
                    try:
                        await async_replace_schedule(
                            self.client,
                            tasks,
                            confirm_context=confirm,
                            expected_tasks=schedule.tasks,
                        )
                    except (
                        CommandRejected,
                        MowerConnectionError,
                        ValueError,
                        StructError,
                    ):
                        self._calendar_update_pending = True
                        self._async_schedule_event_refresh()
                        raise
                    schedule = replace(schedule, tasks=tasks)
                    self.async_set_updated_data(
                        {**(self.data or {}), "weekly_schedule": schedule}
                    )
                    area_tasks = replacement
                    revision = work_area_schedule_revision(
                        mission_id, schedule.time_zone, area_tasks
                    )
                else:
                    await confirm()
            else:
                await confirm()
        return {
            "name": name,
            "mission_id": mission_id,
            "revision": revision,
            "time_zone": schedule.time_zone.key,
            "intervals": [
                {**task.as_response(), "duration_seconds": task.duration}
                for task in area_tasks
            ],
        }

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
        if (
            task := self._event_refresh_task
        ) is not None and task is not asyncio.current_task():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        for task in (
            self._reference_scan_task,
            self._appdrive_manoeuvre_task,
            self._gps_report_task,
        ):
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        await super().async_shutdown()
        async with self._operation_lock:
            if self._appdrive_control is not None:
                with suppress(HomeAssistantError):
                    await self._async_finish_appdrive_control()
            if self.reference_scan_active and self.client.is_connected():
                with suppress(CommandRejected, MowerConnectionError):
                    await self.client.command(STOP_REFERENCE_SCAN)
            if self.appdrive_manoeuvre is not None and self.client.is_connected():
                with suppress(CommandRejected, MowerConnectionError):
                    await self.client.command(APPDRIVE_ABORT)
            self._reference_scan = None
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

    async def async_create_work_area(
        self, name: str, area_id: str, expected_checksum: str
    ) -> dict:
        """Create a mowing task for a verified polygon in the active map."""
        return await self._async_call_work_area(
            async_create_work_area, name, area_id, expected_checksum
        )

    async def async_delete_work_area(self, mission_id: int, expected_name: str) -> None:
        """Remove the named mowing task once its schedule is clear."""
        await self._async_call_work_area(
            async_delete_work_area, mission_id, expected_name
        )

    async def async_work_area_status(self, mission_id: int | None) -> dict:
        """Read a selected area's progress or the active area if omitted."""
        return await self._async_call_work_area(async_get_work_area_status, mission_id)

    async def async_clear_work_area_progress(self, mission_id: int) -> None:
        """Reset the selected area's progress tracker."""
        await self._async_call_work_area(async_reset_work_area_progress, mission_id)

    async def _async_call_work_area[T](
        self, operation: Callable[..., Awaitable[T]], *args
    ) -> T:
        async with self._operation_lock:
            try:
                await self._async_connect()
                return await operation(self.client, *args)
            except (
                MowerConnectionError,
                CommandRejected,
                ValueError,
                StructError,
            ) as err:
                raise HomeAssistantError(f"Work area operation failed: {err}") from err

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
            if operation is async_activate_installation:
                self._map_generation += 1
                self._map_event_payloads.clear()
                with suppress(
                    CommandRejected, MowerConnectionError, ValueError, StructError
                ):
                    await self._async_refresh_map_previews(data)
            self.async_set_updated_data(data)
            return result

    async def async_read_map(self, generated: bool) -> dict:
        """Read map geometry from the mower's native BLE attributes."""
        return await self._async_fetch_map(
            MapKind.GENERATED if generated else MapKind.SITE
        )

    async def async_read_zone_map(self) -> dict:
        """Read the mower's separate zone map when available."""
        return await self._async_fetch_map(MapKind.ZONES)

    async def async_read_map_features(self, kind: MapKind) -> dict:
        """Read structured map geometry from a consistent installation snapshot."""
        async with self._operation_lock:
            try:
                await self._async_connect()
                installation_id = await async_active_installation_id(self.client)
                zone_writable = (
                    kind is MapKind.ZONES and await self._async_zone_map_writable()
                )
                zone_work_areas = zone_writable and await self._async_zone_work_areas(
                    kind
                )
                result = await async_read_map_kind(
                    self.client,
                    kind,
                    check_revision=True,
                    allow_empty_zones=zone_writable,
                    legacy_aim_maps=self._legacy_aim_maps,
                )
                checksum = result.get("checksum")
                if not result["available"] and checksum is None:
                    return {"available": False, "map_type": kind.value, "features": []}
                if installation_id != await async_active_installation_id(self.client):
                    raise ValueError(
                        "The active installation changed while reading the map"
                    )
                features = []
                if result["available"]:
                    features = await self.hass.async_add_executor_job(
                        partial(describe_map_features, zone_work_areas=zone_work_areas),
                        result["svg"],
                        kind,
                        zone_writable
                        or (kind is MapKind.SITE and checksum is not None),
                        self._map_path_min_width,
                    )
                can_create = checksum is not None and (
                    zone_writable
                    or (
                        kind is MapKind.SITE
                        and (
                            result["available"] or await async_epos_enabled(self.client)
                        )
                    )
                )
                site_work_areas = False
                if kind is MapKind.SITE and can_create:
                    with suppress(CommandRejected, StructError):
                        site_work_areas = await async_work_areas_available(
                            self.client, allow_legacy=False
                        )
            except (
                MowerConnectionError,
                CommandRejected,
                ValueError,
                StructError,
            ) as err:
                raise HomeAssistantError(
                    f"Could not read the mower map: {err}"
                ) from err
        response = {
            "available": result["available"],
            "map_type": kind.value,
            "features": features,
        }
        if installation_id is not None:
            response["installation_id"] = installation_id
        if checksum is not None:
            response["checksum"] = checksum
            response["can_create"] = can_create
            if zone_writable:
                response["creatable_types"] = (
                    ["work_area", "stay_out_zone"]
                    if zone_work_areas
                    else ["stay_out_zone"]
                )
            elif kind is MapKind.SITE and can_create:
                profile = self.firmware_profile
                creatable = list(CREATABLE_MAP_TYPES)
                if not site_work_areas:
                    creatable.remove("work_area")
                if not (profile and profile.park_at_poi) or any(
                    feature["type"] == "poi_path" for feature in features
                ):
                    creatable.remove("poi_path")
                response["creatable_types"] = creatable
                response["path_width_limits_mm"] = {
                    "min": self._map_path_min_width,
                    "max": MAX_PATH_WIDTH,
                }
        if "work_area" in response.get("creatable_types", ()):
            response["can_schedule_full_day"] = bool(
                self.firmware_profile and self.firmware_profile.mission_schedule
            )
        return response

    async def async_map_preview(self, kind: MapKind) -> bytes | None:
        """Download the mower's map and render it off the event loop."""
        if (
            kind is MapKind.SITE
            and (snapshot := self._site_map_snapshot) is not None
            and snapshot[0] == self.data.get(kind.preview_key)
        ):
            return await self.hass.async_add_executor_job(render_map_png, snapshot[1])
        result = await self._async_fetch_map(kind, check_revision=True)
        if not result["available"]:
            return None
        return await self.hass.async_add_executor_job(render_map_png, result["svg"])

    async def _async_fetch_map(
        self, kind: MapKind, *, check_revision: bool = False
    ) -> dict:
        async with self._operation_lock:
            try:
                await self._async_connect()
                return await async_read_map_kind(
                    self.client,
                    kind,
                    check_revision=check_revision,
                    legacy_aim_maps=self._legacy_aim_maps,
                )
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

    async def async_read_position(self) -> dict:
        """Return fresh GPS and installation-frame positioning as separate values."""
        async with self._operation_lock:
            try:
                await self._async_connect()
                return await async_describe_position(self.client)
            except (CommandRejected, MowerConnectionError, ValueError) as err:
                raise HomeAssistantError(
                    f"Could not read the mower's position: {err}"
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

    async def async_reset_user_settings(self) -> bool:
        """Request the firmware's user reset and report whether it acknowledged it."""
        async with self._operation_lock:
            if not self.can_reset_user_settings or self.firmware_profile is None:
                raise HomeAssistantError("This mower cannot accept a reset request")
            try:
                await self._async_connect()
            except MowerConnectionError as err:
                raise HomeAssistantError("Could not connect to the mower") from err

            operation = (
                _RESET_USER_SETTINGS_AND_RESTART
                if self.firmware_profile.reset_user_settings == 42
                else _RESET_USER_SETTINGS
            )
            try:
                try:
                    response = await self.client.command(operation)
                except CommandRejected as err:
                    if (
                        operation != _RESET_USER_SETTINGS_AND_RESTART
                        or not err.unsupported
                    ):
                        raise
                    response = await self.client.command(_RESET_USER_SETTINGS)
            except CommandRejected as err:
                raise HomeAssistantError(
                    f"The mower rejected the reset request: {err}"
                ) from err
            except MowerConnectionError:
                # A reboot can disconnect BLE before the reply. Read again after reload.
                confirmed = False
            else:
                confirmed = not response
            self._reset_request_sent = True
            self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
            return confirmed

    async def async_change_pin(self, current_pin: str, new_pin: str) -> bool:
        """Change the operator PIN once, then reconnect with the confirmed value."""
        entry = self.config_entry
        async with self._operation_lock:
            if (
                not self.can_change_pin
                or self.appdrive_active
                or entry.data.get(CONF_PIN) != current_pin
            ):
                raise HomeAssistantError("PIN maintenance is unavailable")
            try:
                await self._async_connect()
                operator_logged_in = await self.client.command(_IS_OPERATOR_LOGGED_IN)
            except (CommandRejected, MowerConnectionError) as err:
                raise HomeAssistantError("Could not validate the BLE session") from err
            if operator_logged_in != b"\x01":
                raise HomeAssistantError("The mower requires an operator session")

            payload = U16.pack(int(current_pin)) + U16.pack(int(new_pin))
            try:
                response = await self.client.command(_SET_OPERATOR_PIN, payload)
            except CommandRejected as err:
                if err.unsupported:
                    self._operator_logged_in = False
                raise HomeAssistantError("The mower rejected the PIN change") from err
            except asyncio.CancelledError:
                self._async_require_pin_reauth()
                raise
            except MowerConnectionError:
                self._async_require_pin_reauth()
                return False
            if response:
                self._async_require_pin_reauth()
                await self.client.disconnect()
                return False

            self.hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_PIN: new_pin}
            )
            self.client.pin = int(new_pin)
            self.hass.config_entries.async_schedule_reload(entry.entry_id)
            return True

    async def async_regenerate_aim_map(self) -> bool:
        """Request a new map after confirming that the current map is ready."""
        async with self._operation_lock:
            if not self.can_regenerate_aim_map or self.appdrive_active:
                raise HomeAssistantError("AIM map regeneration is unavailable")
            try:
                await self._async_connect()
                data = dict(self.data)
                await self._async_refresh_aim(data)
                self.async_set_updated_data(data)
                if not self.can_regenerate_aim_map:
                    raise HomeAssistantError("The mower does not have a ready AIM map")
            except (CommandRejected, MowerConnectionError) as err:
                raise HomeAssistantError("Could not read the AIM map state") from err

            self._aim_map_requested_until = (
                monotonic() + _AIM_REGENERATION_RECHECK_SECONDS
            )
            try:
                response = await self.client.command(START_AIM_MAPPING)
            except CommandRejected as err:
                self._aim_map_requested_until = 0.0
                if err.unsupported:
                    self._unsupported.add("aim_map_regenerate")
                raise HomeAssistantError(
                    "The mower rejected AIM map regeneration"
                ) from err
            except MowerConnectionError:
                return False
            finally:
                self._aim_refresh_pending = True
                self._async_schedule_event_refresh()
            return not response

    @callback
    def _async_require_pin_reauth(self) -> None:
        self._pin_change_unconfirmed = True
        self._operator_logged_in = False
        self.config_entry.async_start_reauth(self.hass)

    async def _async_connect_wifi_interface(self, *, enable: bool = False) -> None:
        await self._async_connect()
        if await self._read(WIFI_AVAILABLE) != 1:
            raise ValueError("This mower does not report an available Wi-Fi interface")
        if enable:
            enabled = await self._read(WIFI_ENABLED)
            if enabled not in (0, 1):
                raise ValueError("The mower returned an invalid Wi-Fi setting")
            if not enabled:
                await self.client.command(SET_WIFI_ENABLED, b"\x01")
                if await self._read(WIFI_ENABLED) != 1:
                    raise ValueError("The mower did not enable Wi-Fi")
            if self.data.get("wifi_enabled") != 1:
                self.async_set_updated_data({**self.data, "wifi_enabled": 1})

    async def async_scan_wifi(self) -> tuple[WifiNetwork, ...]:
        """Discover Wi-Fi networks from the mower's location."""
        async with self._wifi_operation_lock:
            scan = WifiScan()
            try:
                async with self._operation_lock:
                    await self._async_connect_wifi_interface(enable=True)
                    self._wifi_event_listener = scan.handle_event
                    await self.client.command(START_WIFI_SCAN)
                async with asyncio.timeout(WIFI_TIMEOUT):
                    await scan.done.wait()
            except (
                CommandRejected,
                MowerConnectionError,
                ValueError,
                TimeoutError,
            ) as err:
                raise HomeAssistantError(
                    "The mower did not complete the Wi-Fi scan"
                ) from err
            finally:
                started = self._wifi_event_listener is not None
                self._wifi_event_listener = None
                if started and not scan.done.is_set():
                    async with self._operation_lock:
                        if self.client.is_connected():
                            with suppress(CommandRejected, MowerConnectionError):
                                await self.client.command(STOP_WIFI_SCAN)
            return tuple(
                sorted(
                    scan.networks.values(),
                    key=lambda network: (-network.signal, network.name),
                )
            )

    async def async_list_saved_wifi(self) -> tuple[str, ...]:
        """Read saved network names for the maintenance form."""
        async with self._wifi_operation_lock, self._operation_lock:
            try:
                await self._async_connect_wifi_interface()
                return await async_read_saved_wifi(self.client)
            except (CommandRejected, MowerConnectionError, ValueError) as err:
                raise HomeAssistantError(
                    "Could not read the mower's saved Wi-Fi networks"
                ) from err

    async def async_join_wifi(self, name: str, password: str | None = None) -> None:
        """Connect using saved credentials or ask the mower to save a new network."""
        async with self._wifi_operation_lock:
            try:
                ssid = encode_ssid(name)
                payload = (
                    ssid if password is None else ssid + encode_wifi_password(password)
                )
                join = WifiJoin(ssid)
                async with self._operation_lock:
                    await self._async_connect_wifi_interface(enable=True)
                    if password is None and name not in await async_read_saved_wifi(
                        self.client
                    ):
                        raise ValueError(
                            "The mower no longer has this saved Wi-Fi network"
                        )
                    await self.client.command(WIFI_STATUS_EVENTS)
                    self._wifi_event_listener = join.handle_event
                    if password is None:
                        await self.client.command(TRY_SAVED_WIFI, payload)
                    else:
                        await self.client.command(TRY_ADD_WIFI, payload)
                async with asyncio.timeout(WIFI_TIMEOUT):
                    await join.done.wait()
                if not join.connected:
                    raise ValueError("The mower could not join the Wi-Fi network")
                async with self._operation_lock:
                    await self._async_update_wifi_telemetry()
            except (
                CommandRejected,
                MowerConnectionError,
                ValueError,
                TimeoutError,
            ) as err:
                raise HomeAssistantError(
                    "The mower did not confirm a Wi-Fi connection"
                ) from err
            finally:
                self._wifi_event_listener = None

    async def async_forget_wifi(self, name: str) -> None:
        """Remove one saved network and confirm it is absent."""
        async with self._wifi_operation_lock, self._operation_lock:
            try:
                ssid = encode_ssid(name)
                await self._async_connect_wifi_interface()
                if name in await async_read_saved_wifi(self.client):
                    await self.client.command(REMOVE_WIFI, ssid)
                    if name in await async_read_saved_wifi(self.client):
                        raise ValueError("The mower retained the saved Wi-Fi network")
                await self._async_update_wifi_telemetry()
            except (CommandRejected, MowerConnectionError, ValueError) as err:
                raise HomeAssistantError(
                    "Could not remove the mower's saved Wi-Fi network"
                ) from err

    async def _async_update_wifi_telemetry(self) -> None:
        with suppress(CommandRejected, MowerConnectionError, ValueError):
            status = (
                await async_read_wifi_status(self.client)
                if self.data.get("wifi_enabled") == 1
                else {"wifi_connected": 0}
            )
            data = {
                key: value
                for key, value in self.data.items()
                if key not in WIFI_STATUS_KEYS
            }
            self.async_set_updated_data({**data, **status})

    async def async_begin_appdrive_control(self, owner: object) -> str:
        """Claim the joystick only after the mower confirms it is ready."""
        async with self._operation_lock:
            if (
                self._appdrive_control is not None
                or self.appdrive_manoeuvre is not None
            ):
                raise HomeAssistantError("appDrive is already in use")
            if self.docking_installation_active or self.wire_test_active:
                raise HomeAssistantError(
                    "Finish the installation manoeuvre before using appDrive"
                )
            try:
                await self._async_connect()
                if await self._read(APPDRIVE_SUPPORTED) != 1:
                    raise ValueError("The mower does not support appDrive")
                if await async_remote_control_available(self.client) not in (None, 1):
                    raise ValueError("Enable remote control on the mower first")
                state = AppDriveState(await self._read(APPDRIVE_STATE))
                mower_state = await self._read(Reading(GET_STATE, U8))
                if state not in (
                    AppDriveState.IDLE,
                    AppDriveState.IN_OPERATION,
                ) or not APPDRIVE_DOCK.allows_start(mower_state, state):
                    raise ValueError("Pause the mower before using appDrive")
                docked = await self._async_appdrive_station_contact(APPDRIVE_DOCK)
                if docked == 1 or (
                    docked is None
                    and await self._read(Reading(GET_ACTIVITY, U8))
                    == MowerActivity.CHARGING
                ):
                    raise ValueError("Undock the mower before using the joystick")
            except (CommandRejected, MowerConnectionError, ValueError) as err:
                raise HomeAssistantError(
                    f"Could not start appDrive control: {err}"
                ) from err
            control = self._appdrive_control = AppDriveControl(owner, uuid4().hex)
            self._async_arm_appdrive_control(
                control, monotonic() + APPDRIVE_INPUT_TIMEOUT
            )
            self.async_update_listeners()
            return control.token

    @callback
    def _async_arm_appdrive_control(
        self, control: AppDriveControl, deadline: float
    ) -> None:
        control.deadline = deadline
        if control.expiry is not None:
            control.expiry.cancel()
        control.expiry = self.hass.loop.call_later(
            max(0, deadline - monotonic()), self._async_expire_appdrive_control, control
        )

    @callback
    def _async_expire_appdrive_control(self, control: AppDriveControl) -> None:
        self.hass.async_create_task(
            self._async_check_appdrive_expiry(control),
            "expire Automower joystick control",
        )

    async def _async_check_appdrive_expiry(self, control: AppDriveControl) -> None:
        async with self._operation_lock:
            if self._appdrive_control is control and control.deadline <= monotonic():
                try:
                    await self._async_finish_appdrive_control()
                except HomeAssistantError as err:
                    LOGGER.debug(
                        "Could not confirm the automatic appDrive stop: %s", err
                    )

    async def async_drive_appdrive(
        self, owner: object, token: str, forward: float, steering: float
    ) -> None:
        """Send fresh joystick input over the original BLE session."""
        deadline = monotonic() + APPDRIVE_INPUT_TIMEOUT
        payload = drive_payload(forward, steering)
        async with self._operation_lock:
            control = self._appdrive_control
            if control is None or control.owner is not owner or control.token != token:
                raise HomeAssistantError("The appDrive control has ended")
            remaining = min(deadline, control.deadline) - monotonic()
            if remaining <= 0 or not self.client.is_connected():
                with suppress(HomeAssistantError):
                    await self._async_finish_appdrive_control()
                raise HomeAssistantError("The appDrive control has expired")
            self._async_arm_appdrive_control(control, deadline)
            control.sent_input = True
            try:
                await self.client.command(APPDRIVE_DRIVE, payload, timeout=remaining)
            except (CommandRejected, MowerConnectionError) as err:
                with suppress(HomeAssistantError):
                    await self._async_finish_appdrive_control()
                raise HomeAssistantError(
                    f"appDrive movement could not be confirmed: {err}"
                ) from err

    async def async_release_appdrive_control(self, owner: object, token: str) -> None:
        """Stop the joystick session owned by this Home Assistant connection."""
        async with self._operation_lock:
            control = self._appdrive_control
            if (
                control is not None
                and control.owner is owner
                and control.token == token
            ):
                await self._async_finish_appdrive_control()
            elif self._appdrive_stop_error and self._appdrive_stop_error[0] == token:
                raise HomeAssistantError(self._appdrive_stop_error[1])

    async def _async_finish_appdrive_control(self) -> None:
        control, self._appdrive_control = self._appdrive_control, None
        if control is None:
            return
        if control.expiry is not None:
            control.expiry.cancel()
        try:
            if control.sent_input:
                if not self.client.is_connected():
                    raise MowerConnectionError("Bluetooth disconnected")
                try:
                    await self.client.command(
                        APPDRIVE_DRIVE,
                        drive_payload(0, 0),
                        timeout=APPDRIVE_INPUT_TIMEOUT,
                    )
                except CommandRejected:
                    await self.client.command(
                        APPDRIVE_ABORT, timeout=APPDRIVE_INPUT_TIMEOUT
                    )
        except (CommandRejected, MowerConnectionError) as err:
            message = f"The appDrive stop was not confirmed: {err}"
            self._appdrive_stop_error = (control.token, message)
            raise HomeAssistantError(message) from err
        finally:
            self.async_update_listeners()

    async def async_start_appdrive_manoeuvre(
        self, manoeuvre: AppDriveManoeuvre
    ) -> None:
        """Request an eligible firmware-controlled appDrive manoeuvre."""
        async with self._operation_lock:
            if self._appdrive_control is not None:
                raise HomeAssistantError("Release the appDrive control first")
            if self.appdrive_manoeuvre is not None:
                raise HomeAssistantError("An appDrive manoeuvre is already in progress")
            try:
                await self._async_connect()
                if await self._read(APPDRIVE_SUPPORTED) != 1:
                    raise ValueError("The mower does not support appDrive")
                if await async_remote_control_available(self.client) not in (None, 1):
                    raise ValueError("Enable remote control on the mower first")
                if self.docking_installation_active:
                    raise ValueError(
                        "Finish the EPOS docking-point setup before using appDrive"
                    )
                mower_state = await self._read(Reading(GET_STATE, U8))
                state = AppDriveState(await self._read(APPDRIVE_STATE))
                if not manoeuvre.allows_start(mower_state, state):
                    raise ValueError(
                        f"Pause the mower before requesting appDrive {manoeuvre.name}"
                    )
                docked = await self._async_appdrive_station_contact(manoeuvre)
                if docked == int(manoeuvre.ends_in_station) or (
                    docked is None
                    and await self._read(Reading(GET_ACTIVITY, U8))
                    == MowerActivity.CHARGING
                ):
                    if manoeuvre.ends_in_station:
                        raise ValueError("The mower is already in its charging station")
                    raise ValueError("The mower must be in its charging station")
                if await self._read(manoeuvre.possible) != 1:
                    raise ValueError(
                        f"The mower does not currently permit appDrive {manoeuvre.name}"
                    )
                if state in (
                    AppDriveState.PERFORMING_ACTION,
                    AppDriveState.ERROR,
                    AppDriveState.PENDING_START,
                ):
                    raise ValueError(
                        "The mower's appDrive state does not permit this manoeuvre"
                    )
                if "epos_installation_state" in self.data:
                    epos = await async_read_docking_status(self.client)
                    if epos.get("epos_installation_state") != 0:
                        raise ValueError(
                            "Finish the EPOS docking-point setup before using appDrive"
                        )
                await self.client.command(manoeuvre.command)
            except (CommandRejected, MowerConnectionError, ValueError) as err:
                raise HomeAssistantError(
                    f"Could not {manoeuvre.name} using appDrive: {err}"
                ) from err
            self.async_set_updated_data(
                {
                    **self.data,
                    manoeuvre.status_key: AppDriveManoeuvreStatus.IN_PROGRESS,
                }
            )
            self._appdrive_manoeuvre_task = (
                self.config_entry.async_create_background_task(
                    self.hass,
                    self._async_track_appdrive_manoeuvre(manoeuvre),
                    f"track the Automower appDrive {manoeuvre.name}",
                )
            )

    async def _async_appdrive_station_contact(
        self, manoeuvre: AppDriveManoeuvre
    ) -> int | None:
        try:
            docked = await self._read(READINGS["in_charging_station"])
        except CommandRejected as err:
            if manoeuvre.ends_in_station and err.unsupported:
                return None
            raise
        if docked not in (0, 1):
            raise ValueError("The mower returned an invalid charging station state")
        return docked

    async def _async_track_appdrive_manoeuvre(
        self, manoeuvre: AppDriveManoeuvre
    ) -> None:
        try:
            async with asyncio.timeout(APPDRIVE_MANOEUVRE_TIMEOUT):
                while True:
                    async with self._operation_lock:
                        if not self.client.is_connected():
                            raise MowerConnectionError(
                                "Bluetooth disconnected during the appDrive manoeuvre"
                            )
                        state = AppDriveState(await self._read(APPDRIVE_STATE))
                        docked = await self._async_appdrive_station_contact(manoeuvre)
                        status = AppDriveManoeuvreStatus.IN_PROGRESS
                        if state is AppDriveState.ERROR:
                            status = AppDriveManoeuvreStatus.FAILED
                        elif state in (
                            AppDriveState.IDLE,
                            AppDriveState.IN_OPERATION,
                        ):
                            if docked is None:
                                reached = (
                                    await self._read(Reading(GET_ACTIVITY, U8))
                                    == MowerActivity.CHARGING
                                )
                            else:
                                reached = docked == int(manoeuvre.ends_in_station)
                            if reached:
                                status = AppDriveManoeuvreStatus.COMPLETE
                        data = {
                            **self.data,
                            "appdrive_state": state,
                            manoeuvre.status_key: status,
                        }
                        if docked is not None:
                            data["in_charging_station"] = docked
                        if data != self.data:
                            self.async_set_updated_data(data)
                        if status is not AppDriveManoeuvreStatus.IN_PROGRESS:
                            return
                    await asyncio.sleep(1)
        except CommandRejected, MowerConnectionError, TimeoutError, ValueError:
            async with self._operation_lock:
                if self.appdrive_manoeuvre is manoeuvre:
                    self.async_set_updated_data(
                        {
                            **self.data,
                            manoeuvre.status_key: AppDriveManoeuvreStatus.UNCONFIRMED,
                        }
                    )

    async def async_stop_appdrive(self) -> None:
        """Send the firmware's abort command for an active appDrive session."""
        async with self._operation_lock:
            try:
                if self._appdrive_control is not None:
                    await self._async_finish_appdrive_control()
                    return
                await self._async_connect()
                if await self._read(APPDRIVE_SUPPORTED) != 1:
                    raise ValueError("The mower does not support appDrive")
                manoeuvre = self.appdrive_manoeuvre
                if manoeuvre is None and await self._read(APPDRIVE_STATE) not in (
                    AppDriveState.IN_OPERATION,
                    AppDriveState.PERFORMING_ACTION,
                    AppDriveState.PENDING_START,
                ):
                    return
                await self.client.command(APPDRIVE_ABORT)
            except (CommandRejected, MowerConnectionError, ValueError) as err:
                raise HomeAssistantError(f"Could not stop appDrive: {err}") from err
            if (
                manoeuvre is not None
                and (task := self._appdrive_manoeuvre_task) is not None
            ):
                task.cancel()
                self.async_set_updated_data(
                    {
                        **self.data,
                        manoeuvre.status_key: AppDriveManoeuvreStatus.STOP_REQUESTED,
                    }
                )
        await self.async_request_refresh()

    async def async_set_epos_docking_method(self, by_wire: bool) -> None:
        """Set the docking guidance method while the mower is in the station."""
        async with self._operation_lock:
            if (
                self.appdrive_active
                or self.wire_test_active
                or self.docking_installation_active
            ):
                raise HomeAssistantError(
                    "Finish the current manoeuvre before changing the docking method"
                )
            try:
                await self._async_connect()
                try:
                    docked = await self._read(READINGS["in_charging_station"]) == 1
                except CommandRejected as err:
                    if not err.unsupported:
                        raise
                    docked = (
                        await self._read(Reading(GET_ACTIVITY, U8))
                        == MowerActivity.CHARGING
                    )
                if not docked:
                    raise ValueError("Place the mower in the charging station first")
                if (await async_read_docking_status(self.client)).get(
                    "epos_installation_state"
                ) == 1:
                    raise ValueError("Finish the EPOS docking-point setup first")
                actual = await async_set_dock_by_wire(self.client, by_wire)
            except (CommandRejected, MowerConnectionError, ValueError) as err:
                raise HomeAssistantError(
                    f"Could not set the EPOS docking method: {err}"
                ) from err
            self.async_set_updated_data({**self.data, "epos_dock_by_wire": actual})

    async def async_start_epos_docking_installation(self) -> None:
        """Ask the docked, paused mower to establish its EPOS docking point."""
        async with self._operation_lock:
            profile = self.firmware_profile
            if not profile or not profile.docking_pattern:
                raise HomeAssistantError(
                    "This firmware does not offer EPOS docking installation"
                )
            if self.data.get("epos_docking_start_pending"):
                raise HomeAssistantError("The EPOS docking installation is starting")
            if self.appdrive_manoeuvre is not None:
                raise HomeAssistantError(
                    "Finish the appDrive manoeuvre before starting EPOS docking installation"
                )
            try:
                await self._async_connect()
                epos = await async_read_docking_status(self.client, check_epos=True)
                if "epos_installation_result" not in epos:
                    raise ValueError(
                        "The mower does not report an EPOS docking installation"
                    )
                if epos.get("epos_installation_state") != 0:
                    raise ValueError(
                        "The mower must report that EPOS docking installation is idle"
                    )
                try:
                    docked = await self._read(READINGS["in_charging_station"]) == 1
                except CommandRejected as err:
                    if not err.unsupported:
                        raise
                    docked = await self._read(Reading(GET_ACTIVITY, U8)) in (
                        MowerActivity.PARKED,
                        MowerActivity.CHARGING,
                    )
                if not docked:
                    raise ValueError("Place the mower in the charging station first")
                state = await self._read(Reading(GET_STATE, U8))
                if state != MowerState.PAUSED:
                    await self.client.command(_PAUSE)
                    if await self._read(Reading(GET_STATE, U8)) != MowerState.PAUSED:
                        raise ValueError(
                            "Wait until the mower reports it is paused and retry"
                        )
                await self.client.command(RUN_DOCKING_PATTERN)
            except (CommandRejected, MowerConnectionError, ValueError) as err:
                raise HomeAssistantError(
                    f"Could not start EPOS docking installation: {err}"
                ) from err
            self._docking_installation_start_until = (
                monotonic() + _DOCKING_INSTALLATION_START_GRACE
            )
            self.async_set_updated_data({**self.data, "epos_docking_start_pending": 1})
        await self.async_request_refresh()

    async def async_stop_epos_docking_installation(self) -> None:
        """Cancel the firmware's current docking-point procedure."""
        async with self._operation_lock:
            if (
                not self.firmware_profile
                or not self.firmware_profile.docking_pattern_stop
            ):
                raise HomeAssistantError(
                    "This firmware does not offer EPOS docking cancellation"
                )
            try:
                await self._async_connect()
                status = await async_read_docking_status(self.client)
                if status.get("epos_installation_state") == 1 or self.data.get(
                    "epos_docking_start_pending"
                ):
                    await self.client.command(STOP_DOCKING_PATTERN)
                    self._docking_installation_start_until = 0.0
            except (CommandRejected, MowerConnectionError) as err:
                raise HomeAssistantError(
                    f"Could not stop EPOS docking installation: {err}"
                ) from err
        await self.async_request_refresh()

    async def async_scan_reference_stations(self) -> None:
        """Start the firmware's scan and collect its discoveries over BLE."""
        async with self._operation_lock:
            if self.reference_scan_active:
                raise HomeAssistantError(
                    "The mower is already scanning for reference stations"
                )
            scan = ReferenceScan()
            try:
                await self._async_connect()
                station_id = await async_get_reference_station_id(self.client)
                if station_id is None:
                    raise ValueError(
                        "This mower does not report an EPOS reference station interface"
                    )
                self._reference_scan = scan
                await async_start_reference_scan(self.client)
            except asyncio.CancelledError:
                if self._reference_scan is scan:
                    self._async_finish_reference_scan(scan, ScanStatus.FAILED)
                    with suppress(CommandRejected, MowerConnectionError):
                        await self.client.command(STOP_REFERENCE_SCAN)
                raise
            except (CommandRejected, MowerConnectionError, ValueError) as err:
                if self._reference_scan is scan:
                    self._async_finish_reference_scan(scan, ScanStatus.FAILED)
                raise HomeAssistantError(
                    f"Could not scan for reference stations: {err}"
                ) from err
            self.reference_candidates = ()
            self.async_set_updated_data(
                {
                    **self.data,
                    "epos_reference_station_id": station_id,
                    "epos_reference_scan_status": ScanStatus.SCANNING,
                }
            )
            self._reference_scan_task = self.config_entry.async_create_background_task(
                self.hass,
                self._async_wait_for_reference_scan(scan),
                "receive EPOS reference station scan results",
            )

    async def _async_wait_for_reference_scan(self, scan: ReferenceScan) -> None:
        try:
            async with asyncio.timeout(REFERENCE_SCAN_TIMEOUT):
                await scan.done.wait()
        except TimeoutError:
            async with self._operation_lock:
                if self._reference_scan is scan:
                    if self.client.is_connected():
                        with suppress(CommandRejected, MowerConnectionError):
                            await self.client.command(STOP_REFERENCE_SCAN)
                    self._async_finish_reference_scan(scan, ScanStatus.FAILED)
        else:
            async with self._operation_lock:
                if self._reference_scan is scan:
                    self._async_finish_reference_scan(scan, ScanStatus.COMPLETE)

    @callback
    def _async_finish_reference_scan(
        self, scan: ReferenceScan, status: ScanStatus
    ) -> None:
        self._reference_scan = None
        self.reference_candidates = (
            () if status is ScanStatus.FAILED else tuple(sorted(scan.station_ids))
        )
        scan.done.set()
        self.async_set_updated_data(
            {**(self.data or {}), "epos_reference_scan_status": status}
        )

    async def async_stop_reference_scan(self) -> None:
        """Stop scanning and retain the stations already found."""
        async with self._operation_lock:
            if (scan := self._reference_scan) is None:
                return
            if not self.client.is_connected():
                self._async_finish_reference_scan(scan, ScanStatus.FAILED)
                raise HomeAssistantError(
                    "The mower disconnected during the reference station scan"
                )
            try:
                await self.client.command(STOP_REFERENCE_SCAN)
            except (CommandRejected, MowerConnectionError) as err:
                if not self.client.is_connected():
                    self._async_finish_reference_scan(scan, ScanStatus.FAILED)
                raise HomeAssistantError(
                    f"Could not stop the reference station scan: {err}"
                ) from err
            self._async_finish_reference_scan(scan, ScanStatus.STOPPED)

    async def async_select_reference_station(self, station_id: int) -> None:
        """Pair the mower with an EPOS station returned by the last scan."""
        async with self._operation_lock:
            if self.reference_scan_active:
                raise HomeAssistantError(
                    "Wait for the reference station scan to finish"
                )
            try:
                await self._async_connect()
                installed = await async_get_reference_station_id(self.client)
                if installed is None:
                    raise ValueError(
                        "This mower does not report an EPOS reference station interface"
                    )
                if station_id not in self.reference_candidates:
                    if station_id == installed:
                        return
                    raise ValueError(
                        "Choose a reference station from the most recent scan"
                    )
                await async_pair_reference_station(self.client, station_id)
            except (CommandRejected, MowerConnectionError, ValueError) as err:
                # Setting the ID and setting the station position are separate operations.
                if self.client.is_connected():
                    with suppress(CommandRejected, MowerConnectionError, ValueError):
                        actual = await async_get_reference_station_id(self.client)
                        if actual is not None:
                            self.async_set_updated_data(
                                {
                                    **self.data,
                                    "epos_reference_station_id": actual,
                                }
                            )
                raise HomeAssistantError(
                    f"Could not pair the EPOS reference station: {err}"
                ) from err
            self.async_set_updated_data(
                {
                    **self.data,
                    "epos_reference_station_id": station_id,
                }
            )

    async def async_write_map(self, svg: str, expected_checksum: str) -> dict:
        """Replace the prior map revision confirmed by the user."""
        async with self._operation_lock:
            try:
                await self._async_connect()
                return await self._async_publish_map(svg, expected_checksum)
            except (
                MowerConnectionError,
                CommandRejected,
                ValueError,
                StructError,
            ) as err:
                raise HomeAssistantError(
                    f"Could not update the mower map: {err}"
                ) from err

    async def async_edit_map_geometry(
        self,
        feature_id: str,
        expected_checksum: str,
        points: list[dict[str, int]],
        expected_installation_id: int | None,
        *,
        kind: MapKind = MapKind.SITE,
    ) -> dict:
        """Replace one feature using the map revision and profile returned by the read."""
        return await self._async_change_map_feature(
            feature_id,
            expected_checksum,
            expected_installation_id,
            update_map_geometry,
            points,
            kind=kind,
        )

    async def async_edit_map_feature_settings(
        self,
        feature_id: str,
        expected_checksum: str,
        expected_installation_id: int | None,
        name: str | None,
        enabled: bool | None,
        width_mm: int | None,
        *,
        kind: MapKind = MapKind.SITE,
    ) -> dict:
        """Save settings for an existing area or path in the active map."""
        return await self._async_change_map_feature(
            feature_id,
            expected_checksum,
            expected_installation_id,
            update_map_feature_settings,
            name,
            enabled,
            width_mm,
            self._map_path_min_width,
            kind=kind,
        )

    async def _async_change_map_feature(
        self,
        feature_id: str,
        expected_checksum: str,
        expected_installation_id: int | None,
        change: Callable[..., str],
        *args,
        kind: MapKind = MapKind.SITE,
    ) -> dict:
        async with self._async_map_revision(
            expected_checksum, expected_installation_id, kind=kind
        ) as old_svg:
            zone_work_areas = await self._async_zone_work_areas(kind)
            svg = await self.hass.async_add_executor_job(
                partial(change, kind=kind, zone_work_areas=zone_work_areas),
                old_svg,
                feature_id,
                *args,
            )
            await self._async_confirm_map_installation(expected_installation_id)
            if svg == old_svg:
                return {"checksum": expected_checksum.lower(), "feature_id": feature_id}
            result = await self._async_publish_map(
                svg,
                expected_checksum,
                kind=kind,
                expected_installation_id=expected_installation_id,
            )
            return {**result, "feature_id": feature_id}

    async def async_create_map_feature(
        self,
        feature_type: str,
        name: str,
        points: list[dict[str, int]],
        expected_checksum: str,
        expected_installation_id: int | None,
        width_mm: int | None,
        *,
        kind: MapKind = MapKind.SITE,
        schedule_full_day: bool = False,
    ) -> dict:
        """Add geometry and link a mowing mission when adding a work area."""
        async with self._async_map_revision(
            expected_checksum,
            expected_installation_id,
            kind=kind,
            allow_unavailable=True,
        ) as old_svg:
            if schedule_full_day and (
                feature_type != "work_area"
                or not (
                    self.firmware_profile and self.firmware_profile.mission_schedule
                )
            ):
                raise ValueError(
                    "This feature does not support a full-day mowing schedule"
                )
            feature_id = str(uuid4())
            zone_work_areas = await self._async_zone_work_areas(kind)
            svg = await self.hass.async_add_executor_job(
                partial(add_map_feature, kind=kind, zone_work_areas=zone_work_areas),
                old_svg,
                feature_id,
                feature_type,
                name,
                points,
                width_mm,
                self._map_path_min_width,
            )
            missions_before = None
            if feature_type == "work_area":
                missions_before = await async_require_work_area_capacity(
                    self.client, name
                )
            await self._async_confirm_map_installation(expected_installation_id)
            result = await self._async_publish_map(
                svg,
                expected_checksum,
                kind=kind,
                expected_installation_id=expected_installation_id,
            )
            result["feature_id"] = feature_id
            if missions_before is not None:
                try:
                    await self._async_confirm_map_installation(expected_installation_id)
                    result.update(
                        await async_create_work_area(
                            self.client,
                            name,
                            feature_id,
                            result["checksum"],
                            kind=kind,
                            confirm_context=partial(
                                self._async_confirm_map_installation,
                                expected_installation_id,
                            ),
                        )
                    )
                except (
                    MowerConnectionError,
                    CommandRejected,
                    ValueError,
                    StructError,
                ) as err:
                    reverted = False
                    with suppress(
                        MowerConnectionError, CommandRejected, ValueError, StructError
                    ):
                        await self._async_connect()
                        await self._async_confirm_map_installation(
                            expected_installation_id
                        )
                        if missions_before == await async_mission_ids(
                            self.client
                        ) and not await async_get_linked_work_areas(
                            self.client,
                            feature_id,
                            require_available=kind is MapKind.ZONES,
                        ):
                            await self._async_confirm_map_installation(
                                expected_installation_id
                            )
                            await self._async_publish_map(
                                old_svg,
                                result["checksum"],
                                allow_empty=True,
                                kind=kind,
                                expected_installation_id=expected_installation_id,
                            )
                            reverted = True
                    if reverted:
                        raise ValueError(
                            f"The polygon was reverted after mission creation failed: {err}"
                        ) from err
                    raise ValueError(
                        f"Check polygon {feature_id} and its mission after the interrupted creation: {err}"
                    ) from err
                if schedule_full_day:
                    try:
                        await self._async_schedule_new_work_area(
                            result["mission_id"], expected_installation_id
                        )
                    except (
                        CommandRejected,
                        MowerConnectionError,
                        ValueError,
                        StructError,
                    ) as err:
                        # The area was already confirmed. The schedule commit may have succeeded.
                        self._calendar_update_pending = True
                        self._async_schedule_event_refresh()
                        result.update(schedule_confirmed=False, schedule_error=str(err))
                    else:
                        result["schedule_confirmed"] = True
            return result

    async def _async_schedule_new_work_area(
        self, mission_id: int, installation_id: int | None
    ) -> None:
        """Append an all-day interval to a fresh schedule under the map lock."""
        confirm = partial(self._async_confirm_map_installation, installation_id)
        await confirm()
        current = await async_get_weekly_schedule(
            self.client, self.hass.config.time_zone
        )
        interval = ScheduleTask.full_week(mission_id)
        tasks = current.tasks
        if interval not in tasks:
            tasks = (*tasks, interval)
            await async_replace_schedule(
                self.client,
                tasks,
                confirm_context=confirm,
                expected_tasks=current.tasks,
            )
        else:
            await confirm()
        self.async_set_updated_data(
            {**(self.data or {}), "weekly_schedule": replace(current, tasks=tasks)}
        )

    async def async_delete_map_feature(
        self,
        feature_id: str,
        expected_checksum: str,
        expected_installation_id: int | None,
        *,
        kind: MapKind = MapKind.SITE,
    ) -> dict:
        """Remove one polygon or path, preserving scheduled mowing missions."""
        async with self._async_map_revision(
            expected_checksum, expected_installation_id, kind=kind
        ) as old_svg:
            zone_work_areas = await self._async_zone_work_areas(kind)
            svg, feature_type = await self.hass.async_add_executor_job(
                partial(remove_map_feature, kind=kind, zone_work_areas=zone_work_areas),
                old_svg,
                feature_id,
            )
            linked = []
            if feature_type == "work_area":
                linked = await async_get_linked_work_areas(
                    self.client, feature_id, require_available=kind is MapKind.ZONES
                )
            if len(linked) > 1:
                raise ValueError(
                    "Multiple mowing missions reference this polygon. Delete those missions before removing it"
                )
            await self._async_confirm_map_installation(expected_installation_id)
            if linked:
                mission_id, name = linked[0]
                await async_verify_mowing_area(
                    self.client, feature_id, expected_checksum, kind=kind
                )
                await self._async_confirm_map_installation(expected_installation_id)
                await async_delete_work_area(
                    self.client,
                    mission_id,
                    name,
                    confirm_context=partial(
                        self._async_confirm_map_installation, expected_installation_id
                    ),
                )
            try:
                await self._async_confirm_map_installation(expected_installation_id)
                result = await self._async_publish_map(
                    svg,
                    expected_checksum,
                    allow_empty=True,
                    kind=kind,
                    expected_installation_id=expected_installation_id,
                )
            except (
                MowerConnectionError,
                CommandRejected,
                ValueError,
                StructError,
            ) as err:
                if not linked:
                    raise
                raise ValueError(
                    f"Mission {mission_id} was removed. Check polygon {feature_id} before retrying: {err}"
                ) from err
            return {**result, "feature_id": feature_id}

    async def _async_confirm_map_installation(
        self, expected_installation_id: int | None
    ) -> None:
        if expected_installation_id != await async_active_installation_id(self.client):
            raise ValueError("Read the map again and pass its current installation ID")

    async def _async_zone_map_writable(self) -> bool:
        profile = self.firmware_profile
        if not (profile and profile.zone_map_writable):
            return False
        if await async_epos_enabled(self.client):
            return False
        return await async_aim_map_ready(self.client)

    async def _async_zone_work_areas(self, kind: MapKind) -> bool:
        profile = self.firmware_profile
        if kind is not MapKind.ZONES or not (profile and profile.aim_work_areas):
            return False
        try:
            return await async_work_areas_available(self.client, allow_legacy=False)
        except CommandRejected, StructError:
            return False

    async def _async_confirm_zone_map(
        self, expected_installation_id: int | None
    ) -> None:
        if not await self._async_zone_map_writable():
            raise ValueError("A ready AIM map is required to edit its zones")
        await self._async_confirm_map_installation(expected_installation_id)

    @asynccontextmanager
    async def _async_map_revision(
        self,
        expected_checksum: str,
        expected_installation_id: int | None,
        *,
        kind: MapKind = MapKind.SITE,
        allow_unavailable: bool = False,
    ) -> AsyncIterator[str]:
        async with self._operation_lock:
            try:
                await self._async_connect()
                if kind is MapKind.GENERATED:
                    raise ValueError("The generated map does not support editing")
                if kind is MapKind.ZONES:
                    await self._async_confirm_zone_map(expected_installation_id)
                else:
                    await self._async_confirm_map_installation(expected_installation_id)
                current = await async_read_map_kind(
                    self.client,
                    kind,
                    check_revision=True,
                    allow_empty_zones=kind is MapKind.ZONES and allow_unavailable,
                )
                if "checksum" not in current or (
                    not allow_unavailable and not current["available"]
                ):
                    raise ValueError(
                        "The mower does not report a revision for its active map"
                    )
                if expected_checksum.lower() != current["checksum"]:
                    raise ValueError(
                        "The active map changed. Read it again before updating"
                    )
                if (
                    kind is MapKind.SITE
                    and not current["available"]
                    and not await async_epos_enabled(self.client)
                ):
                    raise ValueError(
                        "The mower does not report an active map or enabled EPOS installation"
                    )
                yield current.get("svg", "<svg/>")
            except (
                MowerConnectionError,
                CommandRejected,
                ValueError,
                StructError,
            ) as err:
                raise HomeAssistantError(
                    f"Could not update the mower map: {err}"
                ) from err

    async def _async_publish_map(
        self,
        svg: str,
        expected_checksum: str,
        *,
        allow_empty: bool = False,
        kind: MapKind = MapKind.SITE,
        expected_installation_id: int | None = None,
    ) -> dict:
        if kind is MapKind.ZONES:
            result = await async_replace_zone_map(
                self.client,
                svg,
                expected_checksum,
                partial(self._async_confirm_zone_map, expected_installation_id),
            )
            self._map_generation += 1
        else:
            result = await async_replace_map(
                self.client, svg, expected_checksum, allow_empty=allow_empty
            )
        data = dict(self.data)
        with suppress(CommandRejected, MowerConnectionError, ValueError, StructError):
            await self._async_refresh_map_previews(data)
        if data != self.data:
            self.async_set_updated_data(data)
        return result
