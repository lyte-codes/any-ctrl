"""The mutable state of the emulated controller and its wire encodings."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from anyctrl.controller.buttons import (
    BUTTON_BITS,
    DPAD_BUTTONS,
    DPAD_HAT,
    USB_BUTTON_BITS,
    Button,
    Dpad,
    Stick,
)

#: Raw stick values are 12 bit. Centre and deflection below must agree with the
#: factory calibration reported from the emulated SPI flash, otherwise the
#: console scales our input against the wrong range.
STICK_CENTER = 2048
STICK_RANGE = 1600


def _clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return low if value < low else high if value > high else value


@dataclass(frozen=True)
class StickState:
    """An analogue stick position in ``[-1, 1]``; ``+y`` points up."""

    x: float = 0.0
    y: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "x", _clamp(float(self.x)))
        object.__setattr__(self, "y", _clamp(float(self.y)))

    @classmethod
    def centered(cls) -> StickState:
        return cls(0.0, 0.0)

    @classmethod
    def from_angle(cls, degrees: float, magnitude: float = 1.0) -> StickState:
        """Build a position from a compass style angle (0 deg = up, clockwise)."""
        magnitude = _clamp(float(magnitude), 0.0, 1.0)
        radians = math.radians(float(degrees))
        return cls(math.sin(radians) * magnitude, math.cos(radians) * magnitude)

    @property
    def is_centered(self) -> bool:
        return self.x == 0.0 and self.y == 0.0

    def to_raw(self) -> tuple[int, int]:
        """Convert to the console's 12 bit raw pair."""
        raw_x = int(round(STICK_CENTER + self.x * STICK_RANGE))
        raw_y = int(round(STICK_CENTER + self.y * STICK_RANGE))
        return (max(0, min(0xFFF, raw_x)), max(0, min(0xFFF, raw_y)))

    def to_byte_pair(self) -> tuple[int, int]:
        """Convert to the 8 bit pair used by the USB bridge (128 = centre)."""
        byte_x = int(round(128 + self.x * 127))
        byte_y = int(round(128 - self.y * 127))  # USB reports use screen axes
        return (max(0, min(255, byte_x)), max(0, min(255, byte_y)))

    def encode3(self) -> bytes:
        """Pack the pair into the 3 byte little endian 12+12 bit form."""
        raw_x, raw_y = self.to_raw()
        return bytes((raw_x & 0xFF, ((raw_x >> 8) & 0x0F) | ((raw_y & 0x0F) << 4), raw_y >> 4))

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"({self.x:+.2f},{self.y:+.2f})"


@dataclass
class ControllerState:
    """Everything the console needs to know about the controller right now."""

    buttons: set[Button] = field(default_factory=set)
    left_stick: StickState = field(default_factory=StickState.centered)
    right_stick: StickState = field(default_factory=StickState.centered)
    #: 8 = full, 6 = medium, 4 = low, 2 = critical, 0 = empty.
    battery: int = 8

    # -- mutation ---------------------------------------------------------
    def press(self, buttons: frozenset[Button] | set[Button] | Button) -> None:
        self.buttons |= _as_set(buttons)

    def release(self, buttons: frozenset[Button] | set[Button] | Button) -> None:
        self.buttons -= _as_set(buttons)

    def release_all(self) -> None:
        self.buttons.clear()
        self.left_stick = StickState.centered()
        self.right_stick = StickState.centered()

    def set_stick(self, stick: Stick, position: StickState) -> None:
        if stick is Stick.LEFT:
            self.left_stick = position
        else:
            self.right_stick = position

    def get_stick(self, stick: Stick) -> StickState:
        return self.left_stick if stick is Stick.LEFT else self.right_stick

    def set_dpad(self, direction: Dpad) -> None:
        self.buttons -= {Button.UP, Button.DOWN, Button.LEFT, Button.RIGHT}
        self.buttons |= set(DPAD_BUTTONS[direction])

    def copy(self) -> ControllerState:
        return replace(self, buttons=set(self.buttons))

    @property
    def is_neutral(self) -> bool:
        return not self.buttons and self.left_stick.is_centered and self.right_stick.is_centered

    # -- encodings --------------------------------------------------------
    def button_bytes(self) -> bytes:
        """The three button bytes of a Bluetooth standard input report."""
        out = [0, 0, 0]
        for button in self.buttons:
            index, mask = BUTTON_BITS[button]
            out[index] |= mask
        return bytes(out)

    def usb_buttons(self) -> int:
        """The 16 bit button field of the USB bridge report."""
        value = 0
        for button in self.buttons:
            value |= USB_BUTTON_BITS.get(button, 0)
        return value

    def dpad(self) -> Dpad:
        """Collapse the four direction buttons into a single HAT direction."""
        up = Button.UP in self.buttons
        down = Button.DOWN in self.buttons
        left = Button.LEFT in self.buttons
        right = Button.RIGHT in self.buttons
        # Opposite directions cancel, matching how real hardware behaves.
        if up and down:
            up = down = False
        if left and right:
            left = right = False
        if up:
            return Dpad.UP_LEFT if left else Dpad.UP_RIGHT if right else Dpad.UP
        if down:
            return Dpad.DOWN_LEFT if left else Dpad.DOWN_RIGHT if right else Dpad.DOWN
        if left:
            return Dpad.LEFT
        if right:
            return Dpad.RIGHT
        return Dpad.NONE

    def hat(self) -> int:
        return DPAD_HAT[self.dpad()]

    def usb_report(self) -> bytes:
        """The 8 byte report understood by the micro-controller bridge."""
        buttons = self.usb_buttons()
        lx, ly = self.left_stick.to_byte_pair()
        rx, ry = self.right_stick.to_byte_pair()
        return bytes((buttons & 0xFF, (buttons >> 8) & 0xFF, self.hat(), lx, ly, rx, ry, 0x00))

    def describe(self) -> str:
        """A compact one line description used by logs and the dry-run backend."""
        pressed = "+".join(sorted(b.value for b in self.buttons)) or "-"
        return f"[{pressed}] L{self.left_stick} R{self.right_stick}"

    def __str__(self) -> str:  # pragma: no cover - display only
        return self.describe()


def _as_set(buttons: frozenset[Button] | set[Button] | Button) -> set[Button]:
    if isinstance(buttons, Button):
        return {buttons}
    return set(buttons)
