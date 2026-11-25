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
