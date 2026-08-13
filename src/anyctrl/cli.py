"""Command line interface: ``anyctrl``."""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import sys
import threading
from pathlib import Path

from anyctrl import __version__
from anyctrl.backends import (
    BACKENDS,
    Console,
    ControllerBackend,
    all_statuses,
    available_backends,
    create_backend,
)
from anyctrl.backends.serial_bridge import list_ports
from anyctrl.controller.buttons import Button, parse_combo
from anyctrl.errors import AnyCtrlError, MacroAborted, MacroError
from anyctrl.macro.compiler import compile_file, compile_text
from anyctrl.macro.model import CompiledMacro
from anyctrl.macro.parser import parse_duration
from anyctrl.macro.player import DEFAULT_TICK, MacroPlayer, PlaybackEvent


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns a process exit status."""
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:  # pragma: no cover - argparse always sets one
        parser.print_help()
        return 2
    try:
        return handler(args)
    except MacroAborted as exc:
        print(f"\nstopped: {exc}", file=sys.stderr)
        return 130
    except AnyCtrlError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:  # pragma: no cover - interactive
        print("\ninterrupted", file=sys.stderr)
        return 130


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="anyctrl",
        description="Play controller macros on a Nintendo Switch (1 or 2) from Linux or macOS.",
    )
    parser.add_argument("--version", action="version", version=f"any-ctrl {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    run = subparsers.add_parser("run", help="play a macro file")
    run.add_argument("macro", help="path to a .macro file, or '-' to read stdin")
    _add_connection_options(run)
    _add_timing_options(run)
    run.add_argument("--repeat", type=int, default=1, help="play the whole macro N times")
    run.add_argument(
        "--timeout",
        type=str,
        default=None,
        help="stop after this much playback time, after --speed scaling (e.g. 30m)",
    )
    run.add_argument("--quiet", "-q", action="store_true", help="only print errors")
    run.add_argument(
        "--show-inputs", action="store_true", help="print every controller state change"
    )
    run.set_defaults(handler=cmd_run)

    check = subparsers.add_parser("check", help="parse a macro and describe what it would do")
    check.add_argument("macro", help="path to a .macro file, or '-' to read stdin")
    _add_timing_options(check)
    check.add_argument("--timeline", action="store_true", help="print the full input timeline")
    check.add_argument(
        "--limit", type=int, default=40, help="maximum timeline rows to print (0 = all)"
    )
    check.set_defaults(handler=cmd_check)

    press = subparsers.add_parser("press", help="press a button combination once")
    press.add_argument("combo", help="a button or combination, e.g. A or ZL+ZR")
    press.add_argument("--duration", default="100ms", help="how long to hold it")
    press.add_argument("--repeat", type=int, default=1, help="press it N times")
    _add_connection_options(press)
    _add_timing_options(press)
    press.set_defaults(handler=cmd_press)

    backends = subparsers.add_parser("backends", help="list backends and whether they work here")
    backends.set_defaults(handler=cmd_backends)

    ports = subparsers.add_parser("ports", help="list serial ports that could host a bridge")
    ports.set_defaults(handler=cmd_ports)

    doctor = subparsers.add_parser("doctor", help="diagnose this machine's setup")
    doctor.set_defaults(handler=cmd_doctor)

    buttons = subparsers.add_parser("buttons", help="list the button names macros accept")
    buttons.set_defaults(handler=cmd_buttons)

    return parser


def _add_connection_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--backend",
        default="auto",
        choices=["auto", *BACKENDS],
        help="how to reach the console (default: auto)",
    )
    parser.add_argument(
        "--console",
        default=Console.SWITCH1.value,
        choices=[console.value for console in Console],
        help="console generation; affects handshake timing (default: switch1)",
    )
    parser.add_argument("--adapter", default="hci0", help="bluez: local adapter (default: hci0)")
    parser.add_argument(
        "--address",
        default=None,
        help="bluez: reconnect to a console that already paired with this machine",
    )
    parser.add_argument("--port", default=None, help="serial: bridge serial port")
    parser.add_argument("--baud", type=int, default=115200, help="serial: baud rate")
    parser.add_argument(
        "--connect-timeout", default=None, help="how long to wait for the console (e.g. 60s)"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="explain what is happening")


def _add_timing_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--speed", type=float, default=1.0, help="playback speed multiplier")
    parser.add_argument(
        "--tick", default=None, help=f"report interval (default: {DEFAULT_TICK * 1000:g}ms)"
    )
    parser.add_argument("--press-time", default=None, help="default hold time for 'press'")
    parser.add_argument("--gap", default=None, help="default pause after 'press'")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_run(args: argparse.Namespace, macro: CompiledMacro | None = None) -> int:
    macro = macro if macro is not None else _load_macro(args)
    tick = _tick_for(args, macro)
    timeout = parse_duration(args.timeout) if args.timeout else None
    console = Console(args.console)

    backend = create_backend(
        args.backend,
        console=console,
        adapter=args.adapter,
        reconnect_address=args.address,
        port=args.port,
        baud=args.baud,
        verbose=args.verbose,
    )
    connect_timeout = parse_duration(args.connect_timeout) if args.connect_timeout else None

    if not args.quiet:
        total = macro.duration()
        length = "endless" if total == float("inf") else f"{total:.1f}s"
        print(f"macro: {macro.name} ({length}, {len(macro.ops)} ops)")
        print(f"backend: {backend.name}, console: {console}")
        _print_connect_hint(backend)
        # Milestones are printed as they happen, so "waiting for the console"
        # never appears before we are genuinely listening for one.
        backend.on_status = lambda message: print(f"  {message}")

    cancel = threading.Event()
    listener = _make_listener(args, quiet=args.quiet)
    exit_code = 0
    try:
        backend.connect(timeout=connect_timeout)
    except AnyCtrlError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not args.quiet:
        print("connected. playing (Ctrl-C to stop)")

    with _sigint_handler(cancel):
        player = MacroPlayer(
            backend,
            tick=tick,
            speed=args.speed,
            listener=listener,
            cancel=cancel,
            timeout=timeout,
        )
        try:
            elapsed = player.play(macro, repeat=args.repeat)
            if not args.quiet:
                print(f"done after {elapsed:.1f}s of playback")
        except MacroAborted as exc:
            print(f"\nstopped: {exc}", file=sys.stderr)
            exit_code = 130
        except AnyCtrlError as exc:
            print(f"error: {exc}", file=sys.stderr)
            exit_code = 1
        finally:
            backend.close()
    return exit_code


def cmd_check(args: argparse.Namespace) -> int:
    from anyctrl.backends.dryrun import DryRunBackend

    macro = _load_macro(args)
    total = macro.duration()
    length = "endless" if total == float("inf") else f"{total:.2f}s"
    print(f"macro: {macro.name}")
    if macro.source:
        print(f"source: {macro.source}")
    for key, value in sorted(macro.meta.items()):
        print(f"meta.{key}: {value}")
    print(f"instructions: {len(macro.ops)}")
    print(f"duration: {length}")

    if not args.timeline:
        return 0
    if macro.is_endless:
        print("timeline: not shown, the macro never ends")
        return 0

    rows: list[tuple[float, str]] = []

    def record(event: PlaybackEvent) -> None:
        if event.kind == "log" and event.message:
            rows.append((event.elapsed, f"log: {event.message}"))
        elif event.kind == "state" and event.state is not None:
            description = event.state.describe()
            if not rows or rows[-1][1] != description:
                rows.append((event.elapsed, description))

    backend = DryRunBackend(keep_frames=False)
    backend.connect()
    player = MacroPlayer(
        backend,
        tick=_tick_for(args, macro),
        speed=args.speed,
        listener=record,
        sleeper=lambda _seconds: None,
        monotonic=lambda: 0.0,
    )
    player.play(macro)
    backend.close()

    print(f"\ntimeline ({len(rows)} state changes):")
    shown = rows if args.limit in (0, None) else rows[: args.limit]
    for elapsed, description in shown:
        print(f"  {elapsed:7.3f}s  {description}")
    if len(shown) < len(rows):
        print(f"  ... {len(rows) - len(shown)} more (use --limit 0 to see all)")
    return 0


def cmd_press(args: argparse.Namespace) -> int:
    combo = parse_combo(args.combo)
    names = "+".join(sorted(button.value for button in combo))
    text = f"press {names} {args.duration} x{args.repeat}\n"
    macro = compile_text(text, source="<press>")
    namespace = argparse.Namespace(**vars(args))
    namespace.macro = None
    namespace.quiet = False
    namespace.show_inputs = False
    namespace.timeout = None
    namespace.repeat = 1
    return _run_compiled(namespace, macro)


def cmd_backends(args: argparse.Namespace) -> int:
    usable = available_backends()
    for status in all_statuses():
        mark = "+" if status.available else "-"
        print(f"{mark} {status.name:8} {status.description}")
        if status.detail:
            print(f"           {status.detail}")
    print()
    print(f"auto would use: {usable[0] if usable else 'nothing (see anyctrl doctor)'}")
    return 0


def cmd_ports(args: argparse.Namespace) -> int:
    ports = list_ports()
    if not ports:
        print("no serial ports found")
        print("install pyserial with: pip install 'any-ctrl[serial]'")
        return 1
    for port in ports:
        mark = "+" if port.is_candidate else "-"
        print(f"{mark} {port}")
    candidates = [port for port in ports if port.is_candidate]
    print()
    if candidates:
        print(f"bridge candidate: {candidates[0].device}")
        return 0
    print("no bridge adapter found among these ports.")
    print("The host talks to the board through a USB-to-serial adapter (CH340, CP2102,")
    print("FTDI); the board's own USB port goes to the console. See firmware/README.md.")
    return 1


def cmd_buttons(args: argparse.Namespace) -> int:
    print("buttons:")
    print("  " + " ".join(button.value for button in Button))
    print("\ncombine them with '+', for example: ZL+ZR or A+B")
    print("sticks:  stick L up | stick R 0.5 -1.0 | stick L angle 45 0.8")
    print("d-pad:   dpad up | dpad down_left 500ms")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    print(f"any-ctrl {__version__} on {sys.platform}, python {sys.version.split()[0]}")
    print()
    print("backends")
    for status in all_statuses():
        mark = "ok " if status.available else "no "
        print(f"  {mark} {status.name}: {status.detail or status.description}")

    problems = 0
    if sys.platform == "linux":
        print("\nlinux bluetooth")
        if os.geteuid() != 0:
            problems += 1
            print("  no  not running as root (needed to bind L2CAP PSM 17 and 19)")
            print("      try: sudo -E anyctrl run ...")
        else:
            print("  ok  running as root")
        for tool in ("hciconfig", "btmgmt", "bluetoothctl"):
            path = shutil.which(tool)
            print(f"  {'ok ' if path else 'no '} {tool}: {path or 'not found'}")
        plugin_state = _bluetoothd_input_plugin_state()
        if plugin_state is None:
            problems += 1
            print("  no  bluetoothd does not appear to be running")
        elif plugin_state:
            problems += 1
            print("  no  bluetoothd is running with its input plugin enabled")
            print("      it will grab the HID PSMs; see docs/troubleshooting.md")
        else:
            print("  ok  bluetoothd is running without the input plugin")
    elif sys.platform == "darwin":
        print("\nmacos")
        print("  --  macOS cannot act as a Bluetooth HID device; use the serial bridge")
        ports = list_ports()
        candidates = [port for port in ports if port.is_candidate]
        if ports:
            print(f"  --  {len(ports)} serial port(s):")
            for port in ports:
                print(f"        {port}")
        else:
            print("  no  no serial ports found")
        if candidates:
            print(f"  ok  bridge adapter detected: {candidates[0].device}")
        else:
            problems += 1
            print("  no  no bridge adapter among them")
            print("      the host connects to the board's UART through a USB-to-serial")
            print("      adapter; the board's own USB goes to the console")

    print()
    usable = available_backends()
    if usable:
        print(f"ready: auto would use the {usable[0]} backend")
    else:
        problems += 1
        print("not ready: no backend can run here yet")
    return 1 if problems else 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_macro(args: argparse.Namespace) -> CompiledMacro:
    press_time = parse_duration(args.press_time) if args.press_time else None
    gap = parse_duration(args.gap) if args.gap else None
    if args.macro == "-":
        text = sys.stdin.read()
        return compile_text(text, source="<stdin>", press_time=press_time, gap=gap)
    path = Path(args.macro).expanduser()
    if not path.exists():
        raise MacroError(f"macro not found: {args.macro}")
    return compile_file(str(path), press_time=press_time, gap=gap)


def _tick_for(args: argparse.Namespace, macro: CompiledMacro) -> float:
    if getattr(args, "tick", None):
        return parse_duration(args.tick)
    if "tick" in macro.meta:
        return parse_duration(macro.meta["tick"])
    return DEFAULT_TICK


def _run_compiled(args: argparse.Namespace, macro: CompiledMacro) -> int:
    """Play an already compiled macro using the connection options in ``args``."""
    return cmd_run(args, macro)


def _make_listener(args: argparse.Namespace, *, quiet: bool):
    if quiet:
        return None

    def listener(event: PlaybackEvent) -> None:
        if event.kind == "log" and event.message:
            print(f"  [{event.elapsed:7.2f}s] {event.message}")
        elif event.kind == "loop":
            print(f"  [{event.elapsed:7.2f}s] {event.message}")
        elif event.kind == "state" and args.show_inputs and event.state is not None:
            print(f"  [{event.elapsed:7.2f}s] {event.state.describe()}")

    return listener


def _print_connect_hint(backend: ControllerBackend) -> None:
    """Say what is about to happen. The backend reports what *has* happened."""
    if backend.name == "bluez":
        reconnecting = getattr(backend, "reconnect_address", None)
        if reconnecting:
            print(f"connecting to {reconnecting}: the console must be awake, on HOME")
        else:
            print("setting the adapter up; it will say when the console can find it")
    elif backend.name == "serial":
        print("opening the bridge; the board should be plugged into the console")


class _sigint_handler:
    """Turn the first Ctrl-C into a clean stop and the second into an exit."""

    def __init__(self, cancel: threading.Event):
        self.cancel = cancel
        self._previous = None

    def __enter__(self) -> _sigint_handler:
        def handle(signum, frame):  # pragma: no cover - interactive
            if self.cancel.is_set():
                raise KeyboardInterrupt
            self.cancel.set()

        try:
            self._previous = signal.signal(signal.SIGINT, handle)
        except ValueError:  # pragma: no cover - not on the main thread
            self._previous = None
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._previous is not None:
            try:
                signal.signal(signal.SIGINT, self._previous)
            except ValueError:  # pragma: no cover - not on the main thread
                pass


def _bluetoothd_input_plugin_state() -> bool | None:
    """Is bluetoothd running, and is its input plugin enabled?

    Returns ``None`` when no ``bluetoothd`` process is visible, ``True`` when
    one is running with the input plugin enabled (which steals the HID PSMs we
    need), and ``False`` when it was started with the plugin disabled.
    """
    proc = Path("/proc")
    if not proc.is_dir():  # pragma: no cover - non Linux
        return None
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().split(b"\x00")
        except OSError:
            continue
        if not cmdline or not cmdline[0]:
            continue
        if not cmdline[0].decode("utf-8", "replace").endswith("bluetoothd"):
            continue
        arguments = [part.decode("utf-8", "replace") for part in cmdline[1:] if part]
        return not _input_plugin_disabled(arguments)
    return None


def _input_plugin_disabled(arguments: list[str]) -> bool:
    """Does this ``bluetoothd`` argument list disable the input plugin?"""
    for index, argument in enumerate(arguments):
        value = None
        if argument.startswith("--noplugin="):
            value = argument.split("=", 1)[1]
        elif argument.startswith("-P") and len(argument) > 2:
            value = argument[2:]
        elif argument in {"-P", "--noplugin"} and index + 1 < len(arguments):
            value = arguments[index + 1]
        if value is not None and ("input" in value.split(",") or value.strip() == "*"):
            return True
    return False


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
