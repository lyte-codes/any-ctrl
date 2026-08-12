"""The Pro Controller HID protocol: the reports we send, the ones we answer.

This module is transport agnostic. It turns a :class:`ControllerState` into the
input reports a Nintendo Switch expects, and answers the output reports (mostly
"subcommands") the console sends while it sets the controller up. The Bluetooth
backend feeds bytes in and writes bytes out; nothing here touches a socket, so
the whole handshake is unit testable.

Report reference (payload as seen after the HID ``0xA1`` data header):

===========  ==============================================================
``0x3F``     Simple HID mode. What we send until the console asks for more.
``0x21``     Standard input report carrying a subcommand reply.
``0x30``     Standard full input report at 60 Hz: buttons, sticks and IMU.
===========  ==============================================================
"""

from __future__ import annotations

from collections.abc import Callable
from enum import IntEnum

from anyctrl.controller.buttons import DPAD_HAT
from anyctrl.controller.spi import SpiFlash
from anyctrl.controller.state import ControllerState

#: Length of a standard input report payload (report ID included).
STANDARD_REPORT_LENGTH = 49
SIMPLE_REPORT_LENGTH = 12


class InputReportMode(IntEnum):
    """Input report modes the console can select with subcommand ``0x03``."""

    NFC_IR = 0x31
    STANDARD_FULL = 0x30
    SIMPLE_HID = 0x3F


class DeviceType(IntEnum):
    """Controller kinds. any-ctrl emulates a Pro Controller by default."""

    LEFT_JOYCON = 0x01
    RIGHT_JOYCON = 0x02
    PRO_CONTROLLER = 0x03


def parse_mac(address: str) -> bytes:
    """Turn ``"AA:BB:CC:DD:EE:FF"`` into six bytes."""
    parts = address.replace("-", ":").split(":")
    if len(parts) != 6:
        raise ValueError(f"malformed Bluetooth address: {address!r}")
    return bytes(int(part, 16) for part in parts)


