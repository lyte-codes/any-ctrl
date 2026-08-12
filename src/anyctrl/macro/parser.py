"""Parser for the any-ctrl macro language.

The language is line based and deliberately small; see ``docs/macros.md`` for
the reference. A quick taste::

    meta name "Shop reset"

    def buy {
        press A 120ms
        wait 400ms
    }

    loop 5 {
        call buy
        stick L up for 1.2s
        press ZL+ZR
    }
"""

from __future__ import annotations

import os
from pathlib import Path

from anyctrl.controller.buttons import (
    STICK_DIRECTIONS,
    Stick,
    parse_combo,
    parse_dpad,
    parse_stick,
)
from anyctrl.controller.state import StickState
from anyctrl.errors import MacroError
from anyctrl.macro.model import (
    Call,
    DpadMove,
    Hold,
    Log,
    Loop,
    MacroFile,
    Press,
    Release,
    SetOption,
    Statement,
    StickMove,
    Wait,
)

MAX_INCLUDE_DEPTH = 16
SETTABLE_OPTIONS = {"press_time", "gap", "tick"}

_TIME_UNITS = {
    "ms": 0.001,
    "msec": 0.001,
    "s": 1.0,
    "sec": 1.0,
    "secs": 1.0,
    "seconds": 1.0,
    "m": 60.0,
    "min": 60.0,
    "mins": 60.0,
}


def parse_duration(token: str, *, line: int | None = None, source: str | None = None) -> float:
    """Parse ``500ms``/``1.5s``/``2m``/``250`` (bare numbers are milliseconds)."""
    text = token.strip().lower()
    if not text:
        raise MacroError("expected a duration", source=source, line=line)
    number, scale = text, 0.001  # a bare number means milliseconds
    for suffix, unit in sorted(_TIME_UNITS.items(), key=lambda item: -len(item[0])):
        if text.endswith(suffix):
            number, scale = text[: -len(suffix)].strip(), unit
            break
    try:
        value = float(number)
    except ValueError:
        raise MacroError(f"invalid duration {token!r}", source=source, line=line) from None
    if value < 0:
        raise MacroError(f"negative duration {token!r}", source=source, line=line)
    return value * scale


def _parse_float(token: str, *, line: int, source: str | None) -> float:
    try:
        return float(token)
    except ValueError:
        raise MacroError(f"expected a number, got {token!r}", source=source, line=line) from None


def _parse_int(token: str, *, line: int, source: str | None) -> int:
    try:
        return int(token, 0)
    except ValueError:
        raise MacroError(
            f"expected a whole number, got {token!r}", source=source, line=line
        ) from None


def _looks_like_duration(token: str) -> bool:
    text = token.strip().lower()
    if not text:
        return False
    if any(text.endswith(suffix) for suffix in _TIME_UNITS):
        return True
    try:
        float(text)
    except ValueError:
        return False
    return True


def tokenize(line: str) -> list[str]:
    """Split one source line into tokens, honouring quotes and comments."""
    return [text for text, _quoted in tokenize_marked(line)]


def tokenize_marked(line: str) -> list[tuple[str, bool]]:
    """Tokenize, remembering which tokens came from a quoted string.

    Quoting matters for exactly one thing: a brace inside a string is text,
    not a block delimiter, so ``log "}"`` behaves as written.
    """
    tokens: list[tuple[str, bool]] = []
    current = ""
    quote: str | None = None
    index = 0
    while index < len(line):
        char = line[index]
        if quote:
            if char == "\\" and index + 1 < len(line):
                current += line[index + 1]
                index += 2
                continue
            if char == quote:
                tokens.append((current, True))
                current = ""
                quote = None
            else:
                current += char
            index += 1
            continue
        if char in "\"'":
            quote = char
            index += 1
            continue
        if char == "#" or line.startswith("//", index):
            break
        if char in "{}":
            if current:
                tokens.append((current, False))
                current = ""
            tokens.append((char, False))
            index += 1
            continue
        if char.isspace():
            if current:
                tokens.append((current, False))
                current = ""
            index += 1
            continue
        current += char
        index += 1
    if quote:
        raise MacroError("unterminated string literal")
    if current:
        tokens.append((current, False))
    return tokens


