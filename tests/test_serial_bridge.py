"""The serial bridge backend, driven against a fake serial port."""

from __future__ import annotations

import sys
import types

import pytest

from anyctrl.backends.serial_bridge import (
    MSG_BANNER,
    MSG_HELLO,
    MSG_LOG,
    MSG_RESET,
    MSG_STATE,
    SerialBridgeBackend,
    SerialPort,
    decode_frames,
    encode_frame,
    list_ports,
)
from anyctrl.controller.buttons import Button
from anyctrl.controller.state import ControllerState
from anyctrl.errors import BackendError


class FakeSerial:
    """Just enough of ``serial.Serial`` to exercise the backend."""

    def __init__(self, port, baud, timeout=0, write_timeout=None, banner=b"anyctrl-bridge/1 fake"):
        self.port = port
        self.baud = baud
        self.closed = False
        self.written = bytearray()
        self._to_host = bytearray()
        self._banner = banner

    # -- the bits the backend uses ---------------------------------------
    @property
    def in_waiting(self) -> int:
        return len(self._to_host)

    def read(self, count: int) -> bytes:
        chunk = bytes(self._to_host[:count])
        del self._to_host[:count]
        return chunk

    def write(self, data: bytes) -> int:
        self.written += data
        for message_type, _payload in decode_frames(bytearray(data)):
            if message_type == MSG_HELLO and self._banner is not None:
                self._to_host += encode_frame(MSG_BANNER, self._banner)
        return len(data)

    def reset_input_buffer(self) -> None:
        self._to_host.clear()

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    # -- test helpers ----------------------------------------------------
    def push(self, message_type: int, payload: bytes = b"") -> None:
        self._to_host += encode_frame(message_type, payload)

    def sent_frames(self) -> list[tuple[int, bytes]]:
        return decode_frames(bytearray(self.written))


@pytest.fixture()
def fake_serial(monkeypatch):
    """Install a fake ``serial`` module and hand back the ports it opened."""
    opened: list[FakeSerial] = []
    banner = {"value": b"anyctrl-bridge/1 fake"}

    def factory(port, baud, timeout=0, write_timeout=None):
        device = FakeSerial(port, baud, banner=banner["value"])
        opened.append(device)
        return device

    module = types.ModuleType("serial")
    module.Serial = factory
    monkeypatch.setitem(sys.modules, "serial", module)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    return types.SimpleNamespace(opened=opened, banner=banner)


def connected_backend(**kwargs) -> SerialBridgeBackend:
    backend = SerialBridgeBackend(port="/dev/fake", **kwargs)
    backend.connect()
    return backend


def test_connect_greets_the_bridge_and_reads_its_banner(fake_serial):
    backend = connected_backend()
    assert backend.connected
    assert backend.banner == "anyctrl-bridge/1 fake"
    assert fake_serial.opened[0].sent_frames()[0][0] == MSG_HELLO
    backend.close()


def test_connect_rejects_a_stranger_on_the_port(fake_serial):
    fake_serial.banner["value"] = b"some other device"
    with pytest.raises(BackendError, match="unexpected bridge banner"):
        connected_backend()


def test_connect_reports_silence(fake_serial):
    fake_serial.banner["value"] = None
    with pytest.raises(BackendError, match="no response from bridge"):
        connected_backend(handshake_timeout=0.2)


def test_states_are_sent_as_bridge_reports(fake_serial):
    backend = connected_backend()
    backend.send_state(ControllerState(buttons={Button.A}))
    states = [payload for kind, payload in fake_serial.opened[0].sent_frames() if kind == MSG_STATE]
    assert states == [ControllerState(buttons={Button.A}).usb_report()]
    backend.close()


def test_unchanged_states_are_not_resent(fake_serial):
    backend = connected_backend()
    state = ControllerState(buttons={Button.B})
    for _ in range(10):
        backend.send_state(state)
    states = [kind for kind, _ in fake_serial.opened[0].sent_frames() if kind == MSG_STATE]
    assert len(states) == 1
    backend.close()


def test_changed_states_are_always_sent(fake_serial):
    backend = connected_backend()
    for button in (Button.A, Button.B, Button.X):
        backend.send_state(ControllerState(buttons={button}))
    states = [kind for kind, _ in fake_serial.opened[0].sent_frames() if kind == MSG_STATE]
    assert len(states) == 3
    backend.close()


def test_close_neutralises_the_bridge(fake_serial):
    backend = connected_backend()
    backend.send_state(ControllerState(buttons={Button.ZR}))
    backend.close()
    device = fake_serial.opened[0]
    assert device.closed
    assert device.sent_frames()[-1][0] == MSG_RESET
    assert not backend.connected


def test_log_frames_from_the_firmware_are_collected(fake_serial):
    backend = connected_backend()
    fake_serial.opened[0].push(MSG_LOG, b"bad state length")
    backend.send_state(ControllerState(buttons={Button.Y}))
    assert backend.log_lines == ["bad state length"]
    backend.close()


def test_write_failures_surface_as_backend_errors(fake_serial):
    backend = connected_backend()

    def explode(_data):
        raise OSError("port went away")

    fake_serial.opened[0].write = explode
    with pytest.raises(BackendError, match="serial write failed"):
        backend.send_state(ControllerState(buttons={Button.A}))


