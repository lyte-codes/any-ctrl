"""Parsing and compiling macros."""

from __future__ import annotations

import pytest

from anyctrl.controller.buttons import Button, Dpad, Stick
from anyctrl.errors import MacroError
from anyctrl.macro.compiler import DEFAULT_GAP, DEFAULT_PRESS_TIME, compile_macro, compile_text
from anyctrl.macro.model import (
    Emit,
    Repeat,
    SetButtons,
    SetDpad,
    SetStick,
    Sleep,
)
from anyctrl.macro.parser import parse_duration, parse_macro, parse_macro_file, tokenize


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("500ms", 0.5),
        ("1s", 1.0),
        ("1.5s", 1.5),
        ("2m", 120.0),
        ("250", 0.25),
        ("0", 0.0),
    ],
)
def test_parse_duration(text, expected):
    assert parse_duration(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", ["-1s", "soon", "", "1x"])
def test_parse_duration_rejects_nonsense(text):
    with pytest.raises(MacroError):
        parse_duration(text)


def test_tokenize_handles_comments_quotes_and_braces():
    assert tokenize("press A  # comment") == ["press", "A"]
    assert tokenize("press A // comment") == ["press", "A"]
    assert tokenize('log "hello world"') == ["log", "hello world"]
    assert tokenize("loop 2 { press A }") == ["loop", "2", "{", "press", "A", "}"]
    assert tokenize("") == []


def test_press_expands_to_down_wait_up_wait():
    macro = compile_text("press A")
    assert macro.ops == (
        SetButtons(down=frozenset({Button.A})),
        Sleep(DEFAULT_PRESS_TIME),
        SetButtons(up=frozenset({Button.A})),
        Sleep(DEFAULT_GAP),
    )


def test_press_options_hold_repeat_and_interval():
    macro = compile_text("press A+B 250ms x3 every 50ms")
    presses = [op for op in macro.ops if isinstance(op, SetButtons) and op.down]
    assert len(presses) == 3
    assert presses[0].down == frozenset({Button.A, Button.B})
    assert macro.duration() == pytest.approx(3 * (0.25 + 0.05))


def test_hold_and_release_do_not_wait():
    macro = compile_text("hold ZL\nwait 1s\nrelease ZL")
    assert macro.ops == (
        SetButtons(down=frozenset({Button.ZL})),
        Sleep(1.0),
        SetButtons(up=frozenset({Button.ZL})),
    )


def test_release_all_is_its_own_instruction():
    macro = compile_text("hold A\nrelease all")
    assert macro.ops[-1].__class__.__name__ == "ReleaseAll"


def test_stick_forms_agree():
    by_name = compile_text("stick L up").ops[0]
    by_vector = compile_text("stick L 0 1").ops[0]
    by_angle = compile_text("stick L angle 0").ops[0]
    assert isinstance(by_name, SetStick) and by_name.stick is Stick.LEFT
    assert by_name.position.y == pytest.approx(by_vector.position.y)
    assert by_angle.position.y == pytest.approx(1.0)


def test_stick_with_duration_recentres_afterwards():
    ops = compile_text("stick R down 0.5 for 2s").ops
    assert isinstance(ops[0], SetStick) and ops[0].position.y == pytest.approx(-0.5)
    assert ops[1] == Sleep(2.0)
    assert isinstance(ops[2], SetStick) and ops[2].position.is_centered


def test_dpad_with_duration_returns_to_neutral():
    ops = compile_text("dpad down_left 200ms").ops
    assert ops[0] == SetDpad(Dpad.DOWN_LEFT)
    assert ops[1] == Sleep(0.2)
    assert ops[2] == SetDpad(Dpad.NONE)


def test_loops_are_not_unrolled():
    macro = compile_text("loop 100 {\n  press A\n}")
    assert len(macro.ops) == 1
    assert isinstance(macro.ops[0], Repeat)
    assert macro.ops[0].count == 100
    assert not macro.is_endless


def test_forever_loops_are_endless():
    macro = compile_text("loop forever {\n  press A\n}")
    assert macro.is_endless
    assert macro.duration() == float("inf")


def test_empty_and_single_iteration_loops_collapse():
    assert compile_text("loop 3 {\n}").ops == ()
    assert compile_text("loop 0 {\n press A\n}").ops == ()
    assert compile_text("loop 1 {\n press A\n}").ops[0] == SetButtons(down=frozenset({Button.A}))


def test_blocks_can_be_defined_and_called():
    macro = compile_text(
        """
        def greet {
            log "hi"
            press A
        }
        call greet
        call greet
        """
    )
    assert sum(isinstance(op, Emit) for op in macro.ops) == 2


def test_calling_an_unknown_block_is_an_error():
    with pytest.raises(MacroError, match="unknown block"):
        compile_text("call missing")


def test_recursive_blocks_are_rejected():
    with pytest.raises(MacroError, match="recursive call"):
        compile_text("def a {\n call a\n}\ncall a")


def test_set_changes_defaults_for_later_statements():
    macro = compile_text("press A\nset press_time 500ms\nset gap 0\npress B")
    sleeps = [op.seconds for op in macro.ops if isinstance(op, Sleep)]
    assert sleeps == [DEFAULT_PRESS_TIME, DEFAULT_GAP, 0.5]


def test_set_rejects_unknown_options():
    with pytest.raises(MacroError, match="unknown option"):
        compile_text("set turbo 1")


def test_meta_is_carried_through_compilation():
    macro = compile_text('meta name "Egg hatcher"\nmeta console switch2\npress A', source="x.macro")
    assert macro.meta["name"] == "Egg hatcher"
    assert macro.meta["console"] == "switch2"
    assert macro.name == "Egg hatcher"


def test_braces_may_share_a_line_with_statements():
    inline = compile_text("loop 2 { press A }")
    block = compile_text("loop 2 {\npress A\n}")
    assert inline.ops == block.ops


def test_braces_inside_strings_are_text():
    macro = compile_text('log "} not a brace"')
    assert macro.ops == (Emit("} not a brace"),)


def test_unbalanced_braces_are_reported():
    with pytest.raises(MacroError, match="unclosed"):
        compile_text("loop 2 {\npress A")
    with pytest.raises(MacroError, match="unexpected"):
        compile_text("press A\n}")


def test_errors_carry_source_and_line():
    with pytest.raises(MacroError) as excinfo:
        compile_text("press A\npress NOPE", source="demo.macro")
    assert "demo.macro:2" in str(excinfo.value)


def test_unknown_statement_is_reported():
    with pytest.raises(MacroError, match="unknown statement"):
        compile_text("jump A")


def test_duration_is_the_sum_of_waits():
    macro = compile_text("wait 1s\nloop 3 {\nwait 500ms\n}")
    assert macro.duration() == pytest.approx(2.5)


def test_include_pulls_in_statements_and_blocks(tmp_path):
    (tmp_path / "shared.macro").write_text('def tap_a {\n press A\n}\nlog "shared"\n')
    main = tmp_path / "main.macro"
    main.write_text('include "shared.macro"\ncall tap_a\n')
    macro = compile_macro(parse_macro_file(main))
    assert sum(isinstance(op, Emit) for op in macro.ops) == 1
    assert any(isinstance(op, SetButtons) for op in macro.ops)


def test_include_detects_cycles(tmp_path):
    (tmp_path / "a.macro").write_text('include "b.macro"\n')
    (tmp_path / "b.macro").write_text('include "a.macro"\n')
    with pytest.raises(MacroError, match="circular include"):
        parse_macro_file(tmp_path / "a.macro")


def test_include_reports_missing_files(tmp_path):
    macro = tmp_path / "a.macro"
    macro.write_text('include "nope.macro"\n')
    with pytest.raises(MacroError, match="not found"):
        parse_macro_file(macro)


def test_parse_macro_keeps_statement_order():
    parsed = parse_macro("press A\nwait 1s\npress B")
    assert [type(statement).__name__ for statement in parsed.statements] == [
        "Press",
        "Wait",
        "Press",
    ]
