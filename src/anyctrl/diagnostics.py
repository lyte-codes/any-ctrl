"""Deep diagnosis of why a console will not see the controller.

``anyctrl doctor`` answers "can this machine work at all". This module answers
the harder question: the machine looks fine, so what exactly is failing? Every
check reports a stable hex code, so a failure can be looked up, searched for
and quoted without ambiguity.

Code space, by leading digit:

===========  ==============================================================
``0x0000``   everything checked passed
``0x1xxx``   the platform: OS, Python, privileges
``0x2xxx``   the Bluetooth adapter and its advertised identity
``0x3xxx``   the BlueZ daemon and the HID ports it may be holding
``0x4xxx``   D-Bus and SDP service registration
``0x5xxx``   the USB serial bridge
``0x6xxx``   the console: connection and handshake
===========  ==============================================================
"""

from __future__ import annotations

import errno
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path


class Code(IntEnum):
    """Every failure any-ctrl can diagnose, with a stable hex identity."""

    OK = 0x0000

    # 0x1xxx - platform
    NOT_LINUX = 0x1001
    NO_L2CAP_SUPPORT = 0x1002
    NOT_ROOT = 0x1003

    # 0x2xxx - adapter
    NO_ADAPTER = 0x2001
    ADAPTER_POWERED_OFF = 0x2002
    RFKILL_BLOCKED = 0x2003
    ADAPTER_UNREADABLE = 0x2004
    NOT_DISCOVERABLE = 0x2005
    NOT_PAIRABLE = 0x2006
    CLASS_TOOL_MISSING = 0x2101
    CLASS_WRITE_FAILED = 0x2102
    CLASS_REVERTED = 0x2103
    CLASS_UNREADABLE = 0x2104

    # 0x3xxx - daemon and HID ports
    BLUETOOTHD_NOT_RUNNING = 0x3001
    INPUT_PLUGIN_ENABLED = 0x3002
    PSM_CONTROL_BUSY = 0x3003
    PSM_INTERRUPT_BUSY = 0x3004
    PSM_PERMISSION_DENIED = 0x3005

    # 0x4xxx - D-Bus and SDP
    DBUS_MISSING = 0x4001
    DBUS_UNREACHABLE = 0x4002
    SDP_REGISTER_FAILED = 0x4003

    # 0x5xxx - USB bridge
    PYSERIAL_MISSING = 0x5001
    NO_SERIAL_PORTS = 0x5002
    NO_BRIDGE_ADAPTER = 0x5003

    # 0x6xxx - the console
    NO_CONSOLE_CONNECTION = 0x6001
    HANDSHAKE_INCOMPLETE = 0x6002

    @property
    def hex(self) -> str:
        return f"0x{self.value:04X}"


#: What to do about each code. Kept next to the codes so the advice cannot
#: drift away from the check that produces it.
REMEDIES: dict[Code, str] = {
    Code.NOT_LINUX: "Bluetooth emulation needs Linux; on macOS use the USB bridge",
    Code.NO_L2CAP_SUPPORT: "this Python was built without Bluetooth socket support",
    Code.NOT_ROOT: "re-run under sudo: binding the HID ports is privileged",
    Code.NO_ADAPTER: "no Bluetooth adapter found; check 'hciconfig -a' and the hardware",
    Code.ADAPTER_POWERED_OFF: "power it on: bluetoothctl power on",
    Code.RFKILL_BLOCKED: "unblock it: sudo rfkill unblock bluetooth",
    Code.ADAPTER_UNREADABLE: "the adapter is not answering over D-Bus; restart bluetooth.service",
    Code.NOT_DISCOVERABLE: "any-ctrl sets this while running; outside a run it is expected",
    Code.NOT_PAIRABLE: "any-ctrl sets this while running; outside a run it is expected",
    Code.CLASS_TOOL_MISSING: "install bluez (hciconfig) or bluez-tools (btmgmt)",
    Code.CLASS_WRITE_FAILED: "the class of device could not be written; check the tool's output",
    Code.CLASS_REVERTED: (
        "something is rewriting the class of device: a desktop Bluetooth applet, "
        "or bluetoothd restarting. Stop it, or run with the desktop session logged out"
    ),
    Code.CLASS_UNREADABLE: "cannot read the class back; install hciconfig to verify it",
    Code.BLUETOOTHD_NOT_RUNNING: "start it: sudo systemctl start bluetooth",
    Code.INPUT_PLUGIN_ENABLED: "start bluetoothd with '-P input' (see docs/backends.md)",
    Code.PSM_CONTROL_BUSY: (
        "PSM 17 is held by another process, almost always bluetoothd's input plugin"
    ),
    Code.PSM_INTERRUPT_BUSY: "PSM 19 is held by another process",
    Code.PSM_PERMISSION_DENIED: "re-run under sudo",
    Code.DBUS_MISSING: "pip install 'any-ctrl[bluez]', or apt install python3-dbus",
    Code.DBUS_UNREACHABLE: "bluetoothd is not answering on the system bus",
    Code.SDP_REGISTER_FAILED: "the HID service record was rejected; see the detail line",
    Code.PYSERIAL_MISSING: "pip install 'any-ctrl[serial]'",
    Code.NO_SERIAL_PORTS: "connect the USB-to-serial adapter wired to the bridge board",
    Code.NO_BRIDGE_ADAPTER: "no recognised adapter; pass --port to name one yourself",
    Code.NO_CONSOLE_CONNECTION: (
        "the adapter advertised correctly but no console connected. Confirm the console "
        "is on Change Grip/Order, delete any stale pairing there, and move it closer"
    ),
    Code.HANDSHAKE_INCOMPLETE: "the console connected but never finished setting us up",
}


