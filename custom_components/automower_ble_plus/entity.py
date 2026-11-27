"""Shared entity registration and availability for Automower BLE Plus."""

from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import MowerCoordinator, MowerEntry
from .cutting_status import GLOBAL_HEIGHT_MM, GlobalCuttingHeight
from .features import Setting


@callback
def async_add_detected[Item](
    entry: MowerEntry,
    add_entities: AddConfigEntryEntitiesCallback,
    items: Mapping[str, Item],
    factory: Callable[[str, Item], Entity],
    *,
    require_true: bool = False,
) -> None:
    """Add entities when the mower first reports a compatible value."""
    coordinator = entry.runtime_data
    pending = dict(items)

    @callback
    def _async_detect() -> None:
        ready = pending.keys() & coordinator.data.keys()
        if require_true:
            ready = {key for key in ready if coordinator.data[key] == 1}
        if ready:
            add_entities([factory(key, pending.pop(key)) for key in sorted(ready)])

    entry.async_on_unload(coordinator.async_add_listener(_async_detect))
    _async_detect()


class MowerEntity(CoordinatorEntity[MowerCoordinator]):
    """An entity belonging to one BLE mower."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MowerCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self.key = key
        self._attr_unique_id = f"{coordinator.address}_{key}"
        self._attr_device_info = coordinator.device_info
        self._attr_translation_key = key

    @property
    def available(self) -> bool:
        """Return whether the mower's BLE session is active."""
        return super().available and self.coordinator.client.is_connected()


class MowerValueEntity(MowerEntity):
    """An entity backed by a reported value from the mower."""

    @property
    def available(self) -> bool:
        """Check that the mower still provides this capability."""
        return super().available and self.key in self.coordinator.data

    @property
    def value(self) -> int | None:
        """Return the last value reported by the mower."""
        value = self.coordinator.data.get(self.key)
        return value if isinstance(value, int) else None


class MowerGlobalHeightEntity(MowerEntity):
    """Expose the current native global height and range."""

    def __init__(self, coordinator: MowerCoordinator) -> None:
        super().__init__(coordinator, GLOBAL_HEIGHT_MM)

    @property
    def height(self) -> GlobalCuttingHeight | None:
        value = self.coordinator.data.get(self.key)
        return value if isinstance(value, GlobalCuttingHeight) else None

    @property
    def available(self) -> bool:
        return super().available and self.height is not None

    @property
    def native_value(self) -> int | None:
        return height.value if (height := self.height) is not None else None


class MowerClockEntity(MowerEntity):
    """Use the mower's wall time for its editable date and time controls."""

    _attr_entity_category = EntityCategory.CONFIG

    @property
    def wall_clock(self) -> datetime | None:
        value = self.coordinator.data.get("mower_clock")
        if isinstance(value, int):
            return datetime.fromtimestamp(value, UTC)
        return None

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.wall_clock is not None
            and self.coordinator.data.get("automatic_clock_sync") != 1
        )


class MowerSettingEntity(MowerValueEntity):
    """A writable setting with a readback from the mower."""

    def __init__(self, coordinator: MowerCoordinator, setting: Setting) -> None:
        super().__init__(coordinator, setting.key)
        self.setting = setting
        if setting.translation_key is not None:
            self._attr_translation_key = setting.translation_key
        if setting.index is not None:
            self._attr_translation_placeholders = {"index": str(setting.index)}

    async def async_set_value(self, value: int) -> None:
        """Write a new setting to the mower."""
        await self.coordinator.async_set_setting(self.setting, value)
