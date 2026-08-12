"""A tiny emulated SPI flash for the Pro Controller.

During the handshake the console reads factory calibration, colours and user
calibration out of the controller's flash. Returning plausible, self consistent
values here is what makes the emulated controller feel like real hardware: the
stick calibration in particular has to agree with
:data:`anyctrl.controller.state.STICK_RANGE`, otherwise full stick deflection in
a macro reaches only part of the way in game.
"""

from __future__ import annotations

import struct

from anyctrl.controller.state import STICK_CENTER, STICK_RANGE

#: Emulated flash size. Real controllers expose 512 KiB but every address the
#: console reads during a normal handshake lives in the first 128 KiB.
FLASH_SIZE = 0x20000

# Well known addresses.
ADDR_SERIAL = 0x6000
ADDR_IMU_CALIBRATION = 0x6020
ADDR_STICK_DEVICE_PARAMS = 0x6086
ADDR_FACTORY_STICK_CAL = 0x603D
ADDR_COLORS = 0x6050
ADDR_IMU_HORIZONTAL_OFFSETS = 0x6080
ADDR_USER_STICK_CAL = 0x8010


def _pack_12bit(values: list[int]) -> bytes:
    """Pack six 12 bit values into nine bytes, the way the flash stores them."""
    if len(values) != 6:
        raise ValueError("expected exactly six 12 bit values")
    out = bytearray()
    for i in range(0, 6, 2):
        first, second = values[i] & 0xFFF, values[i + 1] & 0xFFF
        out.append(first & 0xFF)
        out.append(((first >> 8) & 0x0F) | ((second & 0x0F) << 4))
        out.append(second >> 4)
    return bytes(out)


class SpiFlash:
    """An in-memory flash image, addressable by the console's read subcommand."""

    def __init__(
        self,
        *,
        body_color: tuple[int, int, int] = (0x32, 0x32, 0x32),
        button_color: tuple[int, int, int] = (0xFF, 0xFF, 0xFF),
        left_grip_color: tuple[int, int, int] = (0x46, 0x46, 0x46),
        right_grip_color: tuple[int, int, int] = (0x46, 0x46, 0x46),
    ) -> None:
        # Erased flash reads as 0xFF; several regions are *expected* to be 0xFF
        # (an unwritten user calibration means "use the factory values").
        self._data = bytearray(b"\xff" * FLASH_SIZE)
        self._write_factory_stick_calibration()
        self._write_imu_calibration()
        self._write_colors(body_color, button_color, left_grip_color, right_grip_color)
        self._write_stick_device_parameters()

    # -- construction ------------------------------------------------------
    def _write(self, address: int, payload: bytes) -> None:
        self._data[address : address + len(payload)] = payload

    def _write_factory_stick_calibration(self) -> None:
        """Factory stick calibration at 0x603D (9 bytes left, 9 bytes right)."""
        above = STICK_RANGE
        below = STICK_RANGE
        left = _pack_12bit(
            [above, above, STICK_CENTER, STICK_CENTER, below, below],
        )
        # The right stick stores centre first, then below-centre, then above.
        right = _pack_12bit(
            [STICK_CENTER, STICK_CENTER, below, below, above, above],
        )
        self._write(ADDR_FACTORY_STICK_CAL, left + right)

    def _write_imu_calibration(self) -> None:
        """Accelerometer/gyroscope origins and sensitivities at 0x6020."""
        accelerometer_origin = (0, 0, 0)
        accelerometer_sensitivity = (16384, 16384, 16384)
        gyroscope_origin = (0, 0, 0)
        gyroscope_sensitivity = (13371, 13371, 13371)
        values = (
            *accelerometer_origin,
            *accelerometer_sensitivity,
            *gyroscope_origin,
            *gyroscope_sensitivity,
        )
        self._write(ADDR_IMU_CALIBRATION, struct.pack("<12h", *values))
        # Horizontal offsets used when the controller rests on a table.
        self._write(ADDR_IMU_HORIZONTAL_OFFSETS, struct.pack("<3h", 0, 0, 0))

    def _write_colors(
        self,
        body: tuple[int, int, int],
        buttons: tuple[int, int, int],
        left_grip: tuple[int, int, int],
        right_grip: tuple[int, int, int],
    ) -> None:
        self._write(ADDR_COLORS, bytes((*body, *buttons, *left_grip, *right_grip)))

    def _write_stick_device_parameters(self) -> None:
        """Dead zone and range ratio the console applies to our stick values."""
        dead_zone = 160
        range_ratio = 0xDAC
        # The blob below is the stick device parameter set a retail Pro
        # Controller reports; the console reads it verbatim.
        params = bytes.fromhex("0f30619630f3d41454411554c7799c333663")
        self._write(ADDR_STICK_DEVICE_PARAMS, params)
        self._write(0x6098, _pack_12bit([dead_zone, range_ratio, 0, 0, 0, 0])[:6])

    # -- access ------------------------------------------------------------
    def read(self, address: int, length: int) -> bytes:
        """Read ``length`` bytes; reads past the end return erased flash."""
        if address < 0 or length < 0:
            raise ValueError("negative SPI flash read")
        chunk = bytes(self._data[address : address + length])
        if len(chunk) < length:
            chunk += b"\xff" * (length - len(chunk))
        return chunk

    def write(self, address: int, payload: bytes) -> None:
        """Accept a write from the console (the pairing data lands here)."""
        if address < 0 or address + len(payload) > FLASH_SIZE:
            raise ValueError("SPI flash write out of range")
        self._write(address, payload)