@dataclass
class Check:
    """One diagnostic result."""

    name: str
    ok: bool
    code: Code = Code.OK
    detail: str = ""
    skipped: bool = False

    @property
    def hint(self) -> str:
        return "" if self.ok else REMEDIES.get(self.code, "")

    def __str__(self) -> str:
        if self.skipped:
            return f"-- {Code.OK.hex}  {self.name}: skipped ({self.detail})"
        mark = "ok" if self.ok else "NO"
        code = Code.OK.hex if self.ok else self.code.hex
        line = f"{mark} {code}  {self.name}: {self.detail}"
        if not self.ok and self.hint:
            line += f"\n              -> {self.hint}"
        return line


@dataclass
class Report:
    """The full set of results."""

    checks: list[Check] = field(default_factory=list)

    def add(self, check: Check) -> Check:
        self.checks.append(check)
        return check

    @property
    def failures(self) -> list[Check]:
        return [check for check in self.checks if not check.ok and not check.skipped]

    @property
    def code(self) -> Code:
        """The first failure's code: checks run in dependency order."""
        failures = self.failures
        return failures[0].code if failures else Code.OK

    def to_dict(self) -> dict:
        return {
            "code": self.code.hex,
            "status": "ok" if self.code is Code.OK else "fail",
            "checks": [
                {
                    "name": check.name,
                    "ok": check.ok,
                    "skipped": check.skipped,
                    "code": (Code.OK if check.ok else check.code).hex,
                    "detail": check.detail,
                    "hint": check.hint,
                }
                for check in self.checks
            ],
        }


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def check_platform(report: Report) -> bool:
    """Platform, Python and privileges. Returns False if nothing else can run."""
    if sys.platform != "linux":
        report.add(
            Check(
                "platform",
                False,
                Code.NOT_LINUX,
                f"{platform.system()} cannot emulate a Bluetooth controller",
            )
        )
        return False
    report.add(Check("platform", True, detail=f"Linux {platform.release()}"))

    if not hasattr(socket, "BTPROTO_L2CAP"):
        report.add(Check("l2cap support", False, Code.NO_L2CAP_SUPPORT, "missing in this Python"))
        return False
    report.add(Check("l2cap support", True, detail="present"))

    if os.geteuid() != 0:
        report.add(Check("privileges", False, Code.NOT_ROOT, "not running as root"))
        return False
    report.add(Check("privileges", True, detail="running as root"))
    return True


def check_rfkill(report: Report) -> None:
    """Is Bluetooth soft- or hard-blocked?"""
    blocked: list[str] = []
    root = Path("/sys/class/rfkill")
    if not root.is_dir():
        report.add(Check("rfkill", True, detail="no rfkill interface (nothing to block)"))
        return
    for entry in sorted(root.glob("rfkill*")):
        try:
            if (entry / "type").read_text().strip() != "bluetooth":
                continue
            soft = (entry / "soft").read_text().strip()
            hard = (entry / "hard").read_text().strip()
        except OSError:
            continue
        if soft != "0":
            blocked.append(f"{entry.name} soft-blocked")
        if hard != "0":
            blocked.append(f"{entry.name} hard-blocked")
    if blocked:
        report.add(Check("rfkill", False, Code.RFKILL_BLOCKED, ", ".join(blocked)))
    else:
        report.add(Check("rfkill", True, detail="bluetooth not blocked"))


