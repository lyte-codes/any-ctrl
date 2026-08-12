"""Exception hierarchy shared by every layer of any-ctrl."""

from __future__ import annotations


class AnyCtrlError(Exception):
    """Base class for every error raised by any-ctrl."""


class MacroError(AnyCtrlError):
    """A macro could not be parsed or compiled."""

    def __init__(self, message: str, *, source: str | None = None, line: int | None = None):
        self.source = source
        self.line = line
        location = ""
        if source is not None:
            location = f"{source}:{line}: " if line is not None else f"{source}: "
        elif line is not None:
            location = f"line {line}: "
        super().__init__(f"{location}{message}")


class MacroAborted(AnyCtrlError):
    """Playback was cancelled (Ctrl-C, timeout, or a caller supplied event)."""


class BackendError(AnyCtrlError):
    """A backend could not be opened, or lost its connection to the console."""


class BackendUnavailable(BackendError):
    """A backend cannot run here: missing dependency, wrong OS, no permission."""
