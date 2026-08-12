"""Syntax tree and compiled instruction types for the macro language.

The parser produces :class:`Statement` nodes that still mention ``call`` and
``loop``; the compiler lowers them into :class:`Op` instructions that the player
executes against a backend. Loops survive compilation so that ``loop forever``
does not have to be unrolled.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from anyctrl.controller.buttons import Button, Dpad, Stick
from anyctrl.controller.state import StickState

# ---------------------------------------------------------------------------
# Syntax tree
# ---------------------------------------------------------------------------


@dataclass
class Statement:
    """Base class carrying the source location used in error messages."""

    line: int = field(default=0, kw_only=True)
    source: str | None = field(default=None, kw_only=True)


@dataclass
class Press(Statement):
    """Press a combination, hold it briefly, then release it."""

    buttons: frozenset[Button]
    duration: float | None = None
    repeat: int = 1
    interval: float | None = None


@dataclass
class Hold(Statement):
    """Press a combination and leave it held."""

    buttons: frozenset[Button]


@dataclass
class Release(Statement):
    """Release a combination, or everything when ``buttons`` is ``None``."""

    buttons: frozenset[Button] | None = None


@dataclass
class Wait(Statement):
    seconds: float = 0.0


@dataclass
class StickMove(Statement):
    """Move a stick; with a duration, recentre it afterwards."""

    stick: Stick = Stick.LEFT
    position: StickState = field(default_factory=StickState.centered)
    duration: float | None = None


@dataclass
class DpadMove(Statement):
    direction: Dpad = Dpad.NONE
    duration: float | None = None


@dataclass
class Loop(Statement):
    """Repeat a body ``count`` times, or forever when ``count`` is ``None``."""

    count: int | None = None
    body: list[Statement] = field(default_factory=list)


@dataclass
class Call(Statement):
    name: str = ""


@dataclass
class Log(Statement):
    message: str = ""


@dataclass
class SetOption(Statement):
    """Change a playback default (``press_time``, ``gap``) mid macro."""

    name: str = ""
    value: float = 0.0


@dataclass
class MacroFile:
    """A parsed macro: its statements, reusable blocks and metadata."""

    statements: list[Statement] = field(default_factory=list)
    definitions: dict[str, list[Statement]] = field(default_factory=dict)
    meta: dict[str, str] = field(default_factory=dict)
    source: str | None = None


# ---------------------------------------------------------------------------
# Compiled instructions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Op:
    """Base class for compiled instructions."""


@dataclass(frozen=True)
class SetButtons(Op):
    """Press ``down`` and release ``up`` in a single controller update."""

    down: frozenset[Button] = frozenset()
    up: frozenset[Button] = frozenset()


@dataclass(frozen=True)
class SetStick(Op):
    stick: Stick = Stick.LEFT
    position: StickState = field(default_factory=StickState.centered)


@dataclass(frozen=True)
class SetDpad(Op):
    direction: Dpad = Dpad.NONE


@dataclass(frozen=True)
class ReleaseAll(Op):
    pass


@dataclass(frozen=True)
class Sleep(Op):
    """Hold the current state for ``seconds`` while reports keep flowing."""

    seconds: float = 0.0


@dataclass(frozen=True)
class Emit(Op):
    """Emit a message to the player's listener."""

    message: str = ""


@dataclass(frozen=True)
class Repeat(Op):
    """Run ``body`` ``count`` times, or forever when ``count`` is ``None``."""

    count: int | None = None
    body: tuple[Op, ...] = ()


@dataclass(frozen=True)
class CompiledMacro:
    """A ready to play program plus the metadata that shaped it."""

    ops: tuple[Op, ...] = ()
    meta: dict[str, str] = field(default_factory=dict)
    source: str | None = None

    @property
    def name(self) -> str:
        return self.meta.get("name") or (self.source or "macro")

    @property
    def is_endless(self) -> bool:
        return _contains_endless(self.ops)

    def duration(self) -> float:
        """Total playback time in seconds; ``inf`` if the macro never ends."""
        return _duration(self.ops)


def _contains_endless(ops: tuple[Op, ...] | list[Op]) -> bool:
    for op in ops:
        if isinstance(op, Repeat):
            if op.count is None or _contains_endless(op.body):
                return True
    return False


def _duration(ops: tuple[Op, ...] | list[Op]) -> float:
    total = 0.0
    for op in ops:
        if isinstance(op, Sleep):
            total += op.seconds
        elif isinstance(op, Repeat):
            if op.count is None:
                return float("inf")
            inner = _duration(op.body)
            if inner == float("inf"):
                return inner
            total += inner * op.count
    return total