def check_dbus(report: Report, adapter: str) -> object | None:
    """Can we reach bluetoothd, and does the adapter exist?"""
    try:
        import dbus
    except ImportError:
        report.add(Check("d-bus", False, Code.DBUS_MISSING, "dbus-python is not installed"))
        return None

    try:
        bus = dbus.SystemBus()
        obj = bus.get_object("org.bluez", f"/org/bluez/{adapter}")
        properties = dbus.Interface(obj, "org.freedesktop.DBus.Properties")
        address = str(properties.Get("org.bluez.Adapter1", "Address"))
    except dbus.exceptions.DBusException as exc:
        message = str(exc)
        if "ServiceUnknown" in message or "was not provided" in message:
            report.add(Check("d-bus", False, Code.DBUS_UNREACHABLE, "bluetoothd is not on the bus"))
        else:
            report.add(Check("adapter", False, Code.NO_ADAPTER, f"{adapter}: {message}"))
        return None

    report.add(Check("d-bus", True, detail="bluetoothd reachable"))
    report.add(Check("adapter", True, detail=f"{adapter} at {address}"))

    for name, code in (
        ("Powered", Code.ADAPTER_POWERED_OFF),
        ("Discoverable", Code.NOT_DISCOVERABLE),
        ("Pairable", Code.NOT_PAIRABLE),
    ):
        try:
            value = bool(properties.Get("org.bluez.Adapter1", name))
        except Exception:
            report.add(
                Check(f"adapter {name.lower()}", False, Code.ADAPTER_UNREADABLE, "unreadable")
            )
            continue
        # Discoverable and pairable are set by any-ctrl at connect time, so
        # outside a run they say nothing alarming.
        informational = name in ("Discoverable", "Pairable")
        if value or informational:
            report.add(Check(f"adapter {name.lower()}", True, detail=str(value).lower()))
        else:
            report.add(Check(f"adapter {name.lower()}", False, code, "false"))
    return properties


def check_daemon(report: Report) -> None:
    """Is bluetoothd running, and is its input plugin out of the way?"""
    from anyctrl.cli import _bluetoothd_input_plugin_state

    state = _bluetoothd_input_plugin_state()
    if state is None:
        report.add(Check("bluetoothd", False, Code.BLUETOOTHD_NOT_RUNNING, "no process found"))
    elif state:
        report.add(
            Check("bluetoothd", False, Code.INPUT_PLUGIN_ENABLED, "running with the input plugin")
        )
    else:
        report.add(Check("bluetoothd", True, detail="running without the input plugin"))


def check_hid_ports(report: Report) -> None:
    """Bind each HID port separately, so the failure names the port."""
    from anyctrl.backends.bluez import CONTROL_PSM, INTERRUPT_PSM

    for psm, code, label in (
        (CONTROL_PSM, Code.PSM_CONTROL_BUSY, "control"),
        (INTERRUPT_PSM, Code.PSM_INTERRUPT_BUSY, "interrupt"),
    ):
        try:
            sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
        except OSError as exc:
            report.add(Check(f"psm {psm}", False, Code.NO_L2CAP_SUPPORT, str(exc)))
            continue
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("00:00:00:00:00:00", psm))
            sock.listen(1)
            report.add(Check(f"psm {psm}", True, detail=f"{label} channel bindable"))
        except PermissionError:
            report.add(Check(f"psm {psm}", False, Code.PSM_PERMISSION_DENIED, "permission denied"))
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                report.add(Check(f"psm {psm}", False, code, "already in use"))
            else:
                report.add(Check(f"psm {psm}", False, code, str(exc)))
        finally:
            sock.close()


