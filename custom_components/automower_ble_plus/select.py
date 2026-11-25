"""Select entities for mower settings with enumerated values."""

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import MowerCoordinator, MowerEntry
from .entity import MowerEntity, MowerSettingEntity, async_add_detected
from .features import Setting
from .sites import InstallationSelection


async def async_setup_entry(
    hass: HomeAssistant, entry: MowerEntry, add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Add supported settings with a fixed set of options."""
    async_add_detected(
        entry,
        add_entities,
        {
            setting.key: setting
            for setting in entry.runtime_data.settings
            if setting.options
        },
        lambda key, setting: AutomowerSelect(entry.runtime_data, setting),
    )
    async_add_detected(
        entry,
        add_entities,
        {"installation_selection": None},
        lambda key, _: ActiveInstallationSelect(entry.runtime_data, key),
    )


class AutomowerSelect(MowerSettingEntity, SelectEntity):
    """An enumerated setting written over BLE."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: MowerCoordinator, setting: Setting) -> None:
        super().__init__(coordinator, setting)
        self._attr_options = list(setting.options)
        self._option_values = setting.option_values or tuple(
            range(len(setting.options))
        )

    @property
    def current_option(self) -> str | None:
        value = self.value
        if value is None or value not in self._option_values:
            return None
        return self.options[self._option_values.index(value)]

    async def async_select_option(self, option: str) -> None:
        await self.async_set_value(self._option_values[self.options.index(option)])


class ActiveInstallationSelect(MowerEntity, SelectEntity):
    """Select a profile already stored on the mower."""

    _attr_entity_category = EntityCategory.CONFIG

    @property
    def _selection(self) -> InstallationSelection | None:
        selection = self.coordinator.data.get(self.key)
        return selection if isinstance(selection, InstallationSelection) else None

    @property
    def available(self) -> bool:
        return super().available and self._selection is not None

    @property
    def options(self) -> list[str]:
        selection = self._selection
        return list(self._labels(selection)) if selection else []

    @property
    def current_option(self) -> str | None:
        selection = self._selection
        if selection is None:
            return None
        return next(
            (
                label
                for label, site_id in self._labels(selection).items()
                if site_id == selection.active_id
            ),
            None,
        )

    @staticmethod
    def _labels(selection: InstallationSelection) -> dict[str, int]:
        return {f"{name} ({site_id})": site_id for site_id, name in selection.profiles}

    async def async_select_option(self, option: str) -> None:
        selection = self._selection
        if (
            selection is None
            or (site_id := self._labels(selection).get(option)) is None
        ):
            raise ServiceValidationError("This installation is no longer available")
        await self.coordinator.async_set_installation(site_id, selection.active_id)
