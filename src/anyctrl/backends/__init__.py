"""Backend registry and selection."""

from __future__ import annotations

import inspect
from typing import Any

from anyctrl.backends.base import BackendStatus, Console, ConsoleProfile, ControllerBackend
from anyctrl.backends.bluez import BluezBackend
from anyctrl.backends.dryrun import DryRunBackend
from anyctrl.backends.serial_bridge import SerialBridgeBackend
from anyctrl.errors import BackendUnavailable

__all__ = [
    "BackendStatus",
    "BluezBackend",
    "Console",
    "ConsoleProfile",
    "ControllerBackend",
    "DryRunBackend",
    "SerialBridgeBackend",
    "all_statuses",
    "available_backends",
    "create_backend",
    "get_backend_class",
]

#: Registry, in the order ``auto`` prefers them: a direct Bluetooth connection
#: first, then a USB bridge, and never ``dryrun`` unless it is asked for.
BACKENDS: dict[str, type[ControllerBackend]] = {
    "bluez": BluezBackend,
    "serial": SerialBridgeBackend,
    "dryrun": DryRunBackend,
}

AUTO_ORDER = ("bluez", "serial")


def get_backend_class(name: str) -> type[ControllerBackend]:
    """Look up a backend by name."""
    try:
        return BACKENDS[name.lower()]
    except KeyError:
        known = ", ".join(BACKENDS)
        raise BackendUnavailable(f"unknown backend {name!r} (known: {known})") from None


def all_statuses() -> list[BackendStatus]:
    """Report the state of every backend on this machine."""
    return [backend.status() for backend in BACKENDS.values()]


def available_backends() -> list[str]:
    """Names of the backends that could be used right now, best first."""
    return [name for name in AUTO_ORDER if BACKENDS[name].status().available]


def create_backend(name: str = "auto", /, **options: Any) -> ControllerBackend:
    """Instantiate a backend, passing only the options it understands.

    ``name="auto"`` picks the best backend available on this machine, which
    keeps the same command working on a Linux box with Bluetooth and on a Mac
    with a USB bridge plugged in.
    """
    if name == "auto":
        candidates = available_backends()
        if not candidates:
            raise BackendUnavailable(
                "no usable backend found. On Linux run as root with "
                "'pip install any-ctrl[bluez]'; on macOS connect a USB bridge board and "
                "'pip install any-ctrl[serial]'. Run 'anyctrl doctor' for details."
            )
        name = candidates[0]
        # Only automatic selection is gated on readiness. Naming a backend
        # explicitly is taken as "I know what I am doing" - the user may be
        # pointing at a port autodetection cannot recognise - and any real
        # problem surfaces from connect() with a specific message.
        status = BACKENDS[name].status()
        if not status.available:  # pragma: no cover - available_backends filters these
            raise BackendUnavailable(f"backend {name!r} is unavailable: {status.detail}")

    backend_class = get_backend_class(name)
    parameters = inspect.signature(backend_class.__init__).parameters
    accepted = {key: value for key, value in options.items() if key in parameters}
    return backend_class(**accepted)
