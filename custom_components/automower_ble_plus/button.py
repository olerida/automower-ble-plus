"""One-shot schedule and parking commands."""

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import MowerEntry
from .entity import MowerEntity, MowerValueEntity, async_add_detected


async def async_setup_entry(
    hass: HomeAssistant, entry: MowerEntry, add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Add actions shared by mowers using the Planner protocol."""
    add_entities(
        AutomowerButton(entry.runtime_data, key)
        for key in ("resume_schedule", "park_indefinitely")
    )
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
    profile = entry.runtime_data.firmware_profile
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
