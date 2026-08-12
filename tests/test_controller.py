"""Buttons, controller state and their wire encodings."""

from __future__ import annotations

import pytest

from anyctrl.controller.buttons import Button, Dpad, Stick, parse_combo, parse_dpad, parse_stick
from anyctrl.controller.state import STICK_CENTER, STICK_RANGE, ControllerState, StickState
from anyctrl.errors import MacroError


def test_parse_combo_accepts_aliases_and_case():
    assert parse_combo("a") == frozenset({Button.A})
    assert parse_combo("ZL+zr") == frozenset({Button.ZL, Button.ZR})
    assert parse_combo("l3") == frozenset({Button.LSTICK})
    assert parse_combo("+") == frozenset({Button.PLUS})
    assert parse_combo("start") == frozenset({Button.PLUS})


def test_parse_combo_rejects_unknown_buttons():
    with pytest.raises(MacroError, match="unknown button"):
        parse_combo("Z")


def test_parse_stick_and_dpad():
    assert parse_stick("left") is Stick.LEFT
    assert parse_stick("R") is Stick.RIGHT
    assert parse_dpad("down-left") is Dpad.DOWN_LEFT
    assert parse_dpad("center") is Dpad.NONE
    with pytest.raises(MacroError):
        parse_stick("middle")


def test_button_bytes_match_the_documented_bit_layout():
    state = ControllerState(buttons={Button.A, Button.ZL, Button.PLUS})
    right, shared, left = state.button_bytes()
    assert right == 0x08  # A
    assert shared == 0x02  # +
    assert left == 0x80  # ZL


def test_stick_encoding_is_centred_and_symmetric():
    assert StickState().to_raw() == (STICK_CENTER, STICK_CENTER)
    assert StickState(1.0, -1.0).to_raw() == (
        STICK_CENTER + STICK_RANGE,
        STICK_CENTER - STICK_RANGE,
    )
    assert len(StickState(0.5, 0.5).encode3()) == 3

    # The 12+12 bit packing has to round-trip.
    encoded = StickState(0.25, -0.75).encode3()
    raw_x = encoded[0] | ((encoded[1] & 0x0F) << 8)
    raw_y = (encoded[1] >> 4) | (encoded[2] << 4)
    assert (raw_x, raw_y) == StickState(0.25, -0.75).to_raw()


def test_stick_values_are_clamped():
    assert StickState(5.0, -5.0) == StickState(1.0, -1.0)


def test_stick_from_angle_uses_compass_bearings():
    up = StickState.from_angle(0)
    right = StickState.from_angle(90)
    assert up.y == pytest.approx(1.0) and up.x == pytest.approx(0.0, abs=1e-9)
    assert right.x == pytest.approx(1.0) and right.y == pytest.approx(0.0, abs=1e-9)
    assert StickState.from_angle(90, 0.5).x == pytest.approx(0.5)


def test_dpad_collapses_direction_buttons_and_cancels_opposites():
    state = ControllerState(buttons={Button.UP, Button.LEFT})
    assert state.dpad() is Dpad.UP_LEFT
    state.press({Button.RIGHT})
    assert state.dpad() is Dpad.UP  # left and right cancel
    state.press({Button.DOWN})
    assert state.dpad() is Dpad.NONE


def test_set_dpad_replaces_the_previous_direction():
    state = ControllerState()
    state.set_dpad(Dpad.DOWN_RIGHT)
    assert state.buttons == {Button.DOWN, Button.RIGHT}
    state.set_dpad(Dpad.UP)
    assert state.buttons == {Button.UP}
    state.set_dpad(Dpad.NONE)
    assert state.buttons == set()


def test_usb_report_layout():
    state = ControllerState(buttons={Button.A, Button.HOME}, left_stick=StickState(1.0, 1.0))
    report = state.usb_report()
    assert len(report) == 8
    buttons = report[0] | (report[1] << 8)
    assert buttons == (1 << 2) | (1 << 12)
    assert report[2] == 8  # hat: neutral
    assert report[3] == 255  # left stick fully right
    assert report[4] == 1  # left stick fully up, in screen coordinates


def test_release_all_recentres_sticks():
    state = ControllerState(buttons={Button.A}, left_stick=StickState(1.0, 0.0))
    assert not state.is_neutral
    state.release_all()
    assert state.is_neutral


def test_copy_is_independent():
    state = ControllerState(buttons={Button.A})
    clone = state.copy()
    clone.press(Button.B)
    assert state.buttons == {Button.A}
