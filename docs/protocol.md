# How the controller emulation works

Notes for anyone reading or extending the code. The reference for the Bluetooth
side is the community's Nintendo Switch reverse engineering documentation; this
page describes what any-ctrl implements, not the protocol in full.

## Bluetooth

A Pro Controller is a Bluetooth HID *device*. The console is the host and opens
two L2CAP channels:

| PSM | channel   | traffic                                        |
| --- | --------- | ---------------------------------------------- |
| 17  | control   | HID handshakes, `SET_PROTOCOL`                 |
| 19  | interrupt | input reports (device → host) and output reports (host → device) |

Every packet on the interrupt channel carries a HID transaction header:
`0xA1` for our input reports, `0xA2` for the console's output reports.

### Input reports (we send)

| ID     | contents                                                     |
| ------ | ------------------------------------------------------------ |
| `0x3F` | simple HID report: 16 buttons, a hat, four 16 bit axes        |
| `0x21` | standard report plus an acknowledged subcommand reply         |
| `0x30` | standard report plus 36 bytes of IMU data, sent at ~60 Hz     |

A standard report is: report ID, a timer byte, a battery/connection byte, three
button bytes, three bytes per stick (two 12 bit values packed into three
bytes), and a vibration byte. `anyctrl.controller.state` owns those encodings;
`anyctrl.controller.protocol` assembles the reports.

We start in `0x3F` and switch to `0x30` when the console asks for it with
subcommand `0x03`.

### Output reports (the console sends)

`0x01` carries rumble data plus a subcommand; `0x10` carries rumble alone. The
subcommands any-ctrl answers:

| subcommand | meaning                  | reply                                  |
| ---------- | ------------------------ | -------------------------------------- |
| `0x01`     | manual pairing           | ack `0x81`, phase echoed               |
| `0x02`     | request device info      | ack `0x82`, firmware, type, MAC        |
| `0x03`     | set input report mode    | ack `0x80`                             |
| `0x04`     | trigger elapsed time     | ack `0x83`                             |
| `0x10`     | SPI flash read           | ack `0x90`, address, length, data      |
| `0x11`     | SPI flash write          | ack `0x80`                             |
| `0x21`     | NFC/IR configuration     | ack `0xA0`                             |
| `0x30`     | set player lights        | ack `0x80`; this is our "ready" signal |
| `0x40`     | enable IMU               | ack `0x80`                             |
| `0x48`     | enable vibration         | ack `0x80`                             |

Anything else gets a plain `0x80` acknowledgement: refusing an unknown
subcommand makes some console firmware retry it forever, which stalls the
handshake.

### The emulated SPI flash

The console reads factory calibration out of the controller's flash before it
trusts its input. `anyctrl.controller.spi` serves a small in-memory image:
stick calibration at `0x603D`, IMU calibration at `0x6020`, colours at `0x6050`,
and erased `0xFF` bytes everywhere else — notably at `0x8010`, where an unwritten
user calibration means "use the factory values".

The stick calibration deliberately matches `STICK_RANGE` in
`anyctrl.controller.state`. If the two disagree, full deflection in a macro
reaches only part of the way in game, which is a maddening bug to chase.

### Identity

Three things have to line up before a console will pair:

1. the device class, `0x002508` (peripheral / gamepad),
2. the adapter alias, "Pro Controller",
3. an SDP record advertising HID with both PSMs and a report descriptor.

`anyctrl.backends.bluez` sets all three, and restores the first two on exit.
The report descriptor it publishes declares exactly the reports listed above
with the sizes real hardware uses; it is not a byte-for-byte copy of retail
firmware, and `BluezBackend(report_descriptor=...)` accepts a dump of your own
controller if you ever need an exact match.

## USB bridge

The bridge board speaks a much smaller protocol, because the board — not the
host — is the USB device:

```
0xA5 | type | length | payload[length] | checksum        checksum = XOR of type, length, payload
```

The host sends `0x02` STATE frames carrying the same 8 byte report a licensed
wired pad sends: 16 buttons, a hat, four 8 bit axes and a vendor byte. The
firmware forwards them to the console over USB HID and neutralises everything
if the host goes quiet for a second.

Framing details and the board wiring live in
[`firmware/README.md`](../firmware/README.md).

## Timing

Both backends report at a fixed 15 ms tick. On Bluetooth that cadence is
mandatory — the console expects a controller to keep talking whether or not
anything changed — so the backend runs its own sender thread and the macro
player merely publishes state to it. Over USB the firmware does the repeating,
so the host only sends on change plus a keepalive.