def check_device_class(report: Report, adapter: str, *, live: bool) -> None:
    """Read the class of device, and in live mode try to set it and verify."""
    from anyctrl.backends.bluez import PRO_CONTROLLER_CLASS, _AdapterConfig

    if not (shutil.which("hciconfig") or shutil.which("btmgmt")):
        report.add(Check("class tooling", False, Code.CLASS_TOOL_MISSING, "no hciconfig or btmgmt"))
        return
    tools = [name for name in ("hciconfig", "btmgmt") if shutil.which(name)]
    report.add(Check("class tooling", True, detail=", ".join(tools)))

    config = _AdapterConfig(adapter)
    current = config.read_device_class()
    if current is None:
        report.add(Check("class of device", False, Code.CLASS_UNREADABLE, "cannot read it back"))
        return
    if not live:
        report.add(
            Check(
                "class of device",
                True,
                detail=f"currently 0x{current:06x} (any-ctrl sets 0x{PRO_CONTROLLER_CLASS:06x} "
                "while running)",
            )
        )
        return

    try:
        config._write_device_class()
    except Exception as exc:
        report.add(Check("class of device", False, Code.CLASS_WRITE_FAILED, str(exc)))
        return
    time.sleep(0.3)
    after = config.read_device_class()
    if after == PRO_CONTROLLER_CLASS:
        report.add(Check("class of device", True, detail=f"set to 0x{after:06x} and held"))
    else:
        report.add(
            Check(
                "class of device",
                False,
                Code.CLASS_REVERTED,
                f"wrote 0x{PRO_CONTROLLER_CLASS:06x}, reads back 0x{after:06x}",
            )
        )


def check_serial(report: Report) -> None:
    """The USB bridge side, which matters on macOS and as a fallback."""
    from anyctrl.backends.serial_bridge import list_ports

    try:
        import serial  # noqa: F401
    except ImportError:
        report.add(Check("pyserial", False, Code.PYSERIAL_MISSING, "not installed"))
        return
    report.add(Check("pyserial", True, detail="installed"))

    ports = list_ports()
    if not ports:
        report.add(Check("serial ports", False, Code.NO_SERIAL_PORTS, "none found"))
        return
    candidates = [port for port in ports if port.is_candidate]
    if candidates:
        report.add(Check("serial ports", True, detail=f"bridge candidate {candidates[0]}"))
    else:
        report.add(
            Check(
                "serial ports",
                False,
                Code.NO_BRIDGE_ADAPTER,
                f"{len(ports)} port(s), none recognised as a bridge",
            )
        )


def check_live_advertising(report: Report, adapter: str, seconds: float, console) -> None:
    """Advertise as a controller for real and see whether anything connects.

    This is the whole connect path short of playing a macro: adapter
    configured, SDP registered, both channels listening. If a console is on
    Change Grip/Order and still nothing arrives, the fault is past our side.
    """
    from anyctrl.backends.bluez import BluezBackend
    from anyctrl.errors import BackendError

    backend = BluezBackend(console=console, adapter=adapter)
    backend.on_status = lambda message: print(f"      {message}")
    try:
        backend.connect(timeout=seconds)
    except BackendError as exc:
        message = str(exc)
        if "timed out waiting for the console" in message:
            report.add(
                Check(
                    "live advertising",
                    False,
                    Code.NO_CONSOLE_CONNECTION,
                    f"advertised for {seconds:g}s, nothing connected",
                )
            )
        elif "never finished setting the controller up" in message:
            report.add(Check("live advertising", False, Code.HANDSHAKE_INCOMPLETE, message))
        elif "class" in message:
            report.add(Check("live advertising", False, Code.CLASS_REVERTED, message))
        elif "SDP" in message or "service record" in message:
            report.add(Check("live advertising", False, Code.SDP_REGISTER_FAILED, message))
        else:
            report.add(Check("live advertising", False, Code.NO_CONSOLE_CONNECTION, message))
        return
    except Exception as exc:  # pragma: no cover - unexpected, still worth coding
        report.add(Check("live advertising", False, Code.SDP_REGISTER_FAILED, str(exc)))
        return
    finally:
        backend.close()

    report.add(
        Check(
            "live advertising",
            True,
            detail=f"console connected and completed the handshake as player "
            f"{backend.protocol.player_number if backend.protocol else '?'}",
        )
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def diagnose(*, adapter: str = "hci0", live: float = 0.0, console=None) -> Report:
    """Run every applicable check, in dependency order."""
    from anyctrl.backends.base import Console

    console = console or Console.SWITCH1
    report = Report()

    if not check_platform(report):
        check_serial(report)  # the only route left on this machine
        return report

    check_rfkill(report)
    check_daemon(report)
    check_hid_ports(report)
    properties = check_dbus(report, adapter)
    check_device_class(report, adapter, live=bool(live) and properties is not None)
    check_serial(report)

    if live and not report.failures:
        check_live_advertising(report, adapter, live, console)
    elif live:
        report.add(
            Check(
                "live advertising",
                True,
                detail="not attempted: fix the failures above first",
                skipped=True,
            )
        )
    return report
