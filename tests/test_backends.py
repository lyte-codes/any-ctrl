"""Backend registry, the dry-run backend and the serial bridge framing."""

from __future__ import annotations

import sys

import pytest

from anyctrl.backends import (
    BACKENDS,
    DryRunBackend,
    all_statuses,
    available_backends,
    create_backend,
    get_backend_class,
)
from anyctrl.backends.base import Console, ConsoleProfile
from anyctrl.backends.bluez import BluezBackend, build_sdp_record
from anyctrl.backends.serial_bridge import (
    MSG_STATE,
    SerialBridgeBackend,
    decode_frames,
    encode_frame,
)
from anyctrl.controller.buttons import Button
from anyctrl.controller.state import ControllerState
from anyctrl.errors import BackendUnavailable


def test_registry_lookup():
    assert get_backend_class("dryrun") is DryRunBackend
    assert set(BACKENDS) == {"bluez", "serial", "dryrun"}
    with pytest.raises(BackendUnavailable, match="unknown backend"):
        get_backend_class("telepathy")


def test_statuses_cover_every_backend():
    names = {status.name for status in all_statuses()}
    assert names == set(BACKENDS)


def test_create_backend_only_passes_options_the_backend_understands():
    backend = create_backend("dryrun", console=Console.SWITCH2, port="/dev/nonsense", baud=9600)
    assert isinstance(backend, DryRunBackend)
    assert backend.console is Console.SWITCH2


def test_auto_raises_a_helpful_error_when_nothing_is_available(monkeypatch):
    monkeypatch.setattr("anyctrl.backends.available_backends", lambda: [])
    with pytest.raises(BackendUnavailable, match="anyctrl doctor"):
        create_backend("auto")


def test_auto_prefers_bluetooth_over_a_usb_bridge(monkeypatch):
    monkeypatch.setattr(BluezBackend, "status", classmethod(lambda cls: _ok(cls)))
    monkeypatch.setattr(SerialBridgeBackend, "status", classmethod(lambda cls: _ok(cls)))
    assert available_backends() == ["bluez", "serial"]


def _ok(cls):
    from anyctrl.backends.base import BackendStatus

    return BackendStatus(cls.name, cls.description, True, "forced for the test")


def test_switch2_profile_waits_longer_after_the_handshake():
    one = ConsoleProfile.for_console(Console.SWITCH1)
    two = ConsoleProfile.for_console(Console.SWITCH2)
    assert two.settle > one.settle
    assert two.pair_timeout >= one.pair_timeout
    assert one.tick == two.tick == 0.015


def test_dryrun_records_states_and_transitions():
    seen = []
    backend = DryRunBackend(sink=seen.append)
    backend.connect()
    assert backend.connected
    state = ControllerState()
    backend.send_state(state)
    backend.send_state(state)  # unchanged: not a transition
    state.press(Button.A)
    backend.send_state(state)
    backend.close()
    assert len(backend.frames) == 3
    assert len(backend.transitions) == 2
    assert len(seen) == 2
    assert not backend.connected


def test_dryrun_snapshots_are_immune_to_later_mutation():
    backend = DryRunBackend()
    backend.connect()
    state = ControllerState()
    backend.send_state(state)
    state.press(Button.A)
    assert backend.frames[0].state.buttons == set()


def test_backend_context_manager_connects_and_closes():
    with DryRunBackend() as backend:
        assert backend.connected
    assert not backend.connected


# -- serial bridge framing --------------------------------------------------


def test_frame_round_trip():
    payload = bytes(range(8))
    buffer = bytearray(encode_frame(MSG_STATE, payload))
    assert decode_frames(buffer) == [(MSG_STATE, payload)]
    assert not buffer


def test_partial_frames_stay_in_the_buffer():
    frame = encode_frame(MSG_STATE, b"\x01\x02")
    buffer = bytearray(frame[:-1])
    assert decode_frames(buffer) == []
    buffer += frame[-1:]
    assert decode_frames(buffer) == [(MSG_STATE, b"\x01\x02")]


def test_decoder_resynchronises_after_corruption():
    good = encode_frame(MSG_STATE, b"\xaa")
    corrupted = bytearray(b"\xa5\x02\x01\x00\x00" + good)  # bad checksum, then a good frame
    assert decode_frames(corrupted) == [(MSG_STATE, b"\xaa")]


def test_decoder_drops_leading_noise():
    buffer = bytearray(b"junk" + encode_frame(MSG_STATE, b"\x01"))
    assert decode_frames(buffer) == [(MSG_STATE, b"\x01")]


def test_frames_reject_oversized_payloads():
    with pytest.raises(ValueError):
        encode_frame(MSG_STATE, b"\x00" * 256)


def test_state_encodes_into_a_bridge_frame():
    state = ControllerState(buttons={Button.A})
    frame = encode_frame(MSG_STATE, state.usb_report())
    decoded = decode_frames(bytearray(frame))
    assert decoded[0][1] == state.usb_report()


# -- bluez ------------------------------------------------------------------


def test_sdp_record_advertises_hid_and_both_psms():
    record = build_sdp_record()
    assert '<uuid value="0x1124" />' in record  # HumanInterfaceDevice
    assert '<uint16 value="0x0011" />' in record  # control PSM 17
    assert '<uint16 value="0x0013" />' in record  # interrupt PSM 19
    assert "Pro Controller" in record


def test_sdp_record_embeds_the_report_descriptor():
    record = build_sdp_record(descriptor=bytes((0x05, 0x01, 0xC0)))
    assert '<text encoding="hex" value="0501c0" />' in record


@pytest.mark.skipif(sys.platform == "linux", reason="checks the non-Linux message")
def test_bluez_is_unavailable_off_linux():
    status = BluezBackend.status()
    assert not status.available
    assert "Linux only" in status.detail
