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
from anyctrl.errors import BackendError, BackendUnavailable


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


# -- class of device --------------------------------------------------------


def test_device_class_splits_into_major_and_minor():
    from anyctrl.backends.bluez import PRO_CONTROLLER_CLASS, split_device_class

    # 0x002508: peripheral (major 5), gamepad (minor 2). Masking the low byte
    # instead of shifting would give minor 8, which is a different device
    # entirely and one the console does not treat as a controller.
    assert split_device_class(PRO_CONTROLLER_CLASS) == (5, 2)


def test_device_class_round_trips_through_the_split():
    from anyctrl.backends.bluez import PRO_CONTROLLER_CLASS, split_device_class

    major, minor = split_device_class(PRO_CONTROLLER_CLASS)
    assert (major << 8) | (minor << 2) == PRO_CONTROLLER_CLASS & 0x1FFF


def test_enforce_device_class_retries_then_gives_up(monkeypatch):
    from anyctrl.backends.bluez import _AdapterConfig

    config = _AdapterConfig("hci0")
    writes: list[int] = []
    monkeypatch.setattr(config, "_write_device_class", lambda: writes.append(1))
    monkeypatch.setattr(config, "read_device_class", lambda: 0x000104)  # computer, not gamepad
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    with pytest.raises(BackendError, match="keeps reverting"):
        config.enforce_device_class(attempts=3)
    assert len(writes) == 3


def test_enforce_device_class_accepts_a_matching_read_back(monkeypatch):
    from anyctrl.backends.bluez import PRO_CONTROLLER_CLASS, _AdapterConfig

    config = _AdapterConfig("hci0")
    monkeypatch.setattr(config, "_write_device_class", lambda: None)
    monkeypatch.setattr(config, "read_device_class", lambda: PRO_CONTROLLER_CLASS)
    assert config.enforce_device_class() == PRO_CONTROLLER_CLASS


def test_enforce_device_class_tolerates_an_unreadable_class(monkeypatch):
    from anyctrl.backends.bluez import _AdapterConfig

    config = _AdapterConfig("hci0")
    monkeypatch.setattr(config, "_write_device_class", lambda: None)
    monkeypatch.setattr(config, "read_device_class", lambda: None)
    assert config.enforce_device_class() is None
