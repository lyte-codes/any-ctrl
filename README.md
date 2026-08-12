# any-ctrl

Play controller macros on a Nintendo Switch — first or second generation — from
Linux or macOS.

any-ctrl presents itself to the console as a Pro Controller, either over
Bluetooth using your computer's own adapter (Linux), or through a small USB
bridge board (Linux and macOS). You write what you want pressed in a readable
macro file; any-ctrl plays it with frame-accurate timing.

```
$ anyctrl run macros/connection-test.macro --console switch2
macro: Connection test (5.8s, 61 ops)
backend: bluez, console: switch2
on the console open System Settings > Controllers > Change Grip/Order and leave it open
connected. playing (Ctrl-C to stop)
  [   0.00s] d-pad: right, right, left, left
  [   2.10s] left stick: a full circle
  [   3.60s] right stick: a full circle
  [   5.10s] shoulder buttons
done after 5.8s of playback
```

## What a macro looks like

```
meta name "Shop reset"

def buy {
    press A 120ms
    wait 400ms
}

loop 5 {
    call buy
    stick L up for 1.2s
    press ZL+ZR
}
```

Blocks, loops (including `loop forever`), both sticks by direction, vector or
compass bearing, the d-pad, includes, and per-macro timing defaults. The full
reference is in [`docs/macros.md`](docs/macros.md).

## Install

```bash
git clone https://github.com/lyte-codes/any-ctrl
cd any-ctrl

# Linux, Bluetooth:
pip install '.[bluez]'

# macOS or Linux, USB bridge board:
pip install '.[serial]'
```

Python 3.10 or newer. No dependencies beyond the extra you choose.

## Getting connected

```bash
anyctrl doctor      # what works on this machine, and what to fix
anyctrl backends    # which backends are available
```

### Linux, over Bluetooth

Your adapter becomes the controller. It needs root (the HID L2CAP ports are
privileged) and a `bluetoothd` started with its input plugin disabled, which
would otherwise claim those ports:

```bash
sudo systemctl edit bluetooth     # ExecStart=/usr/lib/bluetooth/bluetoothd -P input
sudo systemctl restart bluetooth

sudo -E anyctrl run macros/connection-test.macro
```

On the console, open **System Settings → Controllers → Change Grip/Order** and
stay there until any-ctrl says it is connected. Afterwards you can reconnect
without the grip screen using `--address <console MAC>`.

Full setup, including the `bluetoothd` paths per distribution, is in
[`docs/backends.md`](docs/backends.md).

### macOS, over a USB bridge

macOS cannot present itself as a Bluetooth HID device — no application can, the
capability is not exposed — so a Mac drives the console through a
micro-controller that plugs into the console's USB port and enumerates as a
wired controller. A Pro Micro, a Leonardo or an RP2040 plus a USB-to-serial
adapter is all it takes; the firmware and wiring are in
[`firmware/README.md`](firmware/README.md).

```bash
anyctrl ports
anyctrl run macros/hello.macro --backend serial --port /dev/tty.usbserial-XXXX
```

The same path works on Linux, and is the more robust option on a Switch 2.

### Neither, yet

Every macro can be checked, previewed and timed without a console:

```bash
anyctrl check macros/hello.macro --timeline
```

```
macro: Hello
instructions: 13
duration: 1.50s

timeline (7 state changes):
    0.000s  [A] L(+0.00,+0.00) R(+0.00,+0.00)
    0.100s  [-] L(+0.00,+0.00) R(+0.00,+0.00)
    0.500s  [A] L(+0.00,+0.00) R(+0.00,+0.00)
    ...
```

## Commands

| command                       | what it does                                        |
| ----------------------------- | --------------------------------------------------- |
| `anyctrl run <macro>`         | play a macro                                        |
| `anyctrl check <macro>`       | parse it, time it, print its input timeline         |
| `anyctrl press <combo>`       | press one combination, e.g. `anyctrl press ZL+ZR`   |
| `anyctrl backends`            | list backends and whether they work here            |
| `anyctrl ports`               | list serial ports that could host a bridge          |
| `anyctrl buttons`             | list the button names macros accept                 |
| `anyctrl doctor`              | diagnose the setup and say what to fix              |

Useful flags for `run`: `--console switch1|switch2`, `--repeat N`,
`--speed 2`, `--timeout 30m`, `--show-inputs`, `--backend dryrun`.

## Switch 1 and Switch 2

Both work, through the same code: a Switch 2 accepts an original Pro
Controller. `--console switch2` adjusts the handshake and settle timings that
actually differ, which matters — the newer console keeps configuring a
controller for a couple of seconds after the connection looks finished, and
input sent during that window is discarded. See
[`docs/switch2.md`](docs/switch2.md).

## How it works

- [`anyctrl/controller`](src/anyctrl/controller) — the Pro Controller model:
  buttons, sticks, HID reports, subcommand handling, an emulated SPI flash
  serving the factory calibration the console reads before it trusts our input.
- [`anyctrl/macro`](src/anyctrl/macro) — the macro language: parser, compiler,
  and a player that keeps a virtual clock so timing never drifts across a long
  run.
- [`anyctrl/backends`](src/anyctrl/backends) — BlueZ, the USB bridge, and a
  dry-run backend that records instead of sending.

[`docs/protocol.md`](docs/protocol.md) documents the wire protocol as
implemented.

Anything held down is released when a macro ends, when you press Ctrl-C, and
when a backend fails mid-run. The bridge firmware independently neutralises its
report if the host stops talking to it, so a crash cannot leave a button stuck.

## Development

```bash
pip install '.[dev]'
pytest        # 137 tests, no hardware required
ruff check .
```

The protocol, the language and the timing are all covered by tests that run
without a console: the Bluetooth handshake is driven end to end over a socket
pair, the serial backend against a fake port, playback against a fake clock.
What tests cannot cover is the hardware itself — pairing with a real console,
and the bridge firmware on a real board — so treat those paths as needing a
shakedown run on your own setup. `anyctrl doctor` and
[`docs/troubleshooting.md`](docs/troubleshooting.md) are written for exactly
that.

## Scope and conduct

This is a tool for automating your own console: grinding menus you have already
played through a hundred times, testing your own homebrew, running accessibility
input schemes, leaving a download to finish without the console sleeping.

It emulates a standard controller, so it cannot do anything you could not do
with your thumbs — it does not modify the console, install anything on it, or
touch save data. Bear in mind that automating online play is against Nintendo's
terms of service and can get an account banned; that is your call and your risk.

## Licence

MIT — see [LICENSE](LICENSE).

Not affiliated with or endorsed by Nintendo. Nintendo Switch is a trademark of
Nintendo.
