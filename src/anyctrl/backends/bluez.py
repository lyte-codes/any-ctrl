"""Emulate a Pro Controller over Bluetooth using BlueZ (Linux only).

The console talks to a controller over two L2CAP channels: PSM 17 carries HID
control traffic, PSM 19 carries the input and output reports. To be found and
accepted, the adapter has to look like a controller: the right device class,
the right name, and an SDP record advertising the HID profile.

The sequence implemented below is:

1. Reconfigure the local adapter (class, alias, discoverable, pairable).
2. Register a HID SDP record with ``bluetoothd`` over D-Bus.
3. Either wait for the console to connect (first time pairing, done from the
   console's *Change Grip/Order* screen) or connect out to a console that
   already knows us.
4. Answer the console's subcommands until it assigns a player LED, then keep
   sending input reports at a steady 60 Hz from a background thread.

Everything here needs privileges: binding L2CAP PSMs below 4096 requires root,
and ``bluetoothd`` must be running with its input plugin disabled so that it
does not grab those PSMs first. :meth:`BluezBackend.status` explains what is
missing instead of failing deep inside the handshake.
"""

from __future__ import annotations

import os
import platform
import select
import shutil
import socket
import subprocess
import sys
import threading
import time

from anyctrl.backends.base import BackendStatus, Console, ControllerBackend
from anyctrl.controller.protocol import ProControllerProtocol
from anyctrl.controller.state import ControllerState
from anyctrl.errors import BackendError, BackendUnavailable

CONTROL_PSM = 17
INTERRUPT_PSM = 19

#: HID transaction headers we care about (Bluetooth HID profile, section 7.4).
HID_DATA_INPUT = 0xA1
HID_DATA_OUTPUT = 0xA2
HID_HANDSHAKE_SUCCESS = 0x00
HID_SET_REPORT_OUTPUT = 0x52
HID_TRANSACTION_MASK = 0xF0
HID_TRANSACTION_HID_CONTROL = 0x10
HID_TRANSACTION_GET_REPORT = 0x40
HID_TRANSACTION_SET_REPORT = 0x50
HID_TRANSACTION_GET_PROTOCOL = 0x60
HID_TRANSACTION_SET_PROTOCOL = 0x70
#: HID_CONTROL / VIRTUAL_CABLE_UNPLUG: the console is dropping the controller.
HID_VIRTUAL_CABLE_UNPLUG = 0x15

#: Class of device a Pro Controller reports: peripheral / gamepad.
PRO_CONTROLLER_CLASS = 0x002508
PRO_CONTROLLER_NAME = "Pro Controller"

#: HID report descriptor advertised in the SDP record.
#:
#: The Pro Controller's real descriptor is almost entirely vendor defined: the
#: console recognises the controller from SDP and the device class, then speaks
#: the report protocol implemented in :mod:`anyctrl.controller.protocol`. The
#: descriptor below declares exactly the reports any-ctrl uses, with the sizes
#: real hardware uses, rather than being a byte for byte copy of retail
#: firmware. Pass ``report_descriptor=`` to override it with a dump of your own
#: controller if you ever need an exact match.
# fmt: off
DEFAULT_REPORT_DESCRIPTOR = bytes((
    0x05, 0x01,        # Usage Page (Generic Desktop)
    0x09, 0x05,        # Usage (Gamepad)
    0xA1, 0x01,        # Collection (Application)
    0x06, 0x01, 0xFF,  #   Usage Page (Vendor Defined 0xFF01)
    0x85, 0x21,        #   Report ID (0x21) - subcommand replies
    0x09, 0x21,        #   Usage (0x21)
    0x75, 0x08,        #   Report Size (8)
    0x95, 0x30,        #   Report Count (48)
    0x81, 0x02,        #   Input (Data, Variable, Absolute)
    0x85, 0x30,        #   Report ID (0x30) - standard full report
    0x09, 0x30,        #   Usage (0x30)
    0x75, 0x08,        #   Report Size (8)
    0x95, 0x30,        #   Report Count (48)
    0x81, 0x02,        #   Input (Data, Variable, Absolute)
    0x85, 0x3F,        #   Report ID (0x3F) - simple HID report
    0x09, 0x3F,        #   Usage (0x3F)
    0x75, 0x08,        #   Report Size (8)
    0x95, 0x0B,        #   Report Count (11)
    0x81, 0x02,        #   Input (Data, Variable, Absolute)
    0x85, 0x01,        #   Report ID (0x01) - rumble and subcommands
    0x09, 0x01,        #   Usage (0x01)
    0x75, 0x08,        #   Report Size (8)
    0x95, 0x30,        #   Report Count (48)
    0x91, 0x02,        #   Output (Data, Variable, Absolute)
    0x85, 0x10,        #   Report ID (0x10) - rumble only
    0x09, 0x10,        #   Usage (0x10)
    0x75, 0x08,        #   Report Size (8)
    0x95, 0x30,        #   Report Count (48)
    0x91, 0x02,        #   Output (Data, Variable, Absolute)
    0xC0,              # End Collection
))
# fmt: on

