"""One-shot schedule and parking commands."""

from automower_ble.protocol import MowerActivity
from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import MowerCoordinator, MowerEntry
from .entity import MowerEntity, MowerValueEntity, async_add_detected
from .fota import FIRMWARE_TARGETS, UpdatePhase
from .wire_test import WireTestRoute


async def async_setup_entry(
    hass: HomeAssistant, entry: MowerEntry, add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Add actions shared by mowers using the Planner protocol."""
    add_entities(
        AutomowerButton(entry.runtime_data, key)
        for key in ("resume_schedule", "park_indefinitely")
    )
    profile = entry.runtime_data.firmware_profile
    if profile and profile.spot_cut_trigger:
        async_add_detected(
            entry,
            add_entities,
            {"spot_cut_available": None},
            lambda key, _: SpotCutButton(entry.runtime_data, key),
            require_true=True,
        )
    async_add_detected(
        entry,
        add_entities,
        {"operation_mode": None},
        lambda _key, _: AutomowerButton(entry.runtime_data, "mow_secondary_area"),
    )
    async_add_detected(
        entry,
        add_entities,
        {"geofence_center_settable": None},
        lambda key, _: RecenterGeoFenceButton(entry.runtime_data, key),
        require_true=True,
    )
    if profile and profile.blade_usage_reset:
        async_add_detected(
            entry,
            add_entities,
            {"blade_reset_available": None},
            lambda _key, _: AutomowerButton(entry.runtime_data, "reset_blade_usage"),
        )
    if profile and profile.station_pairing_subscription:
        add_entities([PairStationButton(entry.runtime_data, "pair_charging_station")])
    if profile and profile.wire_test_states:
        _async_add_wire_test_buttons(entry, add_entities)
    if profile and profile.docking_pattern:
        async_add_detected(
            entry,
            add_entities,
            {"epos_installation_state": None},
            lambda _key, _: EposDockingInstallationButton(
                entry.runtime_data, "start_epos_docking_installation"
            ),
        )
    if profile and profile.docking_pattern_stop:
        async_add_detected(
            entry,
            add_entities,
            {"epos_installation_state": None},
            lambda _key, _: StopEposDockingInstallationButton(
                entry.runtime_data, "stop_epos_docking_installation"
            ),
        )
    async_add_detected(
        entry,
        add_entities,
        {"error_confirmable": None},
        lambda key, _: ConfirmFaultButton(entry.runtime_data, "confirm_fault"),
        require_true=True,
    )
    async_add_detected(
        entry,
        add_entities,
        FIRMWARE_TARGETS,
        lambda key, _: FirmwareCheckButton(entry.runtime_data, key),
    )
    async_add_detected(
        entry,
        add_entities,
        {"mower_clock": None},
        lambda _key, _: SyncClockButton(entry.runtime_data, "sync_mower_clock"),
    )
    async_add_detected(
        entry,
        add_entities,
        {"epos_reference_station_id": None},
        lambda _key, _: ScanReferenceStationsButton(
            entry.runtime_data, "scan_epos_reference_stations"
        ),
    )
    async_add_detected(
        entry,
        add_entities,
        {"epos_reference_station_id": None},
        lambda _key, _: StopReferenceScanButton(
            entry.runtime_data, "stop_epos_reference_scan"
        ),
    )


def _async_add_wire_test_buttons(
    entry: MowerEntry, add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    routes = [
        WireTestRoute(
            setting.key,
            "test_starting_point",
            setting.index,
            outbound=True,
            placeholder=setting.index,
        )
        for setting in coordinator.settings
        if setting.translation_key == "starting_point_distance"
        and setting.index is not None
    ]
    routes.extend(
        (
            WireTestRoute("station_search_boundary", "test_return_boundary_right", 0),
            WireTestRoute("station_search_boundary", "test_return_boundary_left", 1),
        )
    )
    routes.extend(
        WireTestRoute(
            f"station_search_guide_{index}",
            "test_return_guide",
            index + 1,
            placeholder=index,
        )
        for index in range(1, 4)
    )
    for route in routes:
        async_add_detected(
            entry,
            add_entities,
            {route.capability: route},
            lambda _key, test_route: StartWireTestButton(coordinator, test_route),
        )
    async_add_detected(
        entry,
        add_entities,
        {"wire_test_status": None},
        lambda _key, _: StopWireTestButton(coordinator, "stop_wire_test"),
    )
    async_add_detected(
        entry,
        add_entities,
        {"wire_test_distance": None},
        lambda _key, _: SaveWireTestDistanceButton(
            coordinator, "save_wire_test_distance"
        ),
    )


class AutomowerButton(MowerEntity, ButtonEntity):
    """A one-shot mower command."""

    async def async_press(self) -> None:
        await self.coordinator.async_mower_action(self.key)


class SpotCutButton(MowerValueEntity, ButtonEntity):
    """Start a spot cut on mowers equipped for it."""

    @property
    def available(self) -> bool:
        return super().available and self.value == 1

    async def async_press(self) -> None:
        await self.coordinator.async_mower_action("spot_cut")


class RecenterGeoFenceButton(MowerValueEntity, ButtonEntity):
    """Move the fence centre to the mower's current position."""

    @property
    def available(self) -> bool:
        return super().available and self.value == 1

    async def async_press(self) -> None:
        await self.coordinator.async_mower_action("recenter_geofence")


class FirmwareCheckButton(MowerEntity, ButtonEntity):
    """Ask the mower or station to check for its vendor firmware."""

    def __init__(self, coordinator: MowerCoordinator, target: str) -> None:
        super().__init__(coordinator, f"{target}_check")
        self.target = target

    @property
    def available(self) -> bool:
        status = self.coordinator.firmware_status(self.target)
        return (
            super().available
            and status is not None
            and status.phase == UpdatePhase.IDLE
        )

    async def async_press(self) -> None:
        await self.coordinator.async_firmware_action(self.target, check=True)


class PairStationButton(MowerEntity, ButtonEntity):
    """Ask the docked mower to generate a new charging-station loop signal."""

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        data = self.coordinator.data
        if "in_charging_station" in data:
            return data["in_charging_station"] == 1
        return data.get("activity") in (MowerActivity.PARKED, MowerActivity.CHARGING)

    async def async_press(self) -> None:
        await self.coordinator.async_pair_station()


class SyncClockButton(MowerEntity, ButtonEntity):
    """Synchronize the local Calendar clock used for mowing schedules."""

    _attr_entity_category = EntityCategory.CONFIG

    @property
    def available(self) -> bool:
        return super().available and "mower_clock" in self.coordinator.data

    async def async_press(self) -> None:
        await self.coordinator.async_sync_clock()


class StartWireTestButton(MowerEntity, ButtonEntity):
    """Prepare an individual starting-point or return-wire test."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: MowerCoordinator, route: WireTestRoute) -> None:
        super().__init__(coordinator, f"{route.label}_{route.index}")
        self._attr_translation_key = route.label
        if route.placeholder is not None:
            self._attr_translation_placeholders = {"index": str(route.placeholder)}
        self.route = route

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.route.capability in self.coordinator.data
            and not self.coordinator.wire_test_active
        )

    async def async_press(self) -> None:
        await self.coordinator.async_start_wire_test(self.route)


class StopWireTestButton(MowerEntity, ButtonEntity):
    """Cancel the test started from this integration."""

    _attr_entity_category = EntityCategory.CONFIG

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.wire_test_active

    async def async_press(self) -> None:
        await self.coordinator.async_stop_wire_test()


class ScanReferenceStationsButton(MowerEntity, ButtonEntity):
    """Find nearby EPOS stations through the mower's radio."""

    _attr_entity_category = EntityCategory.CONFIG

    @property
    def available(self) -> bool:
        return (
            super().available
            and "epos_reference_station_id" in self.coordinator.data
            and not self.coordinator.reference_scan_active
        )

    async def async_press(self) -> None:
        await self.coordinator.async_scan_reference_stations()


class EposDockingInstallationButton(MowerEntity, ButtonEntity):
    """Ask the mower to establish the EPOS docking point from its base."""

    _attr_entity_category = EntityCategory.CONFIG

    @property
    def available(self) -> bool:
        data = self.coordinator.data
        return (
            super().available
            and "epos_installation_result" in data
            and data.get("epos_installation_state") == 0
            and not self.coordinator.docking_installation_active
        )

    async def async_press(self) -> None:
        await self.coordinator.async_start_epos_docking_installation()


class StopEposDockingInstallationButton(MowerEntity, ButtonEntity):
    """Ask compatible firmware to cancel the docking-point procedure."""

    _attr_entity_category = EntityCategory.CONFIG

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.docking_installation_active

    async def async_press(self) -> None:
        await self.coordinator.async_stop_epos_docking_installation()


class StopReferenceScanButton(MowerEntity, ButtonEntity):
    """Stop the current station scan and retain its discoveries."""

    _attr_entity_category = EntityCategory.CONFIG

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.reference_scan_active

    async def async_press(self) -> None:
        await self.coordinator.async_stop_reference_scan()


class SaveWireTestDistanceButton(MowerEntity, ButtonEntity):
    """Use the measured distance as the tested starting-point setting."""

    _attr_entity_category = EntityCategory.CONFIG

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.wire_test_distance_saveable

    async def async_press(self) -> None:
        await self.coordinator.async_save_wire_test_distance()


class ConfirmFaultButton(MowerEntity, ButtonEntity):
    """Acknowledge the fault offered by the mower."""

    @property
    def available(self) -> bool:
        data = self.coordinator.data
        return (
            super().available
            and data.get("error_confirmable") == 1
            and bool(data.get("error_code"))
        )

    async def async_press(self) -> None:
        code = self.coordinator.data.get("error_code")
        if not isinstance(code, int) or not code:
            raise ServiceValidationError("The mower does not report an active fault")
        await self.coordinator.async_confirm_error(code)
