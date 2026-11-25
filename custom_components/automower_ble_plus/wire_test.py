"""Follow a boundary or guide wire in the mower's installation test mode."""

from dataclasses import dataclass
from enum import IntEnum

from automower_ble.protocol import MowerActivity, MowerState

from .features import GET_ACTIVITY, GET_STATE, U8, U16, Reading
from .transport import AutomowerClient, CommandRejected
from .wire import Command, Event

_FOLLOW_WIRE = 4706
_PAUSE = Command(4586, 5)
_IN_CHARGING_STATION = Reading(Command(4692, 27), U8)
_TEST_STARTING_POINT = Command(_FOLLOW_WIRE, 15)
_TEST_FOLLOW_IN = Command(_FOLLOW_WIRE, 17)
_TEST_ABORT = Command(_FOLLOW_WIRE, 18)
_TEST_STATE = Reading(Command(_FOLLOW_WIRE, 23), U8)
_TEST_DISTANCE = Reading(Command(_FOLLOW_WIRE, 16), U16)
_MAXIMUM_CORRIDOR = 1


class WireTestPhase(IntEnum):
    """Common phases for both reported state layouts and legacy events."""

    IDLE = 0
    PENDING_START = 1
    IN_OPERATION = 2
    DONE = 3
    ERROR = 4
    ABORTED = 5
    UNCONFIRMED = 6


WIRE_TEST_OPTIONS = tuple(phase.name.lower() for phase in WireTestPhase)
_ACTIVE_PHASES = (WireTestPhase.PENDING_START, WireTestPhase.IN_OPERATION)


@dataclass(frozen=True, slots=True)
class WireTestRoute:
    """A supported starting point or return wire."""

    capability: str
    label: str
    index: int
    outbound: bool = False
    placeholder: int | None = None


@dataclass(slots=True)
class WireTest:
    """The last locally requested test and its latest observed result."""

    route: WireTestRoute
    layout: str
    phase: WireTestPhase = WireTestPhase.PENDING_START
    distance: int | None = None

    @property
    def active(self) -> bool:
        """Whether a start needs local confirmation or the test is running."""
        return self.phase in _ACTIVE_PHASES

    async def _read_phase(self, client: AutomowerClient) -> WireTestPhase:
        raw = _TEST_STATE.decode(await client.command(_TEST_STATE.command))
        states = self.layout.split()
        if raw >= len(states):
            raise ValueError("The mower returned an unknown wire test state")
        return WireTestPhase[states[raw].upper()]

    async def async_prepare(self, client: AutomowerClient) -> None:
        """Check the same physical preparation required by Automower Connect."""
        state = Reading(GET_STATE, U8).decode(await client.command(GET_STATE))
        activity = Reading(GET_ACTIVITY, U8).decode(await client.command(GET_ACTIVITY))
        if self.layout == "legacy":
            if state != MowerState.STOPPED:
                raise ValueError(
                    "Press STOP on the mower before preparing the wire test"
                )
        else:
            if await self._read_phase(client) in _ACTIVE_PHASES:
                raise ValueError("A wire test is already active on the mower")
            if not self.route.outbound and state != MowerState.PAUSED:
                raise ValueError(
                    "Prepare the mower in its paused mode for the return test"
                )
            if self.route.outbound and state not in (
                MowerState.PAUSED,
                MowerState.STOPPED,
                MowerState.RESTRICTED,
            ):
                raise ValueError("Park the mower before testing a starting point")

        try:
            docked = _IN_CHARGING_STATION.decode(
                await client.command(_IN_CHARGING_STATION.command)
            )
        except CommandRejected as err:
            if not err.unsupported:
                raise
            # Some legacy mowers only report NONE while the physical STOP is engaged.
            docked = activity in (MowerActivity.PARKED, MowerActivity.CHARGING)
            if activity == MowerActivity.NONE and self.layout == "legacy":
                docked = None
        if docked not in (None, 0, 1):
            raise ValueError("The mower returned an invalid charging station state")
        if self.route.outbound and docked == 0:
            raise ValueError(
                "Put the mower in the charging station before testing a starting point"
            )
        if not self.route.outbound and docked:
            raise ValueError(
                "Place the mower outside the charging station facing the selected wire"
            )

        await client.command(_PAUSE)

    async def async_start(self, client: AutomowerClient) -> None:
        """Order the test without issuing a remote START trigger."""
        if self.route.outbound:
            await client.command(
                _TEST_STARTING_POINT, bytes([self.route.index, _MAXIMUM_CORRIDOR])
            )
        else:
            await client.command(_TEST_FOLLOW_IN, U8.pack(self.route.index))

    async def async_stop(self, client: AutomowerClient) -> None:
        """Use the stop procedure offered by the mower's firmware."""
        await client.command(_PAUSE if self.layout == "legacy" else _TEST_ABORT)
        if self.active:
            self.phase = WireTestPhase.ABORTED

    def handle_event(self, event: Event) -> bool:
        """Accept a recognized completion event for the active direction."""
        if (
            self.layout != "legacy"
            or (not self.active and self.phase is not WireTestPhase.UNCONFIRMED)
            or event.family != _FOLLOW_WIRE
        ):
            return False
        failure, success = (1, 2) if self.route.outbound else (3, 4)
        if event.payload or event.event_id not in (failure, success):
            return False
        self.phase = (
            WireTestPhase.DONE if event.event_id == success else WireTestPhase.ERROR
        )
        return True

    async def async_refresh(
        self, client: AutomowerClient, state: int, activity: int
    ) -> None:
        """Read the current phase and distance while a test is active."""
        if not self.active:
            return
        if self.layout != "legacy":
            phase = await self._read_phase(client)
            if phase is not WireTestPhase.IDLE:
                self.phase = phase
            elif self.phase is WireTestPhase.IN_OPERATION:
                self.phase = WireTestPhase.UNCONFIRMED
        elif (
            activity
            == (
                MowerActivity.GOING_OUT
                if self.route.outbound
                else MowerActivity.GOING_HOME
            )
            and state == MowerState.IN_OPERATION
        ):
            self.phase = WireTestPhase.IN_OPERATION
        elif (
            self.phase is WireTestPhase.IN_OPERATION
            and state != MowerState.IN_OPERATION
        ):
            self.phase = WireTestPhase.UNCONFIRMED
        if self.route.outbound and self.phase in (
            WireTestPhase.IN_OPERATION,
            WireTestPhase.DONE,
        ):
            distance = _TEST_DISTANCE.decode(
                await client.command(_TEST_DISTANCE.command)
            )
            if 0 <= distance <= 500:
                self.distance = distance