_SDP_TEMPLATE = """<?xml version="1.0" encoding="UTF-8" ?>
<record>
  <attribute id="0x0001">
    <sequence><uuid value="0x1124" /></sequence>
  </attribute>
  <attribute id="0x0004">
    <sequence>
      <sequence>
        <uuid value="0x0100" />
        <uint16 value="0x0011" />
      </sequence>
      <sequence><uuid value="0x0011" /></sequence>
    </sequence>
  </attribute>
  <attribute id="0x0005">
    <sequence><uuid value="0x1002" /></sequence>
  </attribute>
  <attribute id="0x0006">
    <sequence>
      <uint16 value="0x656e" />
      <uint16 value="0x006a" />
      <uint16 value="0x0100" />
    </sequence>
  </attribute>
  <attribute id="0x0009">
    <sequence>
      <sequence>
        <uuid value="0x1124" />
        <uint16 value="0x0101" />
      </sequence>
    </sequence>
  </attribute>
  <attribute id="0x000d">
    <sequence>
      <sequence>
        <sequence>
          <uuid value="0x0100" />
          <uint16 value="0x0013" />
        </sequence>
        <sequence><uuid value="0x0011" /></sequence>
      </sequence>
    </sequence>
  </attribute>
  <attribute id="0x0100"><text value="{name}" /></attribute>
  <attribute id="0x0101"><text value="Gamepad" /></attribute>
  <attribute id="0x0102"><text value="Nintendo" /></attribute>
  <attribute id="0x0200"><uint16 value="0x0100" /></attribute>
  <attribute id="0x0201"><uint16 value="0x0111" /></attribute>
  <attribute id="0x0202"><uint8 value="0x08" /></attribute>
  <attribute id="0x0203"><uint8 value="0x21" /></attribute>
  <attribute id="0x0204"><boolean value="false" /></attribute>
  <attribute id="0x0205"><boolean value="true" /></attribute>
  <attribute id="0x0206">
    <sequence>
      <sequence>
        <uint8 value="0x22" />
        <text encoding="hex" value="{descriptor}" />
      </sequence>
    </sequence>
  </attribute>
  <attribute id="0x0207">
    <sequence>
      <sequence>
        <uint16 value="0x0409" />
        <uint16 value="0x0100" />
      </sequence>
    </sequence>
  </attribute>
  <attribute id="0x020b"><uint16 value="0x0100" /></attribute>
  <attribute id="0x020c"><uint16 value="0x0c80" /></attribute>
  <attribute id="0x020d"><boolean value="true" /></attribute>
  <attribute id="0x020e"><boolean value="false" /></attribute>
  <attribute id="0x020f"><uint16 value="0x0640" /></attribute>
  <attribute id="0x0210"><uint16 value="0x0320" /></attribute>
</record>
"""

HID_PROFILE_UUID = "00001124-0000-1000-8000-00805f9b34fb"
PROFILE_PATH = "/org/bluez/anyctrl/profile"


def build_sdp_record(
    name: str = PRO_CONTROLLER_NAME, descriptor: bytes = DEFAULT_REPORT_DESCRIPTOR
) -> str:
    """Render the HID SDP record ``bluetoothd`` will publish for us."""
    return _SDP_TEMPLATE.format(name=name, descriptor=descriptor.hex())


