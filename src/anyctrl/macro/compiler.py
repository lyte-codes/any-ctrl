"""Lower a parsed macro into the flat instruction list the player executes."""

from __future__ import annotations

from dataclasses import dataclass

from anyctrl.controller.buttons import Dpad
from anyctrl.controller.state import StickState
from anyctrl.errors import MacroError
from anyctrl.macro.model import (
    Call,
    CompiledMacro,
    DpadMove,
    Emit,
    Hold,
    Log,
    Loop,
    MacroFile,
    Op,
    Press,
    Release,
    ReleaseAll,
    Repeat,
    SetButtons,
    SetDpad,
    SetOption,
    SetStick,
    Sleep,
    Statement,
    StickMove,
    Wait,
)

#: How long ``press`` holds a button when the macro does not say.
DEFAULT_PRESS_TIME = 0.08
#: How long playback pauses after a ``press`` so the console sees two distinct
#: presses when they follow each other.
DEFAULT_GAP = 0.08

MAX_CALL_DEPTH = 32


@dataclass
class _Context:
    press_time: float
    gap: float


def compile_macro(
    macro: MacroFile,
    *,
    press_time: float | None = None,
    gap: float | None = None,
) -> CompiledMacro:
    """Compile a parsed macro, expanding ``call`` and folding ``set``."""
    context = _Context(
        press_time=press_time if press_time is not None else DEFAULT_PRESS_TIME,
        gap=gap if gap is not None else DEFAULT_GAP,
    )
    meta = dict(macro.meta)
    compiler = _Compiler(macro, context, meta)
    ops = compiler.compile_block(macro.statements, call_stack=())
    return CompiledMacro(ops=tuple(ops), meta=meta, source=macro.source)


class _Compiler:
    def __init__(self, macro: MacroFile, context: _Context, meta: dict[str, str]):
        self.macro = macro
        self.context = context
        self.meta = meta

    def compile_block(
        self, statements: list[Statement], *, call_stack: tuple[str, ...]
    ) -> list[Op]:
        ops: list[Op] = []
        for statement in statements:
            ops.extend(self._compile_statement(statement, call_stack))
        return ops

    def _compile_statement(self, statement: Statement, call_stack: tuple[str, ...]) -> list[Op]:
        if isinstance(statement, Press):
            return self._compile_press(statement)
        if isinstance(statement, Hold):
            return [SetButtons(down=statement.buttons)]
        if isinstance(statement, Release):
            if statement.buttons is None:
                return [ReleaseAll()]
            return [SetButtons(up=statement.buttons)]
        if isinstance(statement, Wait):
            return [Sleep(statement.seconds)] if statement.seconds > 0 else []
        if isinstance(statement, StickMove):
            return self._compile_stick(statement)
        if isinstance(statement, DpadMove):
            return self._compile_dpad(statement)
        if isinstance(statement, Log):
            return [Emit(statement.message)]
        if isinstance(statement, SetOption):
            self._apply_option(statement)
            return []
        if isinstance(statement, Loop):
            return self._compile_loop(statement, call_stack)
        if isinstance(statement, Call):
            return self._compile_call(statement, call_stack)
        raise MacroError(  # pragma: no cover - defensive
            f"cannot compile {type(statement).__name__}",
            source=statement.source,
            line=statement.line,
        )

    def _apply_option(self, statement: SetOption) -> None:
        if statement.name == "press_time":
            self.context.press_time = statement.value
        elif statement.name == "gap":
            self.context.gap = statement.value
        elif statement.name == "tick":
            # Consumed by the player rather than the compiler.
            self.meta["tick"] = f"{statement.value}s"

    def _compile_press(self, statement: Press) -> list[Op]:
        hold = statement.duration if statement.duration is not None else self.context.press_time
        gap = statement.interval if statement.interval is not None else self.context.gap
        ops: list[Op] = []
        for _ in range(statement.repeat):
            ops.append(SetButtons(down=statement.buttons))
            if hold > 0:
                ops.append(Sleep(hold))
            ops.append(SetButtons(up=statement.buttons))
            if gap > 0:
                ops.append(Sleep(gap))
        return ops

    def _compile_stick(self, statement: StickMove) -> list[Op]:
        ops: list[Op] = [SetStick(stick=statement.stick, position=statement.position)]
        if statement.duration is not None:
            ops.append(Sleep(statement.duration))
            ops.append(SetStick(stick=statement.stick, position=StickState.centered()))
        return ops

    def _compile_dpad(self, statement: DpadMove) -> list[Op]:
        ops: list[Op] = [SetDpad(direction=statement.direction)]
        if statement.duration is not None:
            ops.append(Sleep(statement.duration))
            ops.append(SetDpad(direction=Dpad.NONE))
        return ops

    def _compile_loop(self, statement: Loop, call_stack: tuple[str, ...]) -> list[Op]:
        body = self.compile_block(statement.body, call_stack=call_stack)
        if not body or statement.count == 0:
            return []
        if statement.count == 1:
            return body
        return [Repeat(count=statement.count, body=tuple(body))]

    def _compile_call(self, statement: Call, call_stack: tuple[str, ...]) -> list[Op]:
        body = self.macro.definitions.get(statement.name)
        if body is None:
            known = ", ".join(sorted(self.macro.definitions)) or "none defined"
            raise MacroError(
                f"unknown block {statement.name!r} (known: {known})",
                source=statement.source,
                line=statement.line,
            )
        if statement.name in call_stack:
            chain = " -> ".join((*call_stack, statement.name))
            raise MacroError(
                f"recursive call: {chain}", source=statement.source, line=statement.line
            )
        if len(call_stack) >= MAX_CALL_DEPTH:
            raise MacroError(
                "call nesting is too deep", source=statement.source, line=statement.line
            )
        return self.compile_block(body, call_stack=(*call_stack, statement.name))


def compile_text(
    text: str,
    *,
    source: str | None = None,
    base_dir: str | None = None,
    press_time: float | None = None,
    gap: float | None = None,
) -> CompiledMacro:
    """Convenience helper: parse and compile macro text in one step."""
    from anyctrl.macro.parser import parse_macro

    parsed = parse_macro(text, source=source, base_dir=base_dir)
    return compile_macro(parsed, press_time=press_time, gap=gap)


def compile_file(
    path: str,
    *,
    press_time: float | None = None,
    gap: float | None = None,
) -> CompiledMacro:
    """Parse and compile a macro file."""
    from anyctrl.macro.parser import parse_macro_file

    parsed = parse_macro_file(path)
    return compile_macro(parsed, press_time=press_time, gap=gap)
