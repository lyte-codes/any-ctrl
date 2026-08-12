"""Execute a compiled macro against a backend on a steady timing tick.

Timing matters more than it looks. The console expects a controller to report
at a fixed cadence, and a macro that drifts a few milliseconds per press is a
macro that desynchronises after a few thousand iterations. The player therefore
keeps a *virtual* clock: every wait advances virtual time by exactly the amount
the macro asked for, and real sleeps are computed against the start of
playback, so scheduling jitter never accumulates.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from anyctrl.backends.base import ControllerBackend
from anyctrl.controller.state import ControllerState
from anyctrl.errors import MacroAborted
from anyctrl.macro.model import (
    CompiledMacro,
    Emit,
    Op,
    ReleaseAll,
    Repeat,
    SetButtons,
    SetDpad,
    SetStick,
    Sleep,
)

#: Real controllers report about every 15 ms; matching that keeps macro timing
#: and the console's view of the controller in step.
DEFAULT_TICK = 0.015


@dataclass(frozen=True)
class PlaybackEvent:
    """Something worth telling the caller about during playback."""

    kind: str  # "start" | "state" | "log" | "loop" | "finish"
    elapsed: float
    message: str = ""
    state: ControllerState | None = None


Listener = Callable[[PlaybackEvent], None]


class MacroPlayer:
    """Walk a :class:`CompiledMacro`, driving ``backend`` as it goes."""

    def __init__(
        self,
        backend: ControllerBackend,
        *,
        tick: float = DEFAULT_TICK,
        speed: float = 1.0,
        listener: Listener | None = None,
        cancel: threading.Event | None = None,
        timeout: float | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.perf_counter,
    ) -> None:
        if tick <= 0:
            raise ValueError("tick must be positive")
        if speed <= 0:
            raise ValueError("speed must be positive")
        self.backend = backend
        self.tick = tick
        self.speed = speed
        self.listener = listener
        self.cancel = cancel or threading.Event()
        self.timeout = timeout
        self._sleep = sleeper
        self._monotonic = monotonic

        self.state = ControllerState()
        self._virtual = 0.0
        self._start = 0.0

    # -- public API -------------------------------------------------------
    @property
    def elapsed(self) -> float:
        """Virtual playback time in seconds."""
        return self._virtual

    def play(self, macro: CompiledMacro, *, repeat: int = 1) -> float:
        """Play ``macro`` ``repeat`` times; returns the virtual time spent.

        The controller is always returned to a neutral state on the way out,
        including when playback is cancelled or a backend raises, so a macro
        that fails half way through never leaves a button stuck down.
        """
        self._virtual = 0.0
        self._start = self._monotonic()
        self._notify("start", message=macro.name)
        try:
            for iteration in range(repeat):
                if repeat > 1:
                    self._notify("loop", message=f"pass {iteration + 1}/{repeat}")
                self._run(macro.ops)
        finally:
            self._neutralise()
            self._notify("finish")
        return self._virtual

    # -- execution --------------------------------------------------------
    def _run(self, ops: tuple[Op, ...]) -> None:
        for op in ops:
            self._check_cancelled()
            if isinstance(op, SetButtons):
                if op.down:
                    self.state.press(op.down)
                if op.up:
                    self.state.release(op.up)
                self._flush()
            elif isinstance(op, SetStick):
                self.state.set_stick(op.stick, op.position)
                self._flush()
            elif isinstance(op, SetDpad):
                self.state.set_dpad(op.direction)
                self._flush()
            elif isinstance(op, ReleaseAll):
                self.state.release_all()
                self._flush()
            elif isinstance(op, Sleep):
                self._hold(op.seconds)
            elif isinstance(op, Emit):
                self._notify("log", message=op.message)
            elif isinstance(op, Repeat):
                self._run_loop(op)
            else:  # pragma: no cover - defensive
                raise TypeError(f"unknown op {op!r}")

    def _run_loop(self, op: Repeat) -> None:
        if op.count is None:
            while True:
                self._check_cancelled()
                self._run(op.body)
        for _ in range(op.count):
            self._run(op.body)

    def _hold(self, seconds: float) -> None:
        """Keep the current state for ``seconds`` of macro time."""
        target = self._virtual + seconds / self.speed
        while self._virtual < target - 1e-9:
            self._check_cancelled()
            self._flush()
            self._virtual = min(target, self._virtual + self.tick)
            self._sleep_until(self._start + self._virtual)

    def _sleep_until(self, deadline: float) -> None:
        remaining = deadline - self._monotonic()
        if remaining > 0:
            self._sleep(remaining)

    def _flush(self) -> None:
        self.backend.send_state(self.state)
        if self.listener is not None:
            self._notify("state", state=self.state.copy())

    def _neutralise(self) -> None:
        """Release everything, and give the backend a moment to send it."""
        self.state.release_all()
        try:
            self.backend.send_state(self.state)
        except Exception:  # pragma: no cover - the caller is already unwinding
            return

    def _check_cancelled(self) -> None:
        if self.cancel.is_set():
            raise MacroAborted("playback cancelled")
        if self.timeout is not None and self._virtual >= self.timeout:
            raise MacroAborted(f"playback exceeded {self.timeout:g}s")

    def _notify(
        self, kind: str, *, message: str = "", state: ControllerState | None = None
    ) -> None:
        if self.listener is None:
            return
        self.listener(PlaybackEvent(kind=kind, elapsed=self._virtual, message=message, state=state))
