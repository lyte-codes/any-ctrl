"""The Pro Controller report protocol and its emulated SPI flash."""

from __future__ import annotations

from anyctrl.controller.buttons import Button
from anyctrl.controller.protocol import (
    SIMPLE_REPORT_LENGTH,
    STANDARD_REPORT_LENGTH,
    InputReportMode,
    ProControllerProtocol,
    parse_mac,
)
from anyctrl.controller.spi import ADDR_FACTORY_STICK_CAL, SpiFlash
from anyctrl.controller.state import STICK_RANGE, ControllerState

ADDRESS = "11:22:33:44:55:66"


def output_report(subcommand: int, args: bytes = b"", counter: int = 0) -> bytes:
    """Build the output report a console sends to issue ``subcommand``."""
    return bytes([0x01, counter, *([0x00] * 8), subcommand, *args])


def test_parse_mac_round_trip():
    assert parse_mac(ADDRESS) == bytes((0x11, 0x22, 0x33, 0x44, 0x55, 0x66))


def test_starts_in_simple_hid_mode():
    protocol = ProControllerProtocol(ADDRESS)
    assert protocol.report_mode is InputReportMode.SIMPLE_HID
    assert not protocol.ready
    assert len(protocol.input_report()) == SIMPLE_REPORT_LENGTH


def test_full_handshake_makes_the_controller_ready():
    protocol = ProControllerProtocol(ADDRESS)
    steps = [
        (0x02, b""),  # request device info
        (0x08, b"\x01"),  # shipment state
        (0x10, bytes([0x00, 0x60, 0x00, 0x00, 0x10])),  # read serial
        (0x03, b"\x30"),  # standard full report mode
        (0x40, b"\x01"),  # enable IMU
        (0x48, b"\x01"),  # enable vibration
        (0x30, b"\x01"),  # player 1
    ]
    for subcommand, args in steps:
        replies = protocol.handle_output_report(output_report(subcommand, args))
        assert replies, f"subcommand 0x{subcommand:02x} was not answered"
        assert replies[0][0] == 0x21
        assert replies[0][14] == subcommand
        assert len(replies[0]) == STANDARD_REPORT_LENGTH

    assert protocol.report_mode is InputReportMode.STANDARD_FULL
    assert protocol.player_number == 1
    assert protocol.imu_enabled and protocol.vibration_enabled
    assert protocol.ready
    assert len(protocol.input_report()) == STANDARD_REPORT_LENGTH


def test_device_info_reports_the_address_in_reverse():
    protocol = ProControllerProtocol(ADDRESS)
    reply = protocol.handle_output_report(output_report(0x02))[0]
    ack, subcommand = reply[13], reply[14]
    assert (ack, subcommand) == (0x82, 0x02)
    assert reply[19:25] == bytes((0x66, 0x55, 0x44, 0x33, 0x22, 0x11))


def test_spi_read_returns_stick_calibration_matching_our_range():
    protocol = ProControllerProtocol(ADDRESS)
    address = ADDR_FACTORY_STICK_CAL.to_bytes(4, "little")
    reply = protocol.handle_output_report(output_report(0x10, address + bytes([18])))[0]
    assert reply[13] == 0x90
    assert reply[15:19] == address
    assert reply[19] == 18
    data = reply[20:38]
    above_x = data[0] | ((data[1] & 0x0F) << 8)
    assert above_x == STICK_RANGE


def test_spi_read_is_clamped_to_the_protocol_maximum():
    protocol = ProControllerProtocol(ADDRESS)
    reply = protocol.handle_output_report(
        output_report(0x10, (0x6000).to_bytes(4, "little") + bytes([0xFF]))
    )[0]
    assert reply[19] == 0x1D


def test_unknown_subcommands_are_acknowledged():
    protocol = ProControllerProtocol(ADDRESS)
    reply = protocol.handle_output_report(output_report(0x7F))[0]
    assert reply[13] == 0x80
    assert reply[14] == 0x7F


def test_rumble_only_reports_are_recorded_without_a_reply():
    seen: list[bytes] = []
    protocol = ProControllerProtocol(ADDRESS, on_rumble=seen.append)
    payload = bytes([0x10, 0x00, *range(8)])
    assert protocol.handle_output_report(payload) == []
    assert seen and len(seen[0]) == 8
    assert protocol.last_rumble == bytes(range(8))


def test_button_state_appears_in_the_input_report():
    protocol = ProControllerProtocol(ADDRESS)
    protocol.handle_output_report(output_report(0x03, b"\x30"))
    protocol.set_state(ControllerState(buttons={Button.A, Button.ZR}))
    report = protocol.input_report()
    assert report[0] == 0x30
    assert report[3] == 0x08 | 0x80  # A and ZR live in the same byte


def test_timer_advances_between_reports():
    protocol = ProControllerProtocol(ADDRESS)
    first, second = protocol.input_report(), protocol.input_report()
    assert first[0] == second[0] == int(InputReportMode.SIMPLE_HID)
    # The simple report has no timer, so check the standard one instead.
    protocol.handle_output_report(output_report(0x03, b"\x30"))
    assert protocol.input_report()[1] != protocol.input_report()[1]


def test_empty_and_unknown_report_ids_are_ignored():
    protocol = ProControllerProtocol(ADDRESS)
    assert protocol.handle_output_report(b"") == []
    assert protocol.handle_output_report(b"\x80\x01") == []


def test_spi_flash_defaults_to_erased_bytes():
    flash = SpiFlash()
    assert flash.read(0x8010, 4) == b"\xff\xff\xff\xff"  # no user calibration
    assert flash.read(0x6050, 3) == b"\x32\x32\x32"  # body colour
    assert flash.read(0x1FFFF, 4) == b"\xff" * 4  # past the end of the image
