"""View and edit the mower's weekly schedule in the Home Assistant calendar."""

from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from homeassistant.components.calendar import (
    EVENT_END,
    EVENT_RRULE,
    EVENT_START,
    CalendarEntity,
    CalendarEntityFeature,
    CalendarEvent,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.translation import async_get_translations
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import MowerCoordinator, MowerEntry
from .entity import MowerEntity, async_add_detected
from .schedule import ScheduleTask, WeeklySchedule

_DAY = timedelta(days=1)
_WEEK_AHEAD = timedelta(days=8)
_BYDAY = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")


async def async_setup_entry(
    hass: HomeAssistant, entry: MowerEntry, add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Expose the calendar after the first successful read of a weekly schedule."""
    translations = await async_get_translations(
        hass, hass.config.language, "common", {DOMAIN}
    )
    async_add_detected(
        entry,
        add_entities,
        {"weekly_schedule": None},
        lambda key, _: MowerCalendar(entry.runtime_data, key, translations),
    )


def _task_ids(tasks: tuple[ScheduleTask, ...], namespace: str) -> Iterator[str]:
    duplicates: Counter[ScheduleTask] = Counter()
    for task in tasks:
        duplicates[task] += 1
        days = "".join(str(day) for day in task.days)
        yield f"{namespace}-{task.start}-{task.duration}-{days}-{task.mission_id}-{duplicates[task]}"


def _wall_instant(wall: datetime, zone: ZoneInfo) -> datetime:
    """Map a mower wall time to an instant, clipping nonexistent times to the DST change."""
    instant = wall.astimezone(UTC)
    if instant.astimezone(zone).replace(tzinfo=None) == wall.replace(tzinfo=None):
        return instant

    lower = int(wall.replace(fold=1).timestamp())
    upper = int(instant.timestamp())
    offset_after = instant.astimezone(zone).utcoffset()
    while upper - lower > 1:
        middle = (lower + upper) // 2
        if datetime.fromtimestamp(middle, zone).utcoffset() == offset_after:
            upper = middle
        else:
            lower = middle
    return datetime.fromtimestamp(upper, UTC)


def _occurrences(
    task: ScheduleTask, zone: ZoneInfo, lower: datetime, upper: datetime
) -> Iterator[tuple[datetime, datetime]]:
    if not (0 <= task.start < 86400 and 0 < task.duration <= 86400):
        return
    lower, upper = lower.astimezone(UTC), upper.astimezone(UTC)
    first = lower.astimezone(zone).date() - _DAY
    last = upper.astimezone(zone).date()
    for offset in range((last - first).days + 1):
        day = first + timedelta(days=offset)
        if not task.days[day.weekday()]:
            continue
        wall = datetime.combine(day, time.min, zone) + timedelta(seconds=task.start)
        start = _wall_instant(wall, zone)
        end = _wall_instant(wall + timedelta(seconds=task.duration), zone)
        if start < upper and lower < end and start < end:
            yield start.astimezone(zone), end.astimezone(zone)


def _event_datetime(value: object, zone: ZoneInfo) -> datetime:
    if isinstance(value, datetime) and value.utcoffset() is not None:
        return value.astimezone(zone)
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime.combine(value, time.min, zone)
    raise ServiceValidationError("Use a start and end date for the mower schedule")


def _event_task(event: Mapping[str, object], zone: ZoneInfo) -> ScheduleTask:
    rule = event.get(EVENT_RRULE)
    if not isinstance(rule, str) or not rule:
        raise ServiceValidationError("Choose a daily or weekly repeat with no end date")
    parts: dict[str, str] = {}
    for segment in rule.upper().removeprefix("RRULE:").split(";"):
        key, _, value = segment.partition("=")
        if (
            key in parts
            or key not in ("FREQ", "BYDAY", "INTERVAL", "WKST")
            or not value
        ):
            raise ServiceValidationError(
                "Choose a daily or weekly repeat with no end date"
            )
        parts[key] = value
    frequency = parts.get("FREQ")
    if (
        frequency not in ("DAILY", "WEEKLY")
        or parts.get("INTERVAL", "1") != "1"
        or parts.get("WKST", "MO") not in _BYDAY
    ):
        raise ServiceValidationError(
            "Repeat the mower schedule every day or every week"
        )

    start_value = event.get(EVENT_START)
    start = _event_datetime(start_value, zone)
    end = _event_datetime(event.get(EVENT_END), zone)
    duration = (end.replace(tzinfo=None) - start.replace(tzinfo=None)).total_seconds()
    if not 0 < duration <= 86400 or start.microsecond or end.microsecond:
        raise ServiceValidationError("A mowing interval must last at most 24 hours")

    source_day = (
        start_value.date() if isinstance(start_value, datetime) else start.date()
    )
    day_shift = (start.date() - source_day).days
    default_days = (
        ",".join(_BYDAY) if frequency == "DAILY" else _BYDAY[source_day.weekday()]
    )
    weekdays = set(parts.get("BYDAY", default_days).split(","))
    if not weekdays.issubset(_BYDAY):
        raise ServiceValidationError("Choose weekdays for the mower schedule")
    task = ScheduleTask(
        start.hour * 3600 + start.minute * 60 + start.second,
        int(duration),
        tuple(int(_BYDAY[(index - day_shift) % 7] in weekdays) for index in range(7)),
    )
    now = dt_util.utcnow()
    first_requested = start.astimezone(UTC)
    if first_requested > now and next(
        _occurrences(task, zone, now, first_requested), None
    ):
        raise ServiceValidationError(
            "The mower applies weekly changes immediately. Choose the first upcoming occurrence."
        )
    return task


def _selected_task(
    schedule: WeeklySchedule,
    namespace: str,
    uid: str,
    recurrence_id: str | None,
    recurrence_range: str | None,
) -> int:
    for index, task_id in enumerate(_task_ids(schedule.tasks, namespace)):
        if task_id == uid:
            break
    else:
        raise ServiceValidationError(
            "This mowing interval has changed. Refresh the calendar."
        )
    if not recurrence_id:
        return index
    if recurrence_range != "THISANDFUTURE":
        raise ServiceValidationError(
            "Choose This and following events for the weekly schedule"
        )
    selected = dt_util.parse_datetime(recurrence_id)
    if selected is None or selected.utcoffset() is None:
        raise ServiceValidationError("The selected mowing occurrence is invalid")
    now = dt_util.utcnow()
    next_event = next(
        _occurrences(schedule.tasks[index], schedule.time_zone, now, now + _WEEK_AHEAD),
        None,
    )
    if next_event is not None and selected > next_event[0].astimezone(UTC):
        raise ServiceValidationError(
            "Edit the next occurrence to change this week's schedule"
        )
    return index


class MowerCalendar(MowerEntity, CalendarEntity):
    """The locally stored weekly mowing schedule, exposed as recurring events."""

    _attr_supported_features = (
        CalendarEntityFeature.CREATE_EVENT
        | CalendarEntityFeature.UPDATE_EVENT
        | CalendarEntityFeature.DELETE_EVENT
    )

    def __init__(
        self, coordinator: MowerCoordinator, key: str, translations: Mapping[str, str]
    ) -> None:
        super().__init__(coordinator, key)
        self._namespace = coordinator.config_entry.entry_id
        self._summary = translations.get(
            f"component.{DOMAIN}.common.calendar_mowing", "Mowing"
        )
        self._area = translations.get(
            f"component.{DOMAIN}.common.calendar_work_area", "Work area {mission_id}"
        )

    @property
    def _schedule(self) -> WeeklySchedule | None:
        value = self.coordinator.data.get(self.key)
        return value if isinstance(value, WeeklySchedule) else None

    @property
    def available(self) -> bool:
        return super().available and self._schedule is not None

    @property
    def event(self) -> CalendarEvent | None:
        now = dt_util.utcnow()
        return next(iter(self._events(now, now + _WEEK_AHEAD)), None)

    def _events(self, lower: datetime, upper: datetime) -> list[CalendarEvent]:
        if (schedule := self._schedule) is None:
            return []
        events = []
        for uid, task in zip(
            _task_ids(schedule.tasks, self._namespace), schedule.tasks, strict=True
        ):
            period = ",".join(
                day for day, enabled in zip(_BYDAY, task.days, strict=True) if enabled
            )
            for start, end in _occurrences(task, schedule.time_zone, lower, upper):
                events.append(
                    CalendarEvent(
                        start=start,
                        end=end,
                        summary=self._summary,
                        description=self._area.format(mission_id=task.mission_id)
                        if task.mission_id
                        else None,
                        uid=uid,
                        recurrence_id=start.isoformat(),
                        rrule=f"FREQ=WEEKLY;BYDAY={period}",
                    )
                )
        return sorted(
            events, key=lambda event: event.start_datetime_local.astimezone(UTC)
        )

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Return each occurrence in the requested period."""
        return self._events(start_date, end_date)

    async def async_create_event(self, **kwargs: object) -> None:
        """Add a recurring mowing interval for the default area."""

        def append(current: WeeklySchedule) -> tuple[ScheduleTask, ...]:
            return (*current.tasks, _event_task(kwargs, current.time_zone))

        await self.coordinator.async_edit_schedule(append)

    async def async_update_event(
        self,
        uid: str,
        event: Mapping[str, object],
        recurrence_id: str | None = None,
        recurrence_range: str | None = None,
    ) -> None:
        """Replace a confirmed recurring interval while keeping its work area."""

        def update(current: WeeklySchedule) -> list[ScheduleTask]:
            index = _selected_task(
                current, self._namespace, uid, recurrence_id, recurrence_range
            )
            tasks = list(current.tasks)
            change = _event_task(event, current.time_zone)
            tasks[index] = replace(change, mission_id=tasks[index].mission_id)
            return tasks

        await self.coordinator.async_edit_schedule(update)

    async def async_delete_event(
        self,
        uid: str,
        recurrence_id: str | None = None,
        recurrence_range: str | None = None,
    ) -> None:
        """Delete a currently stored weekly interval."""

        def delete(current: WeeklySchedule) -> list[ScheduleTask]:
            tasks = list(current.tasks)
            del tasks[
                _selected_task(
                    current, self._namespace, uid, recurrence_id, recurrence_range
                )
            ]
            return tasks

        await self.coordinator.async_edit_schedule(delete)
