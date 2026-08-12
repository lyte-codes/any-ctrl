"""A backend that connects to nothing and records what it was told to do.

Useful for three things: checking a macro without a console in the room,
previewing the exact input timeline a macro produces, and testing the rest of
any-ctrl without hardware.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from anyctrl.backends.base import BackendStatus, Console, ControllerBackend
from anyctrl.controller.state import ControllerState


@dataclass(frozen=True)
class Frame:
    """One recorded controller state and the tick index it was sent on."""

    index: int
    state: ControllerState

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.index:>6} {self.state.describe()}"


class DryRunBackend(ControllerBackend):
    """Record every state instead of sending it anywhere."""

    name = "dryrun"
    description = "Simulate a controller; record inputs instead of sending them"

    def __init__(
        self,
        *,
        console: Console = Console.SWITCH1,
        sink: Callable[[Frame], None] | None = None,
        keep_frames: bool = True,
    ) -> None:
        super().__init__(console=console)
        self.sink = sink
        self.keep_frames = keep_frames
        self.frames: list[Frame] = []
        self.transitions: list[Frame] = []
        self._count = 0
        self._last: str | None = None

    def connect(self, *, timeout: float | None = None) -> None:
        self._connected = True

    def send_state(self, state: ControllerState) -> None:
        frame = Frame(index=self._count, state=state.copy())
        self._count += 1
        if self.keep_frames:
            self.frames.append(frame)
        description = frame.state.describe()
        if description != self._last:
            self._last = description
            self.transitions.append(frame)
            if self.sink is not None:
                self.sink(frame)

    def close(self) -> None:
        self._connected = False

    @classmethod
    def status(cls) -> BackendStatus:
        return BackendStatus(cls.name, cls.description, True, "always available")
