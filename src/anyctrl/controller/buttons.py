"""Buttons, D-pad directions and sticks of a Pro Controller.

The bit positions below are the ones the console expects in the three button
bytes of a standard input report (report ID ``0x30``):

    byte 0 (right)   Y X B A SR SL R  ZR
    byte 1 (shared)  - + R3 L3 Home Capture . Grip
    byte 2 (left)    Down Up Right Left SR SL L ZL
"""

from __future__ import annotations

from enum import Enum

from anyctrl.errors import MacroError


class Button(str, Enum):
    """Every button any-ctrl can press."""

    A = "A"
    B = "B"
    X = "X"
    Y = "Y"
    L = "L"
    R = "R"
    ZL = "ZL"
    ZR = "ZR"
    PLUS = "PLUS"
    MINUS = "MINUS"
    HOME = "HOME"
    CAPTURE = "CAPTURE"
    LSTICK = "LSTICK"
    RSTICK = "RSTICK"
    UP = "UP"
    DOWN = "DOWN"
    LEFT = "LEFT"
    RIGHT = "RIGHT"
    # Joy-Con rail buttons. Harmless on a Pro Controller, kept so recorded
    # sessions from real hardware round-trip cleanly.
    SL_LEFT = "SL_LEFT"
    SR_LEFT = "SR_LEFT"
    SL_RIGHT = "SL_RIGHT"
    SR_RIGHT = "SR_RIGHT"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class Dpad(str, Enum):
    """D-pad positions, including the diagonals the HAT switch supports."""

    NONE = "NONE"
    UP = "UP"
    UP_RIGHT = "UP_RIGHT"
    RIGHT = "RIGHT"
    DOWN_RIGHT = "DOWN_RIGHT"
    DOWN = "DOWN"
    DOWN_LEFT = "DOWN_LEFT"
    LEFT = "LEFT"
    UP_LEFT = "UP_LEFT"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class Stick(str, Enum):
    """The two analogue sticks."""

    LEFT = "L"
    RIGHT = "R"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


#: ``Button`` -> ``(button byte index, bit mask)`` for a standard input report.
BUTTON_BITS: dict[Button, tuple[int, int]] = {
    Button.Y: (0, 0x01),
    Button.X: (0, 0x02),
    Button.B: (0, 0x04),
    Button.A: (0, 0x08),
    Button.SR_RIGHT: (0, 0x10),
    Button.SL_RIGHT: (0, 0x20),
    Button.R: (0, 0x40),
    Button.ZR: (0, 0x80),
    Button.MINUS: (1, 0x01),
    Button.PLUS: (1, 0x02),
    Button.RSTICK: (1, 0x04),
    Button.LSTICK: (1, 0x08),
    Button.HOME: (1, 0x10),
    Button.CAPTURE: (1, 0x20),
    Button.DOWN: (2, 0x01),
    Button.UP: (2, 0x02),
    Button.RIGHT: (2, 0x04),
    Button.LEFT: (2, 0x08),
    Button.SR_LEFT: (2, 0x10),
    Button.SL_LEFT: (2, 0x20),
    Button.L: (2, 0x40),
    Button.ZL: (2, 0x80),
}

#: Bit positions used by the 16 bit button field of the simple USB HID report
#: that the micro-controller bridge speaks.
USB_BUTTON_BITS: dict[Button, int] = {
    Button.Y: 1 << 0,
    Button.B: 1 << 1,
    Button.A: 1 << 2,
    Button.X: 1 << 3,
    Button.L: 1 << 4,
    Button.R: 1 << 5,
    Button.ZL: 1 << 6,
    Button.ZR: 1 << 7,
    Button.MINUS: 1 << 8,
    Button.PLUS: 1 << 9,
    Button.LSTICK: 1 << 10,
    Button.RSTICK: 1 << 11,
    Button.HOME: 1 << 12,
    Button.CAPTURE: 1 << 13,
}

#: D-pad direction -> the HAT switch value used by the USB bridge report.
DPAD_HAT: dict[Dpad, int] = {
    Dpad.UP: 0,
    Dpad.UP_RIGHT: 1,
    Dpad.RIGHT: 2,
    Dpad.DOWN_RIGHT: 3,
    Dpad.DOWN: 4,
    Dpad.DOWN_LEFT: 5,
    Dpad.LEFT: 6,
    Dpad.UP_LEFT: 7,
    Dpad.NONE: 8,
}

#: D-pad direction -> the individual direction buttons that make it up.
DPAD_BUTTONS: dict[Dpad, frozenset[Button]] = {
    Dpad.NONE: frozenset(),
    Dpad.UP: frozenset({Button.UP}),
    Dpad.UP_RIGHT: frozenset({Button.UP, Button.RIGHT}),
    Dpad.RIGHT: frozenset({Button.RIGHT}),
    Dpad.DOWN_RIGHT: frozenset({Button.DOWN, Button.RIGHT}),
    Dpad.DOWN: frozenset({Button.DOWN}),
    Dpad.DOWN_LEFT: frozenset({Button.DOWN, Button.LEFT}),
    Dpad.LEFT: frozenset({Button.LEFT}),
    Dpad.UP_LEFT: frozenset({Button.UP, Button.LEFT}),
}

