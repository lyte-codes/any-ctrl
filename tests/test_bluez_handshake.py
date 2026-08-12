"""Exercise the BlueZ backend's protocol loops over a plain socket pair.

L2CAP sockets and a real console are not available in a test run, but every
byte-level decision the backend makes — stripping HID headers, answering
subcommands, streaming reports, replying on the control channel — happens above
the socket. Swapping in a socket pair therefore tests all of it.
"""

from __future__ import annotations

import socket
import threading
import time

import pytest

from anyctrl.backends.bluez import (
    HID_DATA_INPUT,
    HID_DATA_OUTPUT,
    HID_VIRTUAL_CABLE_UNPLUG,
    BluezBackend,
)
from anyctrl.controller.buttons import Button
from anyctrl.controller.protocol import ProControllerProtocol
from anyctrl.controller.state import ControllerState
from anyctrl.errors import BackendError

ADDRESS = "11:22:33:44:55:66"


@pytest.fixture()
def pair():
    """A connected socket pair standing in for an L2CAP channel."""
    console, controller = socket.socketpair()
    try:
        yield console, controller
    finally:
        console.close()
        controller.close()


@pytest.fixture()
def backend():
    """A backend wired to nothing, ready to have sockets injected."""
    instance = BluezBackend()
    instance.protocol = ProControllerProtocol(ADDRESS)
    yield instance
    instance._stop.set()


def subcommand(subcommand_id: int, args: bytes = b"") -> bytes:
    """An output report as the console sends it, HID header included."""
    return bytes([HID_DATA_OUTPUT, 0x01, 0x00, *([0x00] * 8), subcommand_id, *args])


def read_report(sock: socket.socket, timeout: float = 2.0) -> bytes:
    sock.settimeout(timeout)
    return sock.recv(512)


def test_input_reports_carry_the_hid_data_header(backend, pair):
    console, controller = pair
    backend._interrupt = controller
    backend._send_report(backend.protocol.input_report())
    data = read_report(console)
    assert data[0] == HID_DATA_INPUT
    assert data[1] == 0x3F  # simple HID mode until the console asks for more


def test_output_report_headers_are_stripped(backend):
    replies = backend._process_input(subcommand(0x02))
    assert replies and replies[0][0] == 0x21
    assert replies[0][14] == 0x02


def test_handshake_completes_when_the_console_assigns_a_player(backend, pair):
    console, controller = pair
    backend._interrupt = controller
    error: list[BaseException] = []

    def run_handshake():
        try:
            backend._handshake(time.monotonic() + 5.0)
        except BaseException as exc:  # pragma: no cover - surfaced below
            error.append(exc)

    thread = threading.Thread(target=run_handshake)
    thread.start()
    try:
        # The console drives the usual sequence, draining our reports as it goes.
        for request, args in (
            (0x02, b""),
            (0x10, bytes([0x3D, 0x60, 0x00, 0x00, 18])),
            (0x03, b"\x30"),
            (0x30, b"\x01"),
        ):
            console.sendall(subcommand(request, args))
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                report = read_report(console)
                if report[1] == 0x21 and report[15] == request:
                    break
            else:  # pragma: no cover - only on a very slow machine
                pytest.fail(f"no reply to subcommand 0x{request:02x}")
        thread.join(timeout=5.0)
    finally:
        backend._stop.set()

    assert not error, error
    assert not thread.is_alive()
    assert backend.protocol.ready
    assert backend.protocol.player_number == 1


def test_handshake_reports_a_console_that_hangs_up(backend, pair):
    console, controller = pair
    backend._interrupt = controller
    console.close()
    with pytest.raises(BackendError, match="closed the connection"):
        backend._handshake(time.monotonic() + 2.0)


def test_handshake_times_out_with_a_useful_message(backend, pair):
    console, controller = pair
    backend._interrupt = controller
    with pytest.raises(BackendError, match="never finished setting the controller up"):
        backend._handshake(time.monotonic() + 0.2)


def test_sender_thread_streams_the_current_state(backend, pair):
    console, controller = pair
    backend._interrupt = controller
    backend._connected = True
    backend.send_state(ControllerState(buttons={Button.A}))

    thread = threading.Thread(target=backend._sender_loop, daemon=True)
    thread.start()
    try:
        report = read_report(console)
        assert report[0] == HID_DATA_INPUT
        assert report[4] == 0x08  # A, in the first button byte
    finally:
        backend._stop.set()
        thread.join(timeout=2.0)


def test_control_channel_answers_get_report(backend, pair):
    console, controller = pair
    backend._control = controller
    backend._answer_control(bytes([0x40, 0x30]))  # GET_REPORT
    reply = read_report(console)
    assert reply[0] == HID_DATA_INPUT


def test_control_channel_acknowledges_set_protocol(backend, pair):
    console, controller = pair
    backend._control = controller
    backend._answer_control(bytes([0x70]))  # SET_PROTOCOL
    assert read_report(console) == b"\x00"


def test_virtual_cable_unplug_ends_the_session(backend, pair):
    console, controller = pair
    backend._control = controller
    backend._connected = True
    backend._answer_control(bytes([HID_VIRTUAL_CABLE_UNPLUG]))
    assert not backend.connected
    with pytest.raises(BackendError, match="unplugged"):
        backend.send_state(ControllerState())


def test_send_state_refuses_when_disconnected(backend):
    with pytest.raises(BackendError, match="not connected"):
        backend.send_state(ControllerState())


def test_close_is_safe_to_call_twice(backend):
    backend.close()
    backend.close()
    assert not backend.connected
