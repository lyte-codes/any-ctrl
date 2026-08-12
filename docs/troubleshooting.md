# Troubleshooting

Start with `anyctrl doctor`. It checks the things below and prints what is
missing.

## Nothing is available: "no usable backend found"

Neither Bluetooth nor a bridge board is ready. On Linux, install the extra and
run as root: `pip install 'any-ctrl[bluez]'` then `sudo -E anyctrl ...`. On
macOS, install `pip install 'any-ctrl[serial]'` and connect a bridge board —
macOS cannot act as a Bluetooth HID device, so there is no software-only path.

## bluez: "cannot listen on L2CAP PSM 17"

Something already owns the HID PSMs, almost always `bluetoothd`'s input plugin.
Restart it with the plugin disabled (see
[backends.md](backends.md#requirements)) and try again. `Permission denied`
here means you are not root.

## bluez: "cannot set the Bluetooth device class"

Install `bluez-utils` (for `hciconfig`) or `bluez-tools` (for `btmgmt`). Without
the gamepad device class the console will not offer to pair, because the
adapter does not look like a controller.

## bluez: "cannot register the HID SDP record"

`bluetoothd` is not running, or you are not root. If the error mentions
`AlreadyExists`, a previous run left a profile registered; restarting
`bluetooth.service` clears it.

## bluez: the console never finds the controller

- Are you on **Change Grip/Order**? The console only listens for new
  controllers on that screen.
- Is the adapter free? Another connected device — or a paired phone reconnecting
  in the background — can keep the adapter busy. `bluetoothctl disconnect` and
  `bluetoothctl power on` before trying.
- Some USB Bluetooth dongles do not support the required L2CAP server sockets.
  If a built-in adapter works and a dongle does not, that is why.
- If the console lists the controller but immediately drops it, delete the old
  pairing on the console (**Change Grip/Order → X, Disconnect**) and pair again:
  the console remembers a link key that no longer matches.

## bluez: the handshake never completes

The error lists the subcommands the console sent. If it stops after `0x02`
(device info), the console is not happy with our identity; check the device
class. If it never sends `0x30` (player lights), it is still deciding — a
slower machine or a busy adapter can miss reports. Try again with the console
closer to the adapter.

## serial: "no response from bridge"

The host talks to the board over its UART, not over the board's USB port. Check
the three wires (TX→RX, RX→TX, GND↔GND), that the baud rate is 115200, and that
the firmware is flashed. `anyctrl ports` lists what the OS can see.

## serial: the console ignores the board

The board is enumerating with its default Arduino USB IDs. Re-flash it with
the VID/PID override described in [`firmware/README.md`](../firmware/README.md);
the console only accepts USB IDs it recognises.

## The macro plays but the game does not react

Almost always timing. Games swallow input during animations and menu
transitions, and a macro tuned on one save file or console generation will not
match another. Raise the waits, raise `press_time`, and confirm the raw input
is arriving with `macros/connection-test.macro` before blaming the link.

## The macro drifts out of step after a long run

any-ctrl's own timing does not drift (see [macros.md](macros.md#timing-and-why-it-is-exact)),
but the *game's* timing does: loading times vary, and a one-frame difference
per iteration accumulates. Build a resynchronisation point into the loop — back
out to a known screen every N iterations rather than assuming the game stayed
in step.

## Ctrl-C left a button held down

It should not: playback releases everything on the way out, and the bridge
firmware neutralises its report if the host goes quiet for a second. If a
button really is stuck, disconnect the controller from the console (grip screen,
or unplug the board) and reconnect.
