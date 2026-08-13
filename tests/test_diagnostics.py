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
