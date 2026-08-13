"""Backend interface plus the console profiles that tune timing."""

from __future__ import annotations

import abc
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from anyctrl.controller.state import ControllerState


class Console(str, Enum):
    """Which console we are driving.

    The wire protocol is the same for both generations — a Switch 2 accepts an
    original Pro Controller — but the newer console is noticeably slower to
    finish binding a controller, and ignores input for a moment afterwards.
    """

    SWITCH1 = "switch1"
    SWITCH2 = "switch2"

    def __str__(self) -> str:  # pragma: no cover - display only
        return self.value


@dataclass(frozen=True)
class ConsoleProfile:
    """Timing knobs that differ between console generations."""

    console: Console
    #: Seconds between controller reports.
    tick: float = 0.015
    #: Quiet time after the handshake before the first macro input.
    settle: float = 1.0
    #: How long to wait for a console to accept the controller.
    pair_timeout: float = 120.0

    @classmethod
    def for_console(cls, console: Console) -> ConsoleProfile:
        if console is Console.SWITCH2:
            # The Switch 2 keeps talking to a freshly bound controller for a
            # while after the handshake looks finished; pressing buttons too
            # early is the most common cause of a "nothing happened" run.
            return cls(console=console, tick=0.015, settle=2.5, pair_timeout=180.0)
        return cls(console=console, tick=0.015, settle=1.0, pair_timeout=120.0)


@dataclass(frozen=True)
class BackendStatus:
    """Whether a backend can run here, and why not when it cannot."""

    name: str
    description: str
    available: bool
    detail: str = ""

    def __str__(self) -> str:  # pragma: no cover - display only
        mark = "ready" if self.available else "unavailable"
        suffix = f" ({self.detail})" if self.detail else ""
        return f"{self.name}: {mark}{suffix}"


class ControllerBackend(abc.ABC):
    """Something that can present itself to a console as a controller."""

    #: Short identifier used on the command line.
    name: str = "backend"
    #: One line description shown by ``anyctrl backends``.
    description: str = ""

    def __init__(self, *, console: Console = Console.SWITCH1) -> None:
        self.console = console
        self.profile = ConsoleProfile.for_console(console)
        self._connected = False
        #: Set by the caller to hear about milestones during a connection:
        #: sockets listening, console attached, handshake finished. Progress a
        #: user is waiting on should be reported when it happens, not guessed
        #: at beforehand.
        self.on_status: Callable[[str], None] | None = None

    def _status(self, message: str) -> None:
        """Report a connection milestone, if anyone is listening."""
        if self.on_status is not None:
            self.on_status(message)

    # -- lifecycle --------------------------------------------------------
    @abc.abstractmethod
    def connect(self, *, timeout: float | None = None) -> None:
        """Establish the connection and complete any handshake."""

    @abc.abstractmethod
    def send_state(self, state: ControllerState) -> None:
        """Publish the current controller state to the console."""

    def close(self) -> None:
        """Release resources. Must be safe to call more than once."""
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def tick(self) -> float:
        """Preferred interval between :meth:`send_state` calls."""
        return self.profile.tick

    # -- introspection ----------------------------------------------------
    @classmethod
    def status(cls) -> BackendStatus:
        """Report whether this backend can be used on this machine."""
        return BackendStatus(cls.name, cls.description, True)

    # -- context manager --------------------------------------------------
    def __enter__(self) -> ControllerBackend:
        if not self.connected:
            self.connect()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __repr__(self) -> str:  # pragma: no cover - display only
        state = "connected" if self.connected else "disconnected"
        return f"<{type(self).__name__} {state} console={self.console}>"
