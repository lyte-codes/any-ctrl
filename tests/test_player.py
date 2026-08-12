"""Playback: timing, cancellation and cleanup."""

from __future__ import annotations

import threading

import pytest

from anyctrl.backends.dryrun import DryRunBackend
from anyctrl.controller.buttons import Button
from anyctrl.controller.state import ControllerState
from anyctrl.errors import BackendError, MacroAborted
from anyctrl.macro.compiler import compile_text
from anyctrl.macro.player import MacroPlayer


class FakeClock:
    """A monotonic clock that only moves when something sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def __call__(self) -> float:
        return self.now


def make_player(backend, **kwargs) -> tuple[MacroPlayer, FakeClock]:
    clock = FakeClock()
    player = MacroPlayer(backend, sleeper=clock.sleep, monotonic=clock, **kwargs)
    return player, clock


def test_playback_takes_exactly_as_long_as_the_macro_says():
    backend = DryRunBackend()
    backend.connect()
    macro = compile_text("press A 100ms\nwait 1s")
    player, clock = make_player(backend, tick=0.015)
    elapsed = player.play(macro)
    assert elapsed == pytest.approx(macro.duration())
    assert clock.now == pytest.approx(macro.duration())


def test_waits_are_split_into_ticks():
    backend = DryRunBackend()
    backend.connect()
    player, _clock = make_player(backend, tick=0.1)
    player.play(compile_text("wait 1s"))
    # One report per tick, plus the neutral state sent on the way out.
    assert len(backend.frames) == 11


def test_short_waits_still_send_at_least_one_report():
    backend = DryRunBackend()
    backend.connect()
    player, _clock = make_player(backend, tick=1.0)
    player.play(compile_text("wait 10ms"))
    assert len(backend.frames) >= 1


def test_speed_scales_playback_without_changing_the_inputs():
    fast_backend = DryRunBackend()
    fast_backend.connect()
    macro = compile_text("press A 100ms\nwait 400ms")
    player, clock = make_player(fast_backend, tick=0.01, speed=4.0)
    elapsed = player.play(macro)
    assert elapsed == pytest.approx(macro.duration() / 4)
    assert clock.now == pytest.approx(macro.duration() / 4)
    pressed = [frame for frame in fast_backend.transitions if frame.state.buttons]
    assert pressed and pressed[0].state.buttons == {Button.A}


def test_timing_does_not_drift_over_many_iterations():
    backend = DryRunBackend(keep_frames=False)
    backend.connect()
    macro = compile_text("loop 500 {\n  press A 20ms every 20ms\n}")
    player, clock = make_player(backend, tick=0.015)
    elapsed = player.play(macro)
    assert elapsed == pytest.approx(20.0)
    assert clock.now == pytest.approx(20.0)


def test_repeat_plays_the_whole_macro_again():
    backend = DryRunBackend()
    backend.connect()
    macro = compile_text("press A")
    player, _clock = make_player(backend, tick=0.02)
    elapsed = player.play(macro, repeat=3)
    assert elapsed == pytest.approx(macro.duration() * 3)


def test_forever_loops_run_until_cancelled():
    backend = DryRunBackend(keep_frames=False)
    backend.connect()
    cancel = threading.Event()
    macro = compile_text("loop forever {\n  press A\n}")

    class CountingClock(FakeClock):
        def sleep(self, seconds: float) -> None:
            super().sleep(seconds)
            if self.now > 5.0:
                cancel.set()

    clock = CountingClock()
    player = MacroPlayer(backend, tick=0.015, cancel=cancel, sleeper=clock.sleep, monotonic=clock)
    with pytest.raises(MacroAborted):
        player.play(macro)
    assert clock.now > 5.0


def test_timeout_stops_playback():
    backend = DryRunBackend(keep_frames=False)
    backend.connect()
    player, _clock = make_player(backend, tick=0.05, timeout=1.0)
    with pytest.raises(MacroAborted, match="exceeded"):
        player.play(compile_text("wait 10s"))


def test_everything_is_released_when_playback_is_cancelled():
    backend = DryRunBackend()
    backend.connect()
    cancel = threading.Event()
    cancel.set()
    player, _clock = make_player(backend, tick=0.05, cancel=cancel)
    with pytest.raises(MacroAborted):
        player.play(compile_text("hold A\nwait 10s"))
    assert backend.frames[-1].state.is_neutral


def test_everything_is_released_when_a_backend_fails():
    class FailingBackend(DryRunBackend):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def send_state(self, state: ControllerState) -> None:
            self.calls += 1
            if self.calls == 3:
                raise BackendError("cable yanked")
            super().send_state(state)

    backend = FailingBackend()
    backend.connect()
    player, _clock = make_player(backend, tick=0.05)
    with pytest.raises(BackendError):
        player.play(compile_text("hold A+B\nwait 10s"))
    assert backend.frames[-1].state.is_neutral


def test_listener_sees_logs_loops_and_states():
    backend = DryRunBackend()
    backend.connect()
    events: list[tuple[str, str]] = []
    player, _clock = make_player(
        backend, tick=0.05, listener=lambda event: events.append((event.kind, event.message))
    )
    player.play(compile_text('log "hello"\npress A'), repeat=2)
    kinds = [kind for kind, _ in events]
    assert kinds[0] == "start" and kinds[-1] == "finish"
    assert ("log", "hello") in events
    assert any(kind == "loop" for kind in kinds)
    assert any(kind == "state" for kind in kinds)


def test_invalid_player_settings_are_rejected():
    backend = DryRunBackend()
    with pytest.raises(ValueError):
        MacroPlayer(backend, tick=0)
    with pytest.raises(ValueError):
        MacroPlayer(backend, speed=0)