class _AdapterConfig:
    """Local adapter tweaks, applied on connect and undone on close."""

    def __init__(self, adapter: str, name: str = PRO_CONTROLLER_NAME):
        self.adapter = adapter
        self.name = name
        self._previous_alias: str | None = None
        self._applied = False

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _run(command: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(command, capture_output=True, text=True, check=False)

    def _dbus_properties(self):
        import dbus  # imported lazily so the package installs without D-Bus

        bus = dbus.SystemBus()
        path = f"/org/bluez/{self.adapter}"
        obj = bus.get_object("org.bluez", path)
        return dbus.Interface(obj, "org.freedesktop.DBus.Properties")

    def address(self) -> str:
        """The adapter's Bluetooth address."""
        import dbus

        try:
            properties = self._dbus_properties()
            return str(properties.Get("org.bluez.Adapter1", "Address"))
        except dbus.exceptions.DBusException as exc:
            raise BackendError(
                f"cannot read the address of {self.adapter}: {exc}. Is the adapter present?"
            ) from exc

    def apply(self) -> None:
        """Make the adapter look like a Pro Controller."""
        import dbus

        properties = self._dbus_properties()
        try:
            self._previous_alias = str(properties.Get("org.bluez.Adapter1", "Alias"))
            properties.Set("org.bluez.Adapter1", "Alias", dbus.String(self.name))
            properties.Set("org.bluez.Adapter1", "Powered", dbus.Boolean(True))
            properties.Set("org.bluez.Adapter1", "Pairable", dbus.Boolean(True))
            properties.Set("org.bluez.Adapter1", "Discoverable", dbus.Boolean(True))
            properties.Set("org.bluez.Adapter1", "DiscoverableTimeout", dbus.UInt32(0))
        except dbus.exceptions.DBusException as exc:
            raise BackendError(f"cannot configure {self.adapter}: {exc}") from exc
        self._set_device_class()
        self._applied = True

    def _set_device_class(self) -> None:
        """Set the class of device; BlueZ exposes no D-Bus property for it."""
        hciconfig = shutil.which("hciconfig")
        if hciconfig:
            result = self._run([hciconfig, self.adapter, "class", f"0x{PRO_CONTROLLER_CLASS:06x}"])
            if result.returncode == 0:
                return
        btmgmt = shutil.which("btmgmt")
        if btmgmt:
            index = self.adapter.removeprefix("hci")
            major = (PRO_CONTROLLER_CLASS >> 8) & 0x1F
            minor = PRO_CONTROLLER_CLASS & 0xFF
            result = self._run([btmgmt, "--index", index, "class", str(major), str(minor)])
            if result.returncode == 0:
                return
        raise BackendError(
            "cannot set the Bluetooth device class: install bluez-utils (hciconfig) or "
            "bluez-tools (btmgmt). Without it the console will not offer to pair."
        )

    def restore(self) -> None:
        if not self._applied:
            return
        self._applied = False
        try:
            import dbus

            properties = self._dbus_properties()
            if self._previous_alias is not None:
                properties.Set("org.bluez.Adapter1", "Alias", dbus.String(self._previous_alias))
            properties.Set("org.bluez.Adapter1", "Discoverable", dbus.Boolean(False))
        except Exception:  # pragma: no cover - best effort clean up
            pass


class _SdpProfile:
    """Registers the HID service record with ``bluetoothd``."""

    def __init__(self, record: str):
        self.record = record
        self._manager = None

    def register(self) -> None:
        import dbus

        try:
            bus = dbus.SystemBus()
            obj = bus.get_object("org.bluez", "/org/bluez")
            manager = dbus.Interface(obj, "org.bluez.ProfileManager1")
            options = {
                "ServiceRecord": self.record,
                "Role": "server",
                "RequireAuthentication": dbus.Boolean(False),
                "RequireAuthorization": dbus.Boolean(False),
                "AutoConnect": dbus.Boolean(True),
                "Name": PRO_CONTROLLER_NAME,
            }
            manager.RegisterProfile(PROFILE_PATH, HID_PROFILE_UUID, options)
            self._manager = manager
        except dbus.exceptions.DBusException as exc:
            if "AlreadyExists" in str(exc):
                return
            raise BackendError(
                f"cannot register the HID SDP record: {exc}. "
                "Check that bluetoothd is running and that you are root."
            ) from exc

    def unregister(self) -> None:
        if self._manager is None:
            return
        try:
            self._manager.UnregisterProfile(PROFILE_PATH)
        except Exception:  # pragma: no cover - best effort clean up
            pass
        self._manager = None


class BluezBackend(ControllerBackend):
    """Present this machine to a Switch as a Bluetooth Pro Controller."""

    name = "bluez"
    description = "Emulate a Pro Controller over Bluetooth (Linux, needs root)"

    def __init__(
        self,
        *,
        console: Console = Console.SWITCH1,
        adapter: str = "hci0",
        reconnect_address: str | None = None,
        report_descriptor: bytes = DEFAULT_REPORT_DESCRIPTOR,
        controller_name: str = PRO_CONTROLLER_NAME,
        verbose: bool = False,
    ) -> None:
        super().__init__(console=console)
        self.adapter = adapter
        self.reconnect_address = reconnect_address
        self.controller_name = controller_name
        self.verbose = verbose
        self.protocol: ProControllerProtocol | None = None
        self.peer_address: str | None = None

        self._config = _AdapterConfig(adapter, controller_name)
        self._profile = _SdpProfile(build_sdp_record(controller_name, report_descriptor))
        self._control: socket.socket | None = None
        self._interrupt: socket.socket | None = None
        self._listeners: list[socket.socket] = []
        self._write_lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._error: BaseException | None = None

    # -- availability -----------------------------------------------------
    @classmethod
    def status(cls) -> BackendStatus:
        if sys.platform != "linux":
            return BackendStatus(
                cls.name, cls.description, False, f"Linux only (running on {platform.system()})"
            )
        if not hasattr(socket, "BTPROTO_L2CAP"):
            return BackendStatus(cls.name, cls.description, False, "no L2CAP support in Python")
        try:
            import dbus  # noqa: F401
        except ImportError:
            return BackendStatus(
                cls.name,
                cls.description,
                False,
                "dbus-python missing: pip install 'any-ctrl[bluez]'",
            )
        if os.geteuid() != 0:
            return BackendStatus(
                cls.name, cls.description, False, "needs root to bind L2CAP PSM 17/19"
            )
        return BackendStatus(cls.name, cls.description, True, "adapter checked at connect time")

    # -- lifecycle --------------------------------------------------------
    def connect(self, *, timeout: float | None = None) -> None:
        self._require_linux()
        deadline = time.monotonic() + (
            timeout if timeout is not None else self.profile.pair_timeout
        )

        self._config.apply()
        self._profile.register()
        address = self._config.address()
        self.protocol = ProControllerProtocol(address)

        try:
            if self.reconnect_address:
                self._connect_out(self.reconnect_address, deadline)
            else:
                self._accept_in(address, deadline)
            self._handshake(deadline)
        except BaseException:
            self.close()
            raise

        self._connected = True
        self._start_threads()
        # Give the console the quiet moment it expects before real input.
        time.sleep(self.profile.settle)

    def _require_linux(self) -> None:
        status = self.status()
        if not status.available:
            raise BackendUnavailable(f"bluez backend unavailable: {status.detail}")

    def _connect_out(self, address: str, deadline: float) -> None:
        """Reconnect to a console that already has us paired."""
        self._log(f"connecting to {address}")
        control = _l2cap_socket()
        interrupt = _l2cap_socket()
        try:
            control.settimeout(max(1.0, deadline - time.monotonic()))
            control.connect((address, CONTROL_PSM))
            interrupt.settimeout(max(1.0, deadline - time.monotonic()))
            interrupt.connect((address, INTERRUPT_PSM))
        except OSError as exc:
            control.close()
            interrupt.close()
            raise BackendError(
                f"could not reconnect to {address}: {exc}. "
                "The console must be awake and on the HOME screen."
            ) from exc
        self._control, self._interrupt = control, interrupt
        self.peer_address = address

    def _accept_in(self, address: str, deadline: float) -> None:
        """Wait for the console to connect (Change Grip/Order screen)."""
        control_listener = _l2cap_socket()
        interrupt_listener = _l2cap_socket()
        self._listeners = [control_listener, interrupt_listener]
        for sock, psm in ((control_listener, CONTROL_PSM), (interrupt_listener, INTERRUPT_PSM)):
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((address, psm))
                sock.listen(1)
            except OSError as exc:
                raise BackendError(
                    f"cannot listen on L2CAP PSM {psm}: {exc}. "
                    "Run as root and start bluetoothd with the input plugin disabled "
                    "(see docs/troubleshooting.md)."
                ) from exc

        self._log("waiting for the console: open Change Grip/Order and stay on that screen")
        self._control = self._accept_one(control_listener, deadline, "control")
        self._interrupt = self._accept_one(interrupt_listener, deadline, "interrupt")
        try:
            self.peer_address = self._control.getpeername()[0]
        except OSError:  # pragma: no cover - kernel dependent
            self.peer_address = None
        self._log(f"console connected from {self.peer_address}")
        for sock in self._listeners:
            sock.close()
        self._listeners = []

    def _accept_one(self, listener: socket.socket, deadline: float, label: str) -> socket.socket:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BackendError(f"timed out waiting for the {label} channel")
        listener.settimeout(remaining)
        try:
            sock, _ = listener.accept()
        except TimeoutError as exc:
            raise BackendError(
                f"timed out waiting for the console on the {label} channel. "
                "Was the console on the Change Grip/Order screen?"
            ) from exc
        except OSError as exc:
            raise BackendError(f"accept failed on the {label} channel: {exc}") from exc
        sock.settimeout(None)
        return sock

    def _handshake(self, deadline: float) -> None:
        """Answer subcommands until the console assigns us a player LED."""
        assert self.protocol is not None and self._interrupt is not None
        last_report = 0.0
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now - last_report >= self.profile.tick:
                self._send_report(self.protocol.input_report())
                last_report = now
            if not _readable(self._interrupt, self.profile.tick):
                continue
            try:
                data = self._interrupt.recv(512)
            except OSError as exc:
                raise BackendError(f"connection lost during handshake: {exc}") from exc
            if not data:
                raise BackendError("the console closed the connection during the handshake")
            for reply in self._process_input(data):
                self._send_report(reply)
            if self.protocol.ready:
                self._log(f"handshake complete, player {self.protocol.player_number}")
                return
        raise BackendError(
            "the console never finished setting the controller up "
            f"(report mode {self.protocol.report_mode!r}, "
            f"subcommands seen: {sorted(set(self.protocol.subcommands_seen))})"
        )

    def _process_input(self, data: bytes) -> list[bytes]:
        assert self.protocol is not None
        payload = data
        if payload and payload[0] in (HID_DATA_OUTPUT, HID_SET_REPORT_OUTPUT):
            payload = payload[1:]
        return self.protocol.handle_output_report(payload)

    def _send_report(self, report: bytes) -> None:
        if self._interrupt is None:
            raise BackendError("not connected")
        with self._write_lock:
            try:
                self._interrupt.sendall(bytes((HID_DATA_INPUT,)) + report)
            except (BrokenPipeError, ConnectionResetError) as exc:
                raise BackendError(f"the console closed the connection: {exc}") from exc
            except OSError as exc:
                raise BackendError(f"failed to send an input report: {exc}") from exc

    # -- running ----------------------------------------------------------
    def _start_threads(self) -> None:
        self._stop.clear()
        self._threads = [
            threading.Thread(target=self._reader_loop, name="anyctrl-interrupt", daemon=True),
            threading.Thread(target=self._sender_loop, name="anyctrl-sender", daemon=True),
            threading.Thread(target=self._control_loop, name="anyctrl-control", daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def _reader_loop(self) -> None:
        """Keep answering subcommands while a macro plays."""
        assert self._interrupt is not None
        while not self._stop.is_set():
            # Poll for readability rather than giving the socket a timeout: the
            # sender thread writes to the same socket, and a write timeout
            # would look like a dropped connection under load.
            if not _readable(self._interrupt, 0.2):
                continue
            try:
                data = self._interrupt.recv(512)
            except OSError as exc:
                self._fail(BackendError(f"connection lost: {exc}"))
                return
            if not data:
                self._fail(BackendError("the console closed the connection"))
                return
            try:
                for reply in self._process_input(data):
                    self._send_report(reply)
            except BackendError as exc:
                self._fail(exc)
                return

    def _sender_loop(self) -> None:
        """Send input reports at a steady cadence, whatever the macro is doing."""
        assert self.protocol is not None
        next_send = time.perf_counter()
        while not self._stop.is_set():
            next_send += self.profile.tick
            try:
                self._send_report(self.protocol.input_report())
            except BackendError as exc:
                self._fail(exc)
                return
            delay = next_send - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            else:
                # We fell behind (system load); resynchronise rather than
                # sending a burst of catch-up reports.
                next_send = time.perf_counter()

    def _control_loop(self) -> None:
        """Answer HID control channel transactions such as SET_PROTOCOL."""
        if self._control is None:
            return
        while not self._stop.is_set():
            if not _readable(self._control, 0.2):
                continue
            try:
                data = self._control.recv(512)
            except OSError:
                return
            if not data:
                return
            try:
                self._answer_control(data)
            except OSError:
                return

    def _answer_control(self, data: bytes) -> None:
        """Reply to one control channel transaction."""
        assert self._control is not None and self.protocol is not None
        transaction = data[0] & HID_TRANSACTION_MASK
        if transaction == HID_TRANSACTION_GET_REPORT:
            # The host wants a report right now rather than waiting for the
            # next periodic one.
            self._control.sendall(bytes((HID_DATA_INPUT,)) + self.protocol.input_report())
            return
        if transaction == HID_TRANSACTION_HID_CONTROL and data[0] == HID_VIRTUAL_CABLE_UNPLUG:
            self._fail(BackendError("the console unplugged the virtual cable"))
            return
        if transaction in (
            HID_TRANSACTION_SET_PROTOCOL,
            HID_TRANSACTION_SET_REPORT,
            HID_TRANSACTION_GET_PROTOCOL,
        ):
            self._control.sendall(bytes((HID_HANDSHAKE_SUCCESS,)))

    def _fail(self, error: BaseException) -> None:
        self._error = error
        self._connected = False
        self._stop.set()

    # -- backend API ------------------------------------------------------
    def send_state(self, state: ControllerState) -> None:
        if self._error is not None:
            raise self._error
        if not self._connected or self.protocol is None:
            raise BackendError("bluez backend is not connected")
        # The sender thread owns transmission; publishing the state is enough.
        self.protocol.set_state(state.copy())

    def close(self) -> None:
        self._stop.set()
        for thread in self._threads:
            if thread.is_alive() and thread is not threading.current_thread():
                thread.join(timeout=1.0)
        self._threads = []
        for sock in (*self._listeners, self._interrupt, self._control):
            if sock is not None:
                try:
                    sock.close()
                except OSError:  # pragma: no cover - best effort
                    pass
        self._listeners = []
        self._interrupt = self._control = None
        self._profile.unregister()
        self._config.restore()
        self._connected = False

    def _log(self, message: str) -> None:
        if self.verbose:
            print(f"[bluez] {message}", file=sys.stderr)


def _readable(sock: socket.socket, timeout: float) -> bool:
    """Wait up to ``timeout`` for ``sock`` to have data waiting."""
    try:
        ready, _, _ = select.select([sock], [], [], timeout)
    except (OSError, ValueError):  # closed underneath us while we waited
        return False
    return bool(ready)


def _l2cap_socket() -> socket.socket:
    """Create a sequential packet L2CAP socket."""
    try:
        return socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
    except (AttributeError, OSError) as exc:  # pragma: no cover - platform dependent
        raise BackendUnavailable(f"L2CAP sockets are unavailable: {exc}") from exc
