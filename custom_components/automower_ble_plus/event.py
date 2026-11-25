"""Expose messages delivered by the mower as Home Assistant events."""

from typing import ClassVar

from homeassistant.components.event import EventEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import MowerEntry
from .entity import MowerEntity, async_add_detected
from .faults import SEVERITY_NAMES


async def async_setup_entry(
    hass: HomeAssistant, entry: MowerEntry, add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Add the event entity once the mower accepts the message subscription."""
    async_add_detected(
        entry,
        add_entities,
        {"message_events": None},
        lambda key, _: MowerMessageEvent(entry.runtime_data, key),
        require_true=True,
    )


class MowerMessageEvent(MowerEntity, EventEntity):
    """A new message from the mower's message history."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_event_types: ClassVar[list[str]] = list(SEVERITY_NAMES)

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.get(self.key) == 1

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self.coordinator.message_signal, self._async_handle_message
            )
        )

    @callback
    def _async_handle_message(self, message: dict) -> None:
        self._trigger_event(message.get("severity_name", "unknown"), message)
        self.async_write_ha_state()
