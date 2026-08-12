# Backends

A backend is the thing that actually reaches the console. Pick one with
`--backend`; the default, `auto`, uses the best one available on this machine.

```bash
anyctrl backends   # what exists and whether it works here
anyctrl doctor     # why it does not, and what to do about it
```

| backend  | where it runs        | how it connects                          |
| -------- | -------------------- | ---------------------------------------- |
| `bluez`  | Linux, root          | Bluetooth, emulating a Pro Controller    |
| `serial` | Linux, macOS         | USB bridge board over a serial link      |
| `dryrun` | anywhere             | nowhere; records what would be sent      |

## `bluez` — Bluetooth on Linux

The machine's own Bluetooth adapter pretends to be a Pro Controller. Nothing
else is needed: no extra hardware, and the console pairs with it the same way
it pairs with a real controller.

### Requirements

- Linux with BlueZ and a working Bluetooth adapter.
- `pip install 'any-ctrl[bluez]'` (adds `dbus-python`).
- `hciconfig` (bluez-utils) or `btmgmt` (bluez-tools), to set the device class.
- **root**: the HID PSMs (17 and 19) are privileged ports.
- `bluetoothd` running **without its input plugin**, which would otherwise
  claim those PSMs first.

Disabling the input plugin, on a systemd distribution:

```bash
sudo systemctl edit bluetooth
```

```ini
[Service]
ExecStart=
ExecStart=/usr/libexec/bluetooth/bluetoothd -P input
```

(The path to `bluetoothd` varies: `/usr/lib/bluetooth/bluetoothd` on Debian and
Ubuntu, `/usr/libexec/bluetooth/bluetoothd` on Fedora and Arch.) Then:

```bash
sudo systemctl daemon-reload && sudo systemctl restart bluetooth
```

While the plugin is off, real Bluetooth controllers and keyboards will not work
on that machine. Undo the override when you are done.

### First connection

```bash
sudo -E anyctrl run macros/connection-test.macro --backend bluez
```

On the console: **System Settings → Controllers → Change Grip/Order**, and stay
on that screen. any-ctrl makes the adapter discoverable, advertises the HID
service, waits for the console to connect, answers its setup subcommands and
then starts the macro once a player LED has been assigned.

### Reconnecting later

Once the console knows the adapter it can be woken from the HOME screen without
the grip screen:

```bash
sudo -E anyctrl run macro.macro --backend bluez --address AA:BB:CC:DD:EE:FF
```

The address is the console's, which you can find with `bluetoothctl devices`
after a successful first pairing.

### What it does to your adapter

While connected, the adapter's alias becomes "Pro Controller", its class
changes to a gamepad's, and it becomes discoverable. All of that is restored on
exit — including after Ctrl-C.

## `serial` — a USB bridge board

A micro-controller plugs into the console's USB port and enumerates as a wired
controller; any-ctrl talks to it over a serial link. This is the only option on
macOS, and a good option on Linux when you would rather not disturb the
Bluetooth stack.

```bash
pip install 'any-ctrl[serial]'
anyctrl ports                                     # find the adapter
anyctrl run macro.macro --backend serial --port /dev/tty.usbserial-A50285BI
```

Without `--port`, any-ctrl picks the first port whose USB IDs match a board it
recognises. On connect it exchanges a HELLO/BANNER greeting with the firmware,
so a wiring or baud mistake is reported straight away.

Building the board — wiring, flashing, and the USB VID/PID override the console
requires — is covered in [`firmware/README.md`](../firmware/README.md).

## `dryrun` — no console at all

Records every controller state instead of sending it. `anyctrl check --timeline`
uses it to print the exact input timeline a macro produces, and the test suite
uses it to exercise playback without hardware.

```bash
anyctrl run macro.macro --backend dryrun --show-inputs
```

## Choosing between them

Bluetooth is more convenient — no hardware, no cables — but it needs root and a
modified `bluetoothd`, and it is the fussier of the two on a Switch 2. The
bridge board needs about £10 of parts and thirty minutes of soldering-free
wiring, after which it works identically on every OS and console generation and
survives console firmware updates that tighten Bluetooth pairing.
