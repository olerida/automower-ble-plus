"""Show the mower's BLE map previews as Home Assistant image entities."""

import asyncio
from collections.abc import Callable
from datetime import datetime

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .const import LOGGER
from .coordinator import MowerCoordinator, MowerEntry
from .entity import MowerEntity, async_add_detected
from .mapfiles import MapKind

_MAP_PREVIEW_RETRY_SECONDS = 60


async def async_setup_entry(
    hass: HomeAssistant, entry: MowerEntry, add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Add previews only for the map variants the mower reports."""
    async_add_detected(
        entry,
        add_entities,
        {kind.preview_key: kind for kind in MapKind},
        lambda _key, kind: MowerMapImage(entry.runtime_data, kind),
    )


class MowerMapImage(MowerEntity, ImageEntity):
    """A cached raster image of a map's BLE geometry."""

    _attr_content_type = "image/png"

    def __init__(self, coordinator: MowerCoordinator, kind: MapKind) -> None:
        MowerEntity.__init__(self, coordinator, kind.preview_key)
        ImageEntity.__init__(self, coordinator.hass)
        self.kind = kind
        self._png: bytes | None = None
        self._image_revision: str | None = None
        self._render_task: asyncio.Task[None] | None = None
        self._retry_cancel: Callable[[], None] | None = None
        self._retry_revision: str | None = None
        self._removed = False

    @property
    def _revision(self) -> str | None:
        value = self.coordinator.data.get(self.key)
        return value if isinstance(value, str) else None

    @property
    def available(self) -> bool:
        return self._png is not None and self._image_revision == self._revision

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._async_cancel_refresh)
        self._async_schedule_preview()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._async_schedule_preview()
        super()._handle_coordinator_update()

    @callback
    def _async_schedule_preview(self) -> None:
        revision = self._revision
        if (
            self._removed
            or revision is None
            or self._image_revision == revision
            or self._retry_revision == revision
            or self._render_task is not None
            or not super().available
        ):
            return
        self._async_cancel_retry()
        self._render_task = self.coordinator.config_entry.async_create_background_task(
            self.hass,
            self._async_refresh_preview(revision),
            f"render the Automower {self.kind.value} map",
            eager_start=False,
        )

    async def _async_refresh_preview(self, revision: str) -> None:
        retry = True
        try:
            png = await self.coordinator.async_map_preview(self.kind)
        except ValueError:
            LOGGER.debug(
                "Invalid geometry in the Automower %s map",
                self.kind.value,
                exc_info=True,
            )
            retry = False
            png = None
        except HomeAssistantError, OSError:
            LOGGER.debug(
                "Could not render the Automower %s map", self.kind.value, exc_info=True
            )
            png = None
        finally:
            self._render_task = None
        if self._removed:
            return
        if self._revision != revision:
            self._async_schedule_preview()
        elif png is not None:
            if self._png != png:
                self._attr_image_last_updated = dt_util.utcnow()
            self._png = png
            self._image_revision = revision
            self.async_write_ha_state()
        else:
            self._retry_revision = revision
            if retry:
                self._retry_cancel = async_call_later(
                    self.hass, _MAP_PREVIEW_RETRY_SECONDS, self._async_retry_preview
                )

    @callback
    def _async_retry_preview(self, _now: datetime) -> None:
        self._retry_cancel = None
        self._retry_revision = None
        self._async_schedule_preview()

    @callback
    def _async_cancel_retry(self) -> None:
        if self._retry_cancel is not None:
            self._retry_cancel()
            self._retry_cancel = None
        self._retry_revision = None

    @callback
    def _async_cancel_refresh(self) -> None:
        self._removed = True
        self._async_cancel_retry()
        if self._render_task is not None:
            self._render_task.cancel()

    async def async_image(self) -> bytes | None:
        """Serve the cached PNG without holding the BLE link during image requests."""
        return self._png if self.available else None