class ProControllerProtocol:
    """Stateful Pro Controller emulation.

    Parameters
    ----------
    address:
        The Bluetooth address the console sees. It is echoed back in the device
        info reply, so it should match the adapter actually in use.
    firmware:
        Reported firmware version. ``(3, 72)`` is a widely accepted value; the
        console does not gate anything we use on it.
    """

    def __init__(
        self,
        address: str = "00:00:00:00:00:00",
        *,
        device_type: DeviceType = DeviceType.PRO_CONTROLLER,
        firmware: tuple[int, int] = (3, 72),
        flash: SpiFlash | None = None,
        on_rumble: Callable[[bytes], None] | None = None,
    ) -> None:
        self.address = address
        self._mac = parse_mac(address)
        self.device_type = device_type
        self.firmware = firmware
        self.flash = flash or SpiFlash()
        self.on_rumble = on_rumble

        self.state = ControllerState()
        self.report_mode: InputReportMode = InputReportMode.SIMPLE_HID
        self.player_number: int | None = None
        self.vibration_enabled = False
        self.imu_enabled = False
        self.last_rumble: bytes = b""
        self.subcommands_seen: list[int] = []
        self._timer = 0

    # -- outgoing ---------------------------------------------------------
    @property
    def ready(self) -> bool:
        """True once the console has finished setting the controller up.

        The console assigns a player LED as the last step of a normal
        handshake, so waiting for both a full report mode and a player number
        is a reliable "you may start pressing buttons now" signal.
        """
        return self.report_mode is InputReportMode.STANDARD_FULL and self.player_number is not None

    def set_state(self, state: ControllerState) -> None:
        """Adopt a snapshot of the controller state for subsequent reports."""
        self.state = state

    def input_report(self) -> bytes:
        """Build the next periodic input report for the current mode."""
        if self.report_mode is InputReportMode.SIMPLE_HID:
            return self._simple_report()
        return self._standard_report()

    def _next_timer(self) -> int:
        # Real hardware advances this roughly 3 units per 15 ms report. The
        # console only uses it to spot dropped packets, so a steady increment
        # matching our own tick rate is enough.
        self._timer = (self._timer + 3) & 0xFF
        return self._timer

    def _common_prefix(self, report_id: int) -> bytearray:
        # Battery in the high nibble; low nibble bit 0 = USB powered, bits 1-2
        # = connection type (3 = Pro Controller / charging grip).
        connection = (self.state.battery << 4) | 0x06
        payload = bytearray((report_id, self._next_timer(), connection))
        payload += self.state.button_bytes()
        payload += self.state.left_stick.encode3()
        payload += self.state.right_stick.encode3()
        payload.append(0x00)  # vibrator input report
        return payload

    def _standard_report(self) -> bytes:
        payload = self._common_prefix(int(self.report_mode))
        payload += self._imu_payload()
        return bytes(payload.ljust(STANDARD_REPORT_LENGTH, b"\x00")[:STANDARD_REPORT_LENGTH])

    def _imu_payload(self) -> bytes:
        """Three 12 byte IMU frames.

        any-ctrl does not simulate motion, so the frames describe a controller
        lying still: one g downwards on the accelerometer, no rotation.
        """
        if not self.imu_enabled:
            return b"\x00" * 36
        frame = b"".join(
            value.to_bytes(2, "little", signed=True) for value in (0, 0, 4096, 0, 0, 0)
        )
        return frame * 3

    def _simple_report(self) -> bytes:
        """The 12 byte report used before the console selects a richer mode."""
        buttons = self.state.usb_buttons()
        payload = bytearray(
            (int(InputReportMode.SIMPLE_HID), buttons & 0xFF, (buttons >> 8) & 0xFF)
        )
        payload.append(DPAD_HAT[self.state.dpad()])
        for stick in (self.state.left_stick, self.state.right_stick):
            byte_x, byte_y = stick.to_byte_pair()
            payload += (byte_x * 257).to_bytes(2, "little")
            payload += (byte_y * 257).to_bytes(2, "little")
        return bytes(payload[:SIMPLE_REPORT_LENGTH])

    def subcommand_reply(self, ack: int, subcommand: int, data: bytes = b"") -> bytes:
        """Build a ``0x21`` report carrying a reply to ``subcommand``."""
        payload = self._common_prefix(0x21)
        payload.append(ack)
        payload.append(subcommand)
        payload += data
        return bytes(payload.ljust(STANDARD_REPORT_LENGTH, b"\x00")[:STANDARD_REPORT_LENGTH])

    # -- incoming ---------------------------------------------------------
    def handle_output_report(self, data: bytes) -> list[bytes]:
        """Process one output report from the console, returning any replies."""
        if not data:
            return []
        report_id = data[0]
        if report_id == 0x01:  # rumble + subcommand
            self._note_rumble(data[2:10])
            if len(data) < 11:
                return []
            return self._handle_subcommand(data[10], data[11:])
        if report_id == 0x10:  # rumble only
            self._note_rumble(data[2:10])
            return []
        if report_id == 0x80:
            # USB-only handshake commands. A console will not normally send
            # these over Bluetooth; ignoring them is what real hardware does.
            return []
        return []

    def _note_rumble(self, payload: bytes) -> None:
        if not payload:
            return
        self.last_rumble = bytes(payload)
        if self.on_rumble is not None:
            self.on_rumble(self.last_rumble)

    def _handle_subcommand(self, subcommand: int, args: bytes) -> list[bytes]:
        self.subcommands_seen.append(subcommand)
        handler = self._SUBCOMMANDS.get(subcommand)
        if handler is None:
            # Unknown subcommands get a plain ACK. Refusing them makes some
            # firmware retry forever, which stalls the handshake.
            return [self.subcommand_reply(0x80, subcommand)]
        return handler(self, subcommand, args)

    # Individual subcommand handlers. Each returns the reports to send back.
    def _sub_request_device_info(self, subcommand: int, args: bytes) -> list[bytes]:
        data = bytearray(
            (
                self.firmware[0],
                self.firmware[1],
                int(self.device_type),
                0x02,  # unknown, always 2 on retail hardware
            )
        )
        data += self._mac[::-1]
        data += bytes((0x01, 0x02))  # 0x01 unknown, 0x02 = colours live in SPI
        return [self.subcommand_reply(0x82, subcommand, bytes(data))]

    def _sub_set_input_report_mode(self, subcommand: int, args: bytes) -> list[bytes]:
        if args:
            try:
                self.report_mode = InputReportMode(args[0])
            except ValueError:
                # Unsupported mode: stay where we are but still acknowledge.
                pass
        return [self.subcommand_reply(0x80, subcommand)]

    def _sub_trigger_buttons_elapsed(self, subcommand: int, args: bytes) -> list[bytes]:
        return [self.subcommand_reply(0x83, subcommand, b"\x00" * 10)]

    def _sub_spi_read(self, subcommand: int, args: bytes) -> list[bytes]:
        if len(args) < 5:
            return [self.subcommand_reply(0x80, subcommand)]
        address = int.from_bytes(args[0:4], "little")
        # A reply has to fit in one report, so long reads are truncated and the
        # echoed length says so.
        length = min(args[4], 0x1D)
        payload = args[0:4] + bytes((length,)) + self.flash.read(address, length)
        return [self.subcommand_reply(0x90, subcommand, payload)]

    def _sub_spi_write(self, subcommand: int, args: bytes) -> list[bytes]:
        if len(args) >= 5:
            address = int.from_bytes(args[0:4], "little")
            length = args[4]
            try:
                self.flash.write(address, args[5 : 5 + length])
            except ValueError:
                return [self.subcommand_reply(0x80, subcommand, b"\x01")]  # write protected
        return [self.subcommand_reply(0x80, subcommand, b"\x00")]

    def _sub_set_player_lights(self, subcommand: int, args: bytes) -> list[bytes]:
        if args:
            self.player_number = _player_from_light_mask(args[0])
        return [self.subcommand_reply(0x80, subcommand)]

    def _sub_get_player_lights(self, subcommand: int, args: bytes) -> list[bytes]:
        mask = 0 if self.player_number is None else (1 << self.player_number) - 1
        return [self.subcommand_reply(0xB0, subcommand, bytes((mask,)))]

    def _sub_enable_imu(self, subcommand: int, args: bytes) -> list[bytes]:
        self.imu_enabled = bool(args and args[0])
        return [self.subcommand_reply(0x80, subcommand)]

    def _sub_enable_vibration(self, subcommand: int, args: bytes) -> list[bytes]:
        self.vibration_enabled = bool(args and args[0])
        return [self.subcommand_reply(0x80, subcommand)]

    def _sub_set_nfc_ir_config(self, subcommand: int, args: bytes) -> list[bytes]:
        data = bytes((0x01, 0x00, 0xFF, 0x00, 0x08, 0x00, 0x1B, 0x01))
        return [self.subcommand_reply(0xA0, subcommand, data)]

    def _sub_manual_pairing(self, subcommand: int, args: bytes) -> list[bytes]:
        # Link level pairing has already happened by the time this arrives; the
        # console just wants each phase acknowledged.
        phase = args[0] if args else 0x01
        return [self.subcommand_reply(0x81, subcommand, bytes((phase, 0x00, 0x03)))]

    def _sub_ack_only(self, subcommand: int, args: bytes) -> list[bytes]:
        return [self.subcommand_reply(0x80, subcommand)]

    _SUBCOMMANDS: dict[int, Callable[[ProControllerProtocol, int, bytes], list[bytes]]] = {
        0x00: _sub_ack_only,  # do nothing
        0x01: _sub_manual_pairing,
        0x02: _sub_request_device_info,
        0x03: _sub_set_input_report_mode,
        0x04: _sub_trigger_buttons_elapsed,
        0x06: _sub_ack_only,  # set HCI state
        0x07: _sub_ack_only,  # reset pairing info
        0x08: _sub_ack_only,  # set shipment low power state
        0x10: _sub_spi_read,
        0x11: _sub_spi_write,
        0x12: _sub_ack_only,  # SPI sector erase
        0x21: _sub_set_nfc_ir_config,
        0x22: _sub_ack_only,  # set NFC/IR state
        0x30: _sub_set_player_lights,
        0x31: _sub_get_player_lights,
        0x38: _sub_ack_only,  # set HOME light
        0x40: _sub_enable_imu,
        0x41: _sub_ack_only,  # IMU sensitivity
        0x48: _sub_enable_vibration,
        0x50: _sub_ack_only,  # get regulated voltage
    }


def _player_from_light_mask(mask: int) -> int:
    """Turn the player LED bit mask into a 1-based player number."""
    lit = mask & 0x0F
    if lit == 0:
        # Only the flashing bits are set: the console is still assigning a slot.
        flashing = (mask >> 4) & 0x0F
        return max(1, flashing.bit_length())
    return lit.bit_length()
