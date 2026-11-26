"""The main Home Assistant lawn mower entity."""

from automower_ble.protocol import MowerActivity, MowerState
from homeassistant.components.lawn_mower import (
    LawnMowerActivity,
    LawnMowerEntity,
    LawnMowerEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import MowerEntry
from .entity import MowerEntity
from .mapfiles import MapKind
from .schedule import ScheduleTask


async def async_setup_entry(
    hass: HomeAssistant, entry: MowerEntry, add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Add the mower entity for this entry."""
    add_entities([AutomowerEntity(entry.runtime_data, "mower")])


class AutomowerEntity(MowerEntity, LawnMowerEntity):
    """Expose standard mower controls and timed actions."""

    _attr_name = None
    _attr_supported_features = (
        LawnMowerEntityFeature.START_MOWING
        | LawnMowerEntityFeature.DOCK
        | LawnMowerEntityFeature.PAUSE
    )

    @property
    def activity(self) -> LawnMowerActivity | None:
        """Translate the mower state machine into Home Assistant activities."""
        state, activity = (
            self.coordinator.data["state"],
            self.coordinator.data["activity"],
        )
        if state == MowerState.PAUSED:
            return LawnMowerActivity.PAUSED
        if state in (
            MowerState.ERROR,
            MowerState.FATAL_ERROR,
            MowerState.WAIT_FOR_SAFETYPIN,
        ):
            return LawnMowerActivity.ERROR
        if state in (MowerState.OFF, MowerState.STOPPED):
            return LawnMowerActivity.IDLE
        if activity in (MowerActivity.CHARGING, MowerActivity.PARKED):
            return LawnMowerActivity.DOCKED
        if activity in (MowerActivity.GOING_OUT, MowerActivity.MOWING):
            return LawnMowerActivity.MOWING
        if activity == MowerActivity.GOING_HOME:
            return LawnMowerActivity.RETURNING
        if activity == MowerActivity.STOPPED_IN_GARDEN:
            return LawnMowerActivity.IDLE
        if activity == MowerActivity.NONE and state == MowerState.RESTRICTED:
            return LawnMowerActivity.DOCKED
        return None

    async def async_start_mowing(self) -> None:
        await self.coordinator.async_mower_action("start")

    async def async_dock(self) -> None:
        await self.coordinator.async_mower_action("park")

    async def async_pause(self) -> None:
        await self.coordinator.async_mower_action("pause")

    async def async_mow_for(self, minutes: int, mission_id: int | None = None) -> None:
        """Mow outside the normal schedule for the requested duration."""
        await self.coordinator.async_mower_action(
            "mow_for", minutes * 60, mission_id=mission_id
        )

    async def async_park_for(self, minutes: int) -> None:
        """Park outside the normal schedule for the requested duration."""
        await self.coordinator.async_mower_action("park_for", minutes * 60)

    async def async_mow_secondary_area(
        self, minutes: int | None = None, mission_id: int | None = None
    ) -> None:
        """Start the mower in the secondary area for a duration or until the battery runs low."""
        await self.coordinator.async_mower_action(
            "mow_secondary_area",
            minutes * 60 if minutes is not None else None,
            mission_id=mission_id,
        )

    async def async_get_schedule(self) -> dict:
        """Return the mower's locally stored weekly schedule."""
        return await self.coordinator.async_read_schedule()

    async def async_replace_schedule(self, intervals: list[dict]) -> None:
        """Replace and verify the weekly schedule."""
        await self.coordinator.async_write_schedule(
            [ScheduleTask.from_action(**interval) for interval in intervals]
        )

    async def async_get_message_history(self, offset: int, limit: int) -> dict:
        """Return messages from the mower's local history."""
        return await self.coordinator.async_read_history(offset, limit)

    async def async_get_work_areas(self, offset: int, limit: int) -> dict:
        """Return available work areas and their device-assigned IDs."""
        return await self.coordinator.async_read_work_areas(offset, limit)

    async def async_create_work_area(
        self, name: str, area_id: str, expected_checksum: str
    ) -> dict:
        """Link a new mowing task to a polygon on the active map."""
        return await self.coordinator.async_create_work_area(
            name, area_id, expected_checksum
        )

    async def async_delete_work_area(self, mission_id: int, expected_name: str) -> None:
        """Remove a confirmed work area without pending scheduled intervals."""
        await self.coordinator.async_delete_work_area(mission_id, expected_name)

    async def async_get_work_area_status(self, mission_id: int | None = None) -> dict:
        """Read the area's saved progress and recent mowing timestamps."""
        return await self.coordinator.async_work_area_status(mission_id)

    async def async_reset_work_area_progress(self, mission_id: int) -> None:
        """Reset the saved progress for this area."""
        await self.coordinator.async_clear_work_area_progress(mission_id)

    async def async_get_work_area_mowing(self, mission_id: int) -> dict:
        """Return the supported settings for one mowing area."""
        return await self.coordinator.async_read_work_area_mowing(mission_id)

    async def async_set_work_area_mowing(self, mission_id: int, **changes) -> None:
        """Change one supported setting for a mowing area."""
        await self.coordinator.async_write_work_area_mowing(mission_id, changes)

    async def async_update_work_area(
        self, mission_id: int, name: str | None = None, enabled: bool | None = None
    ) -> None:
        """Rename or enable/disable an area when supported by the mower."""
        await self.coordinator.async_change_work_area(
            mission_id, name=name, enabled=enabled
        )

    async def async_get_installations(self, offset: int, limit: int) -> dict:
        """List the available installation profiles on the mower."""
        return await self.coordinator.async_read_installations(offset, limit)

    async def async_activate_installation(
        self, site_id: int, expected_active_id: int | None = None
    ) -> None:
        """Switch to a saved installation."""
        await self.coordinator.async_set_installation(site_id, expected_active_id)

    async def async_rename_installation(self, site_id: int, name: str) -> None:
        """Rename the active installation after verifying the site ID."""
        await self.coordinator.async_change_installation_name(site_id, name)

    async def async_create_installation(self, copy_current: bool) -> dict:
        """Create an installation and return the assigned identifier."""
        return await self.coordinator.async_create_installation(copy_current)

    async def async_delete_installation(self, site_id: int, expected_name: str) -> None:
        """Delete a confirmed inactive installation."""
        await self.coordinator.async_delete_installation(site_id, expected_name)

    async def async_get_map(self, generated: bool = False) -> dict:
        """Read the site or generated map as Automower SVG, when available."""
        return await self.coordinator.async_read_map(generated)

    async def async_get_zone_map(self) -> dict:
        """Read the separate zone map directly from the mower."""
        return await self.coordinator.async_read_zone_map()

    async def async_get_map_features(self, map_type: str) -> dict:
        """Return the named areas and paths reported in the selected map."""
        return await self.coordinator.async_read_map_features(MapKind(map_type))

    async def async_edit_map_geometry(
        self,
        feature_id: str,
        expected_checksum: str,
        points: list[dict[str, int]],
        expected_installation_id: int | None = None,
    ) -> dict:
        """Update the vertices of a selected feature in the active map."""
        return await self.coordinator.async_edit_map_geometry(
            feature_id, expected_checksum, points, expected_installation_id
        )

    async def async_get_position(self) -> dict:
        """Read the mower's geographic and local positions over BLE."""
        return await self.coordinator.async_read_position()

    async def async_get_geofence(self) -> dict:
        """Read the GeoFence settings that this mower exposes over BLE."""
        return await self.coordinator.async_read_geofence()

    async def async_get_loop_signals(self) -> dict:
        """Read signal strength where the mower is currently standing."""
        return await self.coordinator.async_read_loop_signals()

    async def async_replace_map(self, svg: str, expected_checksum: str) -> dict:
        """Upload a matching revision of the active installation map."""
        return await self.coordinator.async_write_map(svg, expected_checksum)
