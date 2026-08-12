"""End to end exercises of the command line interface."""

from __future__ import annotations

import pytest

from anyctrl.cli import _input_plugin_disabled, main

MACRO = """
meta name "test macro"
press A 20ms
stick L up for 40ms
loop 2 { press B 10ms every 10ms }
"""


@pytest.fixture()
def macro_file(tmp_path):
    path = tmp_path / "test.macro"
    path.write_text(MACRO)
    return path


def test_check_describes_the_macro(macro_file, capsys):
    assert main(["check", str(macro_file)]) == 0
    out = capsys.readouterr().out
    assert "test macro" in out
    assert "duration:" in out


def test_check_prints_a_timeline(macro_file, capsys):
    assert main(["check", str(macro_file), "--timeline"]) == 0
    out = capsys.readouterr().out
    assert "timeline" in out
    assert "[A]" in out


def test_check_limits_the_timeline(macro_file, capsys):
    assert main(["check", str(macro_file), "--timeline", "--limit", "2"]) == 0
    assert "more (use --limit 0" in capsys.readouterr().out


def test_check_refuses_to_unroll_an_endless_macro(tmp_path, capsys):
    path = tmp_path / "forever.macro"
    path.write_text("loop forever {\n press A\n}\n")
    assert main(["check", str(path), "--timeline"]) == 0
    out = capsys.readouterr().out
    assert "endless" in out
    assert "never ends" in out


def test_check_reads_stdin(monkeypatch, capsys):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("press A\n"))
    assert main(["check", "-"]) == 0
    assert "<stdin>" in capsys.readouterr().out


def test_run_against_the_dryrun_backend(macro_file, capsys):
    assert main(["run", str(macro_file), "--backend", "dryrun", "--speed", "50"]) == 0
    out = capsys.readouterr().out
    assert "backend: dryrun" in out
    assert "done after" in out


def test_run_reports_macro_errors(tmp_path, capsys):
    path = tmp_path / "bad.macro"
    path.write_text("press NOTABUTTON\n")
    assert main(["run", str(path), "--backend", "dryrun"]) == 1
    assert "unknown button" in capsys.readouterr().err


def test_run_reports_a_missing_file(capsys):
    assert main(["run", "/nonexistent/none.macro", "--backend", "dryrun"]) == 1
    assert "macro not found" in capsys.readouterr().err


def test_run_honours_the_timeout(tmp_path, capsys):
    path = tmp_path / "slow.macro"
    path.write_text("wait 10s\n")
    code = main(["run", str(path), "--backend", "dryrun", "--speed", "100", "--timeout", "50ms"])
    assert code == 130
    assert "stopped" in capsys.readouterr().err


def test_press_sends_a_single_combination(capsys):
    assert main(["press", "ZL+ZR", "--backend", "dryrun", "--duration", "10ms"]) == 0
    assert "done after" in capsys.readouterr().out


def test_press_rejects_unknown_buttons(capsys):
    assert main(["press", "NOPE", "--backend", "dryrun"]) == 1
    assert "unknown button" in capsys.readouterr().err


def test_backends_lists_every_backend(capsys):
    assert main(["backends"]) == 0
    out = capsys.readouterr().out
    for name in ("bluez", "serial", "dryrun"):
        assert name in out


def test_buttons_lists_names(capsys):
    assert main(["buttons"]) == 0
    out = capsys.readouterr().out
    assert "ZL" in out and "CAPTURE" in out


def test_doctor_runs_and_reports(capsys):
    code = main(["doctor"])
    assert code in (0, 1)
    assert "backends" in capsys.readouterr().out


def test_no_command_prints_help(capsys):
    assert main([]) == 2
    assert "usage" in capsys.readouterr().out


def test_show_inputs_prints_state_changes(macro_file, capsys):
    code = main(["run", str(macro_file), "--backend", "dryrun", "--speed", "100", "--show-inputs"])
    assert code == 0
    assert "[A]" in capsys.readouterr().out


def test_quiet_run_prints_nothing_on_success(macro_file, capsys):
    assert main(["run", str(macro_file), "--backend", "dryrun", "--speed", "100", "-q"]) == 0
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (["-P", "input"], True),
        (["--noplugin=input"], True),
        (["-Pinput"], True),
        (["--noplugin=input,hog"], True),
        (["--noplugin=*"], True),
        (["-n", "-d"], False),
        (["--noplugin=hog"], False),
        ([], False),
    ],
)
def test_bluetoothd_plugin_detection(arguments, expected):
    assert _input_plugin_disabled(arguments) is expected