class _Parser:
    def __init__(self, source: str | None, base_dir: Path, include_stack: tuple[Path, ...]):
        self.source = source
        self.base_dir = base_dir
        self.include_stack = include_stack
        self.file = MacroFile(source=source)
        # A stack of statement lists: the last entry receives new statements.
        self._blocks: list[list[Statement]] = [self.file.statements]
        self._block_kinds: list[str] = ["top"]

    # -- helpers ----------------------------------------------------------
    def _error(self, message: str, line: int) -> MacroError:
        return MacroError(message, source=self.source, line=line)

    def _emit(self, statement: Statement) -> None:
        self._blocks[-1].append(statement)

    def _push(self, kind: str, body: list[Statement]) -> None:
        self._blocks.append(body)
        self._block_kinds.append(kind)

    def _split_at_brace(self, tokens: list[str], line: int, keyword: str) -> tuple[list[str], int]:
        """Split ``loop 3 { press A`` into its header and the index after ``{``."""
        try:
            index = tokens.index("{")
        except ValueError:
            raise self._error(f"{keyword} must be followed by '{{'", line) from None
        return tokens[:index], index + 1

    # -- entry point ------------------------------------------------------
    def parse(self, text: str) -> MacroFile:
        for lineno, raw in enumerate(text.splitlines(), start=1):
            try:
                marked = tokenize_marked(raw)
            except MacroError as exc:
                raise self._error(str(exc), lineno) from None
            if not marked:
                continue
            self._parse_line([text for text, _ in marked], [q for _, q in marked], lineno)
        if len(self._blocks) > 1:
            raise self._error(f"unclosed '{self._block_kinds[-1]}' block", 0)
        return self.file

    def _parse_line(self, tokens: list[str], quoted: list[bool], line: int) -> None:
        """Parse one logical line, which may open or close blocks part way."""
        for index, (text, is_quoted) in enumerate(zip(tokens, quoted, strict=True)):
            if text != "}" or is_quoted:
                continue
            if index:
                self._parse_line(tokens[:index], quoted[:index], line)
            if len(self._blocks) == 1:
                raise self._error("unexpected '}'", line)
            self._blocks.pop()
            self._block_kinds.pop()
            if tokens[index + 1 :]:
                self._parse_line(tokens[index + 1 :], quoted[index + 1 :], line)
            return

        keyword = tokens[0].lower()
        handler = self._KEYWORDS.get(keyword)
        if handler is None:
            raise self._error(f"unknown statement {tokens[0]!r}", line)
        consumed = handler(self, tokens[1:], line)
        if consumed is None:
            return
        rest = 1 + consumed
        if tokens[rest:]:
            self._parse_line(tokens[rest:], quoted[rest:], line)

    # -- statements -------------------------------------------------------
    def _st_meta(self, args: list[str], line: int) -> None:
        if not args:
            raise self._error("meta needs a key", line)
        key = args[0].lower()
        self.file.meta.setdefault(key, " ".join(args[1:]))

    def _st_set(self, args: list[str], line: int) -> None:
        if len(args) != 2:
            raise self._error("set needs an option name and a value", line)
        name = args[0].lower()
        if name not in SETTABLE_OPTIONS:
            options = ", ".join(sorted(SETTABLE_OPTIONS))
            raise self._error(f"unknown option {args[0]!r} (known: {options})", line)
        value = parse_duration(args[1], line=line, source=self.source)
        self._emit(SetOption(name=name, value=value, line=line, source=self.source))

    def _st_def(self, args: list[str], line: int) -> int:
        if self._block_kinds[-1] != "top":
            raise self._error("def blocks cannot be nested", line)
        header, consumed = self._split_at_brace(args, line, "def")
        if len(header) != 1:
            raise self._error("def needs exactly one name", line)
        name = header[0]
        if name in self.file.definitions:
            raise self._error(f"block {name!r} is already defined", line)
        body: list[Statement] = []
        self.file.definitions[name] = body
        self._push("def", body)
        return consumed

    def _st_loop(self, args: list[str], line: int) -> int:
        header, consumed = self._split_at_brace(args, line, "loop")
        if len(header) != 1:
            raise self._error("loop needs a count or 'forever'", line)
        if header[0].lower() in {"forever", "always", "infinite"}:
            count = None
        else:
            count = _parse_int(header[0], line=line, source=self.source)
            if count < 0:
                raise self._error("loop count cannot be negative", line)
        loop = Loop(count=count, body=[], line=line, source=self.source)
        self._emit(loop)
        self._push("loop", loop.body)
        return consumed

    def _st_call(self, args: list[str], line: int) -> None:
        if len(args) != 1:
            raise self._error("call needs exactly one block name", line)
        self._emit(Call(name=args[0], line=line, source=self.source))

    def _st_press(self, args: list[str], line: int) -> None:
        if not args:
            raise self._error("press needs a button", line)
        buttons = parse_combo(args[0], line=line, source=self.source)
        duration: float | None = None
        repeat = 1
        interval: float | None = None
        rest = args[1:]
        while rest:
            token = rest.pop(0)
            low = token.lower()
            if low in {"for", "hold"}:
                if not rest:
                    raise self._error(f"'{low}' needs a duration", line)
                duration = parse_duration(rest.pop(0), line=line, source=self.source)
            elif low in {"every", "interval", "gap"}:
                if not rest:
                    raise self._error(f"'{low}' needs a duration", line)
                interval = parse_duration(rest.pop(0), line=line, source=self.source)
            elif low.startswith("x") and low[1:]:
                repeat = _parse_int(low[1:], line=line, source=self.source)
            elif low in {"x", "times"}:
                if not rest:
                    raise self._error("'x' needs a repeat count", line)
                repeat = _parse_int(rest.pop(0), line=line, source=self.source)
            elif _looks_like_duration(token):
                duration = parse_duration(token, line=line, source=self.source)
            else:
                raise self._error(f"unexpected {token!r} after press", line)
        if repeat < 1:
            raise self._error("press repeat count must be at least 1", line)
        self._emit(
            Press(
                buttons=buttons,
                duration=duration,
                repeat=repeat,
                interval=interval,
                line=line,
                source=self.source,
            )
        )

    def _st_hold(self, args: list[str], line: int) -> None:
        if len(args) != 1:
            raise self._error("hold needs exactly one button combination", line)
        buttons = parse_combo(args[0], line=line, source=self.source)
        self._emit(Hold(buttons=buttons, line=line, source=self.source))

    def _st_release(self, args: list[str], line: int) -> None:
        if not args or args[0].lower() in {"all", "everything"}:
            self._emit(Release(buttons=None, line=line, source=self.source))
            return
        if len(args) != 1:
            raise self._error("release needs one button combination or 'all'", line)
        buttons = parse_combo(args[0], line=line, source=self.source)
        self._emit(Release(buttons=buttons, line=line, source=self.source))

    def _st_wait(self, args: list[str], line: int) -> None:
        if len(args) != 1:
            raise self._error("wait needs exactly one duration", line)
        seconds = parse_duration(args[0], line=line, source=self.source)
        self._emit(Wait(seconds=seconds, line=line, source=self.source))

    def _st_stick(self, args: list[str], line: int) -> None:
        if len(args) < 2:
            raise self._error("stick needs a side and a position", line)
        stick: Stick = parse_stick(args[0], line=line, source=self.source)
        rest = args[1:]
        duration: float | None = None

        # Trailing "for <duration>" applies to every position form below.
        if len(rest) >= 2 and rest[-2].lower() in {"for", "during"}:
            duration = parse_duration(rest[-1], line=line, source=self.source)
            rest = rest[:-2]
        if not rest:
            raise self._error("stick needs a position", line)

        head = rest[0].lower()
        if head in {"angle", "deg", "degrees"}:
            if len(rest) < 2:
                raise self._error("stick angle needs a value in degrees", line)
            degrees = _parse_float(rest[1], line=line, source=self.source)
            magnitude = (
                _parse_float(rest[2], line=line, source=self.source) if len(rest) > 2 else 1.0
            )
            position = StickState.from_angle(degrees, magnitude)
        elif head.upper().replace("-", "_") in STICK_DIRECTIONS:
            unit_x, unit_y = STICK_DIRECTIONS[head.upper().replace("-", "_")]
            magnitude = (
                _parse_float(rest[1], line=line, source=self.source) if len(rest) > 1 else 1.0
            )
            position = StickState(unit_x * magnitude, unit_y * magnitude)
        elif len(rest) >= 2:
            position = StickState(
                _parse_float(rest[0], line=line, source=self.source),
                _parse_float(rest[1], line=line, source=self.source),
            )
        else:
            raise self._error(f"cannot read stick position from {rest[0]!r}", line)

        self._emit(
            StickMove(
                stick=stick, position=position, duration=duration, line=line, source=self.source
            )
        )

    def _st_dpad(self, args: list[str], line: int) -> None:
        if not args:
            raise self._error("dpad needs a direction", line)
        direction = parse_dpad(args[0], line=line, source=self.source)
        duration = None
        if len(args) > 1:
            token = args[-1]
            if args[1].lower() == "for" and len(args) > 2:
                token = args[2]
            duration = parse_duration(token, line=line, source=self.source)
        self._emit(DpadMove(direction=direction, duration=duration, line=line, source=self.source))

    def _st_log(self, args: list[str], line: int) -> None:
        self._emit(Log(message=" ".join(args), line=line, source=self.source))

    def _st_include(self, args: list[str], line: int) -> None:
        if len(args) != 1:
            raise self._error("include needs exactly one path", line)
        path = (self.base_dir / args[0]).resolve()
        if len(self.include_stack) >= MAX_INCLUDE_DEPTH:
            raise self._error("include nesting is too deep", line)
        if path in self.include_stack:
            raise self._error(f"circular include of {args[0]!r}", line)
        if not path.is_file():
            raise self._error(f"included macro not found: {args[0]}", line)
        included = parse_macro_file(path, include_stack=(*self.include_stack, path))
        for name, body in included.definitions.items():
            if name in self.file.definitions:
                raise self._error(f"block {name!r} is already defined", line)
            self.file.definitions[name] = body
        for key, value in included.meta.items():
            self.file.meta.setdefault(key, value)
        self._blocks[-1].extend(included.statements)

    _KEYWORDS = {
        "meta": _st_meta,
        "set": _st_set,
        "def": _st_def,
        "block": _st_def,
        "loop": _st_loop,
        "repeat": _st_loop,
        "call": _st_call,
        "press": _st_press,
        "tap": _st_press,
        "click": _st_press,
        "hold": _st_hold,
        "down": _st_hold,
        "release": _st_release,
        "up": _st_release,
        "wait": _st_wait,
        "sleep": _st_wait,
        "delay": _st_wait,
        "stick": _st_stick,
        "dpad": _st_dpad,
        "log": _st_log,
        "echo": _st_log,
        "include": _st_include,
    }


def parse_macro(
    text: str, *, source: str | None = None, base_dir: str | os.PathLike | None = None
) -> MacroFile:
    """Parse macro text into a :class:`MacroFile`."""
    directory = Path(base_dir) if base_dir is not None else Path.cwd()
    return _Parser(source, directory, ()).parse(text)


def parse_macro_file(path: str | os.PathLike, *, include_stack: tuple[Path, ...] = ()) -> MacroFile:
    """Parse a macro from disk, resolving ``include`` relative to it."""
    file_path = Path(path).expanduser()
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MacroError(f"cannot read macro: {exc}", source=str(path)) from None
    resolved = file_path.resolve()
    parser = _Parser(str(file_path), resolved.parent, include_stack or (resolved,))
    return parser.parse(text)
