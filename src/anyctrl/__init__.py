"""any-ctrl: play controller macros on a Nintendo Switch from Linux or macOS.

The package is organised in three layers:

``anyctrl.controller``
    A platform independent model of a Nintendo Switch Pro Controller: buttons,
    sticks and the HID reports the console expects to receive.

``anyctrl.macro``
    A small human readable macro language, its parser/compiler and the player
    that walks the compiled program on a fixed timing tick.

``anyctrl.backends``
    The things that actually talk to a console: a BlueZ (Linux) Bluetooth
    backend, a USB micro-controller bridge backend that works anywhere including
    macOS, and a dry-run backend used for testing and previewing macros.
"""

from anyctrl.controller.buttons import Button, Dpad, Stick
from anyctrl.controller.state import ControllerState, StickState
from anyctrl.errors import AnyCtrlError, BackendError, MacroAborted, MacroError

__all__ = [
    "AnyCtrlError",
    "BackendError",
    "Button",
    "ControllerState",
    "Dpad",
    "MacroAborted",
    "MacroError",
    "Stick",
    "StickState",
    "__version__",
]

__version__ = "0.1.0"
