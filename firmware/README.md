# The USB bridge board

The `serial` backend needs a small micro-controller that plugs into the
console's USB port and pretends to be a wired controller. any-ctrl sends it
controller states over a serial link; the board turns them into USB HID
reports.

This is the only way to drive a console from **macOS**, which cannot present
itself as a Bluetooth HID *device*. It also works on Linux, and it is the more
reliable option on a Switch 2, whose Bluetooth stack is fussier than its
predecessor's.

## Why a separate serial adapter?

The board has one USB port and it is already busy being a controller, so the
host computer talks to the board over its **hardware UART** instead. You need
a USB-to-serial adapter (FTDI, CP2102, CH340 — any of them) between your
computer and the board.

```
   Mac / Linux box            USB-serial adapter            bridge board          console
  ┌───────────────┐          ┌─────────────────┐          ┌─────────────┐       ┌─────────┐
  │  anyctrl run  │──USB────▶│ TX ───────────▶ │ RX (D0)  │   ATmega    │──USB─▶│  dock   │
  │               │◀─────────│ RX ◀─────────── │ TX (D1)  │    32u4     │       │ or hub  │
  └───────────────┘          │ GND ────────────│ GND      └─────────────┘       └─────────┘
                             └─────────────────┘
```

Wiring, three connections only:

| adapter | board (Pro Micro / Leonardo) |
| ------- | ---------------------------- |
| TX      | RX, digital pin 0            |
| RX      | TX, digital pin 1            |
| GND     | GND                          |

Do **not** connect the adapter's VCC when the console is powering the board.
Use a 5 V adapter with a 5 V board and a 3.3 V adapter with a 3.3 V board.

## Flashing

1. Open `anyctrl_bridge/anyctrl_bridge.ino` in the Arduino IDE (or use
   `arduino-cli`).
2. Select your board: Pro Micro, Leonardo or Micro (any ATmega32u4).
3. **Override the USB VID/PID before uploading** (see below).
4. Upload while the board is connected to your computer's USB port. Afterwards
   move that cable to the console and use the serial adapter for the host link.

### The VID/PID override matters

A Switch only accepts wired controllers it recognises. Out of the box an
Arduino enumerates with Arduino's own USB IDs and the console ignores it. Give
the board the IDs of a licensed wired pad — `0x0F0D` / `0x0092` (HORI) is the
usual choice:

```bash
arduino-cli compile \
  --fqbn arduino:avr:leonardo \
  --build-property "build.vid=0x0F0D" \
  --build-property "build.pid=0x0092" \
  --build-property "build.usb_manufacturer=\"HORI CO.,LTD.\"" \
  --build-property "build.usb_product=\"POKKEN CONTROLLER\"" \
  firmware/anyctrl_bridge
```

In the Arduino IDE the same thing is done by adding a custom board entry to
`boards.txt`. If the console shows "the controller cannot be used", the IDs are
almost always the reason.

## Checking it works

With the board plugged into your computer (not the console) and the serial
adapter wired up:

```bash
anyctrl ports          # find the adapter
anyctrl press A --backend serial --port /dev/tty.usbserial-XXXX
```

`anyctrl` performs a HELLO/BANNER exchange on connect, so a wiring or baud-rate
mistake is reported immediately rather than looking like a dead macro.

## Safety behaviour

The firmware neutralises every input if the host stops sending frames for one
second, so a crashed or killed `anyctrl` cannot leave a button held down. It
also repeats the current report every 10 ms, which is what the console expects
from a wired pad.

## Other boards

Any board that speaks the framing in `anyctrl_bridge.ino` works. The protocol
is deliberately trivial:

```
0xA5 | type | length | payload[length] | checksum      checksum = XOR of type, length, payload
```

Host to device: `0x01` HELLO, `0x02` STATE (8 byte report), `0x03` RESET,
`0x04` PING. Device to host: `0x81` BANNER, `0x82` ACK, `0x84` PONG, `0x8F`
LOG. The banner must start with `anyctrl-bridge` (pass `--no-banner-check` by
constructing the backend with `require_banner=False` if you are experimenting).

The 8-byte STATE payload is a standard wired-pad report:

| byte | meaning                                                    |
| ---- | ---------------------------------------------------------- |
| 0-1  | buttons, little endian: Y B A X L R ZL ZR − + L3 R3 Home Capture |
| 2    | hat: 0 = up, 2 = right, 4 = down, 6 = left, 8 = neutral     |
| 3-4  | left stick X, Y (0-255, 128 = centre, Y grows downwards)    |
| 5-6  | right stick X, Y                                            |
| 7    | vendor byte, always 0                                       |

On an RP2040 (Arduino-Pico core) the same sketch works with the Adafruit
TinyUSB HID stack in place of `HID.h`, using `Serial1` on GP0/GP1.