#: Spellings accepted in macros, on the command line and in recorded sessions.
BUTTON_ALIASES: dict[str, Button] = {
    "+": Button.PLUS,
    "-": Button.MINUS,
    "PLUS": Button.PLUS,
    "MINUS": Button.MINUS,
    "START": Button.PLUS,
    "SELECT": Button.MINUS,
    "L1": Button.L,
    "R1": Button.R,
    "L2": Button.ZL,
    "R2": Button.ZR,
    "L3": Button.LSTICK,
    "R3": Button.RSTICK,
    "LCLICK": Button.LSTICK,
    "RCLICK": Button.RSTICK,
    "L_STICK": Button.LSTICK,
    "R_STICK": Button.RSTICK,
    "LEFTSTICK": Button.LSTICK,
    "RIGHTSTICK": Button.RSTICK,
    "SCREENSHOT": Button.CAPTURE,
    "CAP": Button.CAPTURE,
    "DUP": Button.UP,
    "DDOWN": Button.DOWN,
    "DLEFT": Button.LEFT,
    "DRIGHT": Button.RIGHT,
    "DPAD_UP": Button.UP,
    "DPAD_DOWN": Button.DOWN,
    "DPAD_LEFT": Button.LEFT,
    "DPAD_RIGHT": Button.RIGHT,
    "SL": Button.SL_LEFT,
    "SR": Button.SR_RIGHT,
}

DPAD_ALIASES: dict[str, Dpad] = {
    "NONE": Dpad.NONE,
    "CENTER": Dpad.NONE,
    "CENTRE": Dpad.NONE,
    "NEUTRAL": Dpad.NONE,
    "UPRIGHT": Dpad.UP_RIGHT,
    "UPLEFT": Dpad.UP_LEFT,
    "DOWNRIGHT": Dpad.DOWN_RIGHT,
    "DOWNLEFT": Dpad.DOWN_LEFT,
}

#: Named stick directions, as ``(x, y)`` unit vectors. ``+y`` is up.
STICK_DIRECTIONS: dict[str, tuple[float, float]] = {
    "CENTER": (0.0, 0.0),
    "CENTRE": (0.0, 0.0),
    "NEUTRAL": (0.0, 0.0),
    "UP": (0.0, 1.0),
    "DOWN": (0.0, -1.0),
    "LEFT": (-1.0, 0.0),
    "RIGHT": (1.0, 0.0),
    "UP_LEFT": (-0.7071, 0.7071),
    "UP_RIGHT": (0.7071, 0.7071),
    "DOWN_LEFT": (-0.7071, -0.7071),
    "DOWN_RIGHT": (0.7071, -0.7071),
    "UPLEFT": (-0.7071, 0.7071),
    "UPRIGHT": (0.7071, 0.7071),
    "DOWNLEFT": (-0.7071, -0.7071),
    "DOWNRIGHT": (0.7071, -0.7071),
}

STICK_ALIASES: dict[str, Stick] = {
    "L": Stick.LEFT,
    "R": Stick.RIGHT,
    "LEFT": Stick.LEFT,
    "RIGHT": Stick.RIGHT,
    "LSTICK": Stick.LEFT,
    "RSTICK": Stick.RIGHT,
    "L_STICK": Stick.LEFT,
    "R_STICK": Stick.RIGHT,
}


def parse_button(name: str, *, line: int | None = None, source: str | None = None) -> Button:
    """Resolve one button name. Raises :class:`MacroError` for unknown names."""
    key = name.strip().upper().replace("-", "_") if name.strip() not in {"+", "-"} else name.strip()
    if key in BUTTON_ALIASES:
        return BUTTON_ALIASES[key]
    try:
        return Button(key)
    except ValueError:
        raise MacroError(f"unknown button {name!r}", source=source, line=line) from None


def parse_combo(
    text: str, *, line: int | None = None, source: str | None = None
) -> frozenset[Button]:
    """Resolve ``"A"`` or ``"ZL+ZR+A"`` into a set of buttons."""
    parts = [part for part in text.split("+") if part.strip()]
    # ``+`` is also the name of a button, so "+" and "A++" need care: a lone
    # empty piece between two separators means the PLUS button was meant.
    if text.strip() == "+":
        return frozenset({Button.PLUS})
    if not parts:
        raise MacroError("empty button combination", source=source, line=line)
    buttons = {parse_button(part, line=line, source=source) for part in parts}
    return frozenset(buttons)


def parse_stick(name: str, *, line: int | None = None, source: str | None = None) -> Stick:
    """Resolve ``L``/``left``/``lstick`` into a :class:`Stick`."""
    key = name.strip().upper().replace("-", "_")
    if key in STICK_ALIASES:
        return STICK_ALIASES[key]
    raise MacroError(f"unknown stick {name!r} (use L or R)", source=source, line=line)


def parse_dpad(name: str, *, line: int | None = None, source: str | None = None) -> Dpad:
    """Resolve a D-pad direction name."""
    key = name.strip().upper().replace("-", "_")
    if key in DPAD_ALIASES:
        return DPAD_ALIASES[key]
    try:
        return Dpad(key)
    except ValueError:
        raise MacroError(f"unknown d-pad direction {name!r}", source=source, line=line) from None