def test_missing_pyserial_is_explained(monkeypatch):
    monkeypatch.setitem(sys.modules, "serial", None)
    backend = SerialBridgeBackend(port="/dev/fake")
    with pytest.raises(BackendError, match="pyserial is not installed"):
        backend.connect()


def test_known_boards_are_recognised():
    leonardo = SerialPort("/dev/ttyACM0", "Arduino", vid=0x2341, pid=0x8036)
    unknown = SerialPort("/dev/ttyUSB9", "Serial adapter", vid=0x1234, pid=0x5678)
    assert leonardo.known_board == "Arduino Leonardo"
    assert unknown.known_board is None
    assert "Arduino Leonardo" in str(leonardo)


# -- port classification and selection --------------------------------------


def macos_builtin_ports() -> list[SerialPort]:
    """The ports a stock Mac offers with nothing plugged in."""
    return [
        SerialPort("/dev/cu.Bluetooth-Incoming-Port", "n/a"),
        SerialPort("/dev/cu.debug-console", "n/a"),
        SerialPort("/dev/cu.IDW28BT", "n/a"),
    ]


def test_builtin_ports_are_never_bridge_candidates():
    for port in macos_builtin_ports():
        assert not port.is_candidate
    assert SerialPort("/dev/cu.Bluetooth-Incoming-Port", "n/a").is_builtin


def test_a_usb_serial_adapter_is_a_candidate():
    # The host sees the adapter, not the board: the board's USB goes to the
    # console in the normal wiring.
    adapter = SerialPort("/dev/cu.usbserial-1420", "USB Serial", vid=0x1A86, pid=0x7523)
    assert adapter.is_candidate
    assert adapter.known_device == ("CH340 serial adapter", "adapter")


def test_a_board_plugged_into_the_host_is_also_a_candidate():
    board = SerialPort("/dev/cu.usbmodem1401", "Pro Micro", vid=0x1B4F, pid=0x9205)
    assert board.is_candidate
    assert board.known_device == ("SparkFun Pro Micro 5V", "board")


def test_autodetect_refuses_to_guess_at_unrecognised_ports(monkeypatch, fake_serial):
    monkeypatch.setattr("anyctrl.backends.serial_bridge.list_ports", macos_builtin_ports)
    backend = SerialBridgeBackend()
    with pytest.raises(BackendError, match="no bridge adapter recognised"):
        backend.connect()


def test_autodetect_reports_when_there_are_no_ports_at_all(monkeypatch, fake_serial):
    monkeypatch.setattr("anyctrl.backends.serial_bridge.list_ports", list)
    backend = SerialBridgeBackend()
    with pytest.raises(BackendError, match="no serial ports found"):
        backend.connect()


def test_autodetect_picks_the_adapter_over_the_noise(monkeypatch, fake_serial):
    adapter = SerialPort("/dev/cu.usbserial-1420", "USB Serial", vid=0x1A86, pid=0x7523)
    monkeypatch.setattr(
        "anyctrl.backends.serial_bridge.list_ports",
        lambda: [*macos_builtin_ports(), adapter],
    )
    backend = SerialBridgeBackend()
    backend.connect()
    assert backend.port == adapter.device
    backend.close()


def test_status_is_honest_when_no_bridge_is_attached(monkeypatch, fake_serial):
    monkeypatch.setattr("anyctrl.backends.serial_bridge.list_ports", macos_builtin_ports)
    status = SerialBridgeBackend.status()
    assert not status.available
    assert "none is a bridge adapter" in status.detail


def test_status_reports_ready_once_an_adapter_appears(monkeypatch, fake_serial):
    adapter = SerialPort("/dev/cu.usbserial-1420", "USB Serial", vid=0x10C4, pid=0xEA60)
    monkeypatch.setattr("anyctrl.backends.serial_bridge.list_ports", lambda: [adapter])
    status = SerialBridgeBackend.status()
    assert status.available
    assert "CP2102" in status.detail


def test_ports_are_ranked_adapter_board_unknown_builtin(monkeypatch):
    adapter = SerialPort("/dev/cu.usbserial-1", "adapter", vid=0x0403, pid=0x6001)
    board = SerialPort("/dev/cu.usbmodem-2", "board", vid=0x2341, pid=0x8036)
    unknown = SerialPort("/dev/cu.mystery-3", "mystery")
    builtin = SerialPort("/dev/cu.Bluetooth-Incoming-Port", "n/a")

    class FakeComPort:
        def __init__(self, port):
            self.device = port.device
            self.description = port.description
            self.vid = port.vid
            self.pid = port.pid

    scrambled = [builtin, unknown, board, adapter]
    module = types.ModuleType("serial.tools.list_ports")
    module.comports = lambda: [FakeComPort(port) for port in scrambled]
    monkeypatch.setitem(sys.modules, "serial", types.ModuleType("serial"))
    monkeypatch.setitem(sys.modules, "serial.tools", types.ModuleType("serial.tools"))
    monkeypatch.setitem(sys.modules, "serial.tools.list_ports", module)

    ordered = [port.device for port in list_ports()]
    assert ordered == [adapter.device, board.device, unknown.device, builtin.device]
