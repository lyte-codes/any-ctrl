"""The deep diagnosis command and its error codes."""

from __future__ import annotations

import json

from anyctrl.cli import main
from anyctrl.diagnostics import REMEDIES, Check, Code, Report, diagnose


def test_every_failure_code_is_unique():
    values = [code.value for code in Code]
    assert len(values) == len(set(values))


def test_every_failure_code_has_a_remedy():
    for code in Code:
        if code is Code.OK:
            continue
        assert code in REMEDIES, f"{code.name} ({code.hex}) has no remedy text"
        assert REMEDIES[code].strip()


def test_codes_are_grouped_by_area():
    assert Code.NOT_ROOT.value // 0x1000 == 1  # platform
    assert Code.RFKILL_BLOCKED.value // 0x1000 == 2  # adapter
    assert Code.PSM_CONTROL_BUSY.value // 0x1000 == 3  # daemon and ports
    assert Code.DBUS_MISSING.value // 0x1000 == 4  # d-bus and sdp
    assert Code.PYSERIAL_MISSING.value // 0x1000 == 5  # usb bridge
    assert Code.NO_CONSOLE_CONNECTION.value // 0x1000 == 6  # console


def test_hex_rendering_is_four_digits():
    assert Code.OK.hex == "0x0000"
    assert Code.CLASS_REVERTED.hex == "0x2103"


def test_report_code_is_the_first_failure():
    report = Report()
    report.add(Check("first", True, detail="fine"))
    report.add(Check("second", False, Code.RFKILL_BLOCKED, "blocked"))
    report.add(Check("third", False, Code.PSM_CONTROL_BUSY, "busy"))
    assert report.code is Code.RFKILL_BLOCKED
    assert len(report.failures) == 2


def test_a_clean_report_codes_as_zero():
    report = Report()
    report.add(Check("all good", True, detail="fine"))
    assert report.code is Code.OK
    assert report.to_dict()["status"] == "ok"


def test_skipped_checks_are_not_failures():
    report = Report()
    report.add(Check("live", False, Code.NO_CONSOLE_CONNECTION, "not run", skipped=True))
    assert report.failures == []
    assert report.code is Code.OK


def test_failed_checks_render_with_code_and_hint():
    rendered = str(Check("psm 17", False, Code.PSM_CONTROL_BUSY, "already in use"))
    assert "0x3003" in rendered
    assert "already in use" in rendered
    assert REMEDIES[Code.PSM_CONTROL_BUSY][:20] in rendered


def test_diagnose_returns_a_report_without_hardware():
    report = diagnose(adapter="hci-nonexistent")
    assert report.checks
    # Whatever this machine looks like, the result must be a valid code.
    assert isinstance(report.code, Code)


def test_diagnose_command_runs(capsys):
    code = main(["diagnose"])
    assert code in (0, 1)
    out = capsys.readouterr().out
    assert "result 0x" in out


def test_diagnose_json_is_machine_readable(capsys):
    main(["diagnose", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["code"].startswith("0x")
    assert payload["status"] in ("ok", "fail")
    assert all("code" in check for check in payload["checks"])


def test_advisory_failures_do_not_become_the_result():
    report = Report()
    report.add(Check("bluetooth", True, detail="fine"))
    report.add(Check("serial ports", False, Code.NO_BRIDGE_ADAPTER, "none", advisory=True))
    assert report.failures == []
    assert report.code is Code.OK
    assert len(report.advisories) == 1


def test_advisory_failures_still_appear_in_the_output():
    rendered = str(Check("serial ports", False, Code.NO_BRIDGE_ADAPTER, "none", advisory=True))
    assert "0x5003" in rendered
    assert "not the route in use" in rendered


def test_the_bluez_route_treats_bridge_problems_as_advisory():
    # A Pi driving a console over Bluetooth has no bridge, and that must not
    # be reported as the reason the console is not connecting.
    report = diagnose(path="bluez")
    serial_checks = [check for check in report.checks if "serial" in check.name]
    assert serial_checks, "the bridge is still inspected"
    assert all(check.ok or check.advisory for check in serial_checks)
    assert all(check.code.value // 0x1000 != 5 for check in report.failures)


def test_the_serial_route_treats_a_non_linux_platform_as_advisory():
    report = diagnose(path="serial")
    platform_checks = [check for check in report.checks if check.name == "platform"]
    assert platform_checks
    assert all(check.ok or check.advisory for check in platform_checks)


def test_live_is_not_blocked_by_an_advisory_failure(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        "anyctrl.diagnostics.check_live_advertising",
        lambda report, adapter, seconds, console: calls.append(adapter),
    )
    # Force every real bluez check to pass so only the advisory one remains.
    monkeypatch.setattr("anyctrl.diagnostics.check_platform", lambda report, advisory=False: True)
    monkeypatch.setattr("anyctrl.diagnostics.check_rfkill", lambda report: None)
    monkeypatch.setattr("anyctrl.diagnostics.check_daemon", lambda report: None)
    monkeypatch.setattr("anyctrl.diagnostics.check_hid_ports", lambda report: None)
    monkeypatch.setattr("anyctrl.diagnostics.check_dbus", lambda report, adapter: object())
    monkeypatch.setattr(
        "anyctrl.diagnostics.check_device_class", lambda report, adapter, live: None
    )
    monkeypatch.setattr("anyctrl.diagnostics.check_scan_and_agent", lambda report, adapter: None)

    def failing_serial(report, advisory=False):
        report.add(Check("serial ports", False, Code.NO_BRIDGE_ADAPTER, "none", advisory=advisory))

    monkeypatch.setattr("anyctrl.diagnostics.check_serial", failing_serial)

    report = diagnose(path="bluez", live=5.0)
    assert calls == ["hci0"], "the live test must still run"
    assert report.code is Code.OK


def test_json_reports_the_route(capsys):
    main(["diagnose", "--json", "--path", "serial"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["path"] == "serial"
