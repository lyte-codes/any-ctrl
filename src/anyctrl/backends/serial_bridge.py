"""Drive a USB micro-controller that pretends to be a controller.

macOS cannot expose itself as a Bluetooth HID *device*, so on a Mac (and on any
Linux box without a usable Bluetooth adapter) any-ctrl talks over USB serial to
a small board — a Pro Micro, a Leonardo, an RP2040 — which is plugged into the
console's USB port and enumerates as a wired controller. The firmware in
``firmware/`` implements the other half of the framing below.

Wire format, host to device and back::

    0xA5 | type | length | payload[length] | checksum

``checksum`` is the XOR of ``type``, ``length`` and every payload byte. Frames
are short and self-synchronising: a receiver that loses framing simply waits
for the next ``0xA5`` whose checksum agrees.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from anyctrl.backends.base import BackendStatus, Console, ControllerBackend
from anyctrl.controller.state import ControllerState
from anyctrl.errors import BackendError, BackendUnavailable

FRAME_START = 0xA5

# Host -> device.
MSG_HELLO = 0x01
MSG_STATE = 0x02
MSG_RESET = 0x03
MSG_PING = 0x04
# Device -> host.
MSG_BANNER = 0x81
MSG_ACK = 0x82
MSG_PONG = 0x84
MSG_LOG = 0x8F

DEFAULT_BAUD = 115200
BANNER_PREFIX = "anyctrl-bridge"

#: USB IDs of boards known to run the reference firmware, most specific first.
KNOWN_BOARDS: tuple[tuple[int, int, str], ...] = (
    (0x2341, 0x8036, "Arduino Leonardo"),
    (0x2341, 0x8037, "Arduino Micro"),
    (0x1B4F, 0x9205, "SparkFun Pro Micro 5V"),
    (0x1B4F, 0x9206, "SparkFun Pro Micro 3V3"),
    (0x2E8A, 0x000A, "Raspberry Pi Pico"),
    (0x2E8A, 0x0003, "Raspberry Pi Pico (bootloader)"),
    (0x16C0, 0x0483, "Teensy"),
)


@dataclass(frozen=True)
class SerialPort:
    """A candidate serial port found while scanning."""

    device: str
    description: str
    vid: int | None = None
    pid: int | None = None

    @property
    def known_board(self) -> str | None:
        for vid, pid, name in KNOWN_BOARDS:
            if self.vid == vid and self.pid == pid:
                return name
        return None

    def __str__(self) -> str:  # pragma: no cover - display only
        board = self.known_board
        suffix = f" [{board}]" if board else ""
        return f"{self.device} - {self.description}{suffix}"


def encode_frame(message_type: int, payload: bytes = b"") -> bytes:
    """Build one framed message."""
    if len(payload) > 255:
        raise ValueError("payload too long for a bridge frame")
    checksum = message_type ^ len(payload)
    for byte in payload:
        checksum ^= byte
    return bytes((FRAME_START, message_type, len(payload), *payload, checksum))


def decode_frames(buffer: bytearray) -> list[tuple[int, bytes]]:
    """Pull every complete frame out of ``buffer``, leaving partial data behind."""
    frames: list[tuple[int, bytes]] = []
    while True:
        start = buffer.find(FRAME_START)
        if start < 0:
            buffer.clear()
            return frames
        if start:
            del buffer[:start]
        if len(buffer) < 4:
            return frames
        message_type = buffer[1]
        length = buffer[2]
        if len(buffer) < 4 + length:
            return frames
        payload = bytes(buffer[3 : 3 + length])
        checksum = buffer[3 + length]
        expected = message_type ^ length
        for byte in payload:
            expected ^= byte
        if checksum == expected:
            frames.append((message_type, payload))
            del buffer[: 4 + length]
        else:
            # Bad checksum: drop the start byte and resynchronise.
            del buffer[:1]


def list_ports() -> list[SerialPort]:
    """List serial ports, best candidates for a bridge first."""
    try:
        from serial.tools import list_ports as _list_ports
    except ImportError:  # pragma: no cover - depends on optional dependency
        return []
    ports = [
        SerialPort(
            device=port.device,
            description=port.description or "",
            vid=port.vid,
            pid=port.pid,
        )
        for port in _list_ports.comports()
    ]
    ports.sort(key=lambda port: (port.known_board is None, port.device))
    return ports


class SerialBridgeBackend(ControllerBackend):
    """Send controller states to a USB bridge board over a serial port."""

    name = "serial"
    description = "Drive a USB micro-controller bridge (works on macOS and Linux)"

    #: Resend an unchanged state at least this often so the firmware watchdog,
    #: which neutralises the controller when the host goes quiet, stays happy.
    KEEPALIVE = 0.25

    def __init__(
        self,
        *,
        console: Console = Console.SWITCH1,
        port: str | None = None,
        baud: int = DEFAULT_BAUD,
        handshake_timeout: float = 3.0,
        require_banner: bool = True,
    ) -> None:
        super().__init__(console=console)
        self.port = port
        self.baud = baud
        self.handshake_timeout = handshake_timeout
        self.require_banner = require_banner
        self.banner: str = ""
        self.log_lines: list[str] = []
        self._serial = None
        self._rx = bytearray()
        self._last_report: bytes | None = None
        self._last_sent = 0.0

    # -- lifecycle --------------------------------------------------------
    def connect(self, *, timeout: float | None = None) -> None:
        try:
            import serial
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise BackendUnavailable(
                "pyserial is not installed; run: pip install 'any-ctrl[serial]'"
            ) from exc

        device = self.port or self._autodetect()
        try:
            self._serial = serial.Serial(device, self.baud, timeout=0, write_timeout=2.0)
        except Exception as exc:  # serial.SerialException and friends
            raise BackendError(f"cannot open serial port {device}: {exc}") from exc
        self.port = device

        # Boards that reset on DTR need a moment before they listen.
        time.sleep(0.2)
        self._serial.reset_input_buffer()
        self._handshake(timeout if timeout is not None else self.handshake_timeout)
        self._connected = True

    def _autodetect(self) -> str:
        ports = list_ports()
        if not ports:
            raise BackendError(
                "no serial ports found; connect the bridge board and pass --port if needed"
            )
        known = [port for port in ports if port.known_board]
        chosen = known[0] if known else ports[0]
        return chosen.device

    def _handshake(self, timeout: float) -> None:
        self._write(encode_frame(MSG_HELLO))
        deadline = time.monotonic() + max(0.1, timeout)
        while time.monotonic() < deadline:
            for message_type, payload in self._poll():
                if message_type == MSG_BANNER:
                    self.banner = payload.decode("ascii", "replace").strip()
                    if self.require_banner and not self.banner.startswith(BANNER_PREFIX):
                        raise BackendError(
                            f"unexpected bridge banner {self.banner!r}; "
                            "is the any-ctrl firmware flashed?"
                        )
                    return
            time.sleep(0.02)
        if self.require_banner:
            raise BackendError(
                f"no response from bridge on {self.port} within {timeout:g}s; "
                "check the firmware and the baud rate"
            )

    def send_state(self, state: ControllerState) -> None:
        report = state.usb_report()
        now = time.monotonic()
        if report == self._last_report and (now - self._last_sent) < self.KEEPALIVE:
            self._poll()  # keep the receive buffer drained
            return
        self._write(encode_frame(MSG_STATE, report))
        self._last_report = report
        self._last_sent = now
        self._poll()

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._write(encode_frame(MSG_RESET))
                self._serial.flush()
            except Exception:
                pass
            try:
                self._serial.close()
            except Exception:  # pragma: no cover - best effort
                pass
            self._serial = None
        self._connected = False

    # -- plumbing ---------------------------------------------------------
    def _write(self, data: bytes) -> None:
        if self._serial is None:
            raise BackendError("serial bridge is not open")
        try:
            self._serial.write(data)
        except Exception as exc:
            raise BackendError(f"serial write failed: {exc}") from exc

    def _poll(self) -> list[tuple[int, bytes]]:
        if self._serial is None:
            return []
        try:
            waiting = self._serial.in_waiting
            if waiting:
                self._rx += self._serial.read(waiting)
        except Exception as exc:
            raise BackendError(f"serial read failed: {exc}") from exc
        frames = decode_frames(self._rx)
        for kind, payload in frames:
            if kind == MSG_LOG:
                self.log_lines.append(payload.decode("ascii", "replace"))
        del self.log_lines[:-50]  # keep the tail, this runs at 60 Hz
        return frames

    # -- introspection ----------------------------------------------------
    @classmethod
    def status(cls) -> BackendStatus:
        try:
            import serial  # noqa: F401
        except ImportError:
            return BackendStatus(
                cls.name,
                cls.description,
                False,
                "pyserial missing: pip install 'any-ctrl[serial]'",
            )
        ports = list_ports()
        known = [port for port in ports if port.known_board]
        if known:
            return BackendStatus(cls.name, cls.description, True, f"bridge candidate {known[0]}")
        if ports:
            return BackendStatus(
                cls.name, cls.description, True, f"{len(ports)} serial port(s), none recognised"
            )
        return BackendStatus(cls.name, cls.description, False, "no serial ports found")
