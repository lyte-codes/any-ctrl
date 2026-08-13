# Troubleshooting

Start with `anyctrl doctor` for "can this machine work at all". When it says
yes and the console still does nothing, run the deep diagnosis:

```bash
sudo anyctrl diagnose             # every check, each failure with a hex code
sudo anyctrl diagnose --live      # also advertise for 60s and see what connects
sudo anyctrl diagnose --json      # machine readable
```

Each failure carries a stable code you can look up below. The exit status is 0
when everything passed and 1 otherwise, and the last line repeats the first
failure — checks run in dependency order, so that first code is the one to fix.

| code | meaning | code | meaning |
| ---- | ------- | ---- | ------- |
| `0x1001` | not Linux | `0x3001` | bluetoothd not running |
| `0x1002` | no L2CAP support in Python | `0x3002` | input plugin enabled |
| `0x1003` | not running as root | `0x3003` | PSM 17 already in use |
| `0x2001` | no Bluetooth adapter | `0x3004` | PSM 19 already in use |
| `0x2002` | adapter powered off | `0x3005` | PSM bind denied (not root) |
| `0x2003` | blocked by rfkill | `0x4001` | dbus-python missing |
| `0x2004` | adapter not answering | `0x4002` | bluetoothd not on the bus |
| `0x2005` | not discoverable | `0x4003` | SDP registration refused |
| `0x2006` | not pairable | `0x5001` | pyserial missing |
| `0x2007` | page scan off (not connectable) | `0x5002` | no serial ports |
| `0x2008` | no pairing agent available | `0x5003` | no bridge adapter found |
| `0x2101` | no hciconfig or btmgmt | | |
| `0x2102` | class of device write failed | | |
| `0x2103` | class of device reverted | `0x6001` | no console connected |
| `0x2104` | class of device unreadable | `0x6003` | console tried, link failed |
| | | `0x6002` | handshake never completed |

`anyctrl doctor` still covers the basics below.

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

any-ctrl says it is waiting, the grip screen shows nothing. Work down this
list; the first item accounts for most cases.

**Check the device class actually took.** While any-ctrl is waiting, from
another shell:

```bash
sudo hciconfig hci0 class      # want: Class: 0x002508
sudo btmgmt --index hci0 info  # or here, if hciconfig is not installed
```

The console decides what is a controller mostly from this value. any-ctrl sets
it after registering the HID profile — registering makes `bluetoothd` recompute
the class and overwrite it — and verifies the result, so a mismatch is now
reported rather than silently waited out. If it reads anything else, something
on the system is rewriting it: a desktop Bluetooth applet, or `bluetoothd`
restarting underneath you.

Run with `--verbose` to see the class any-ctrl read back after setting it.

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

## bluez: 0x6001 - we advertise correctly and nothing connects

Every check passes, the adapter holds the gamepad class, both channels listen,
and the console still ignores it. In order of likelihood:

1. **A stale pairing on the console.** It remembers a link key for this adapter
   that no longer matches, and silently declines. On the console:
   **Change Grip/Order → X, Disconnect**, then try again.
2. **The console is not actually scanning.** It only looks for new controllers
   while the grip screen is open — and it stops looking after a while. Open it
   fresh, then start the run.
3. **Page scan or the pairing agent.** any-ctrl now enables page scan
   (`piscan`) and registers a `NoInputNoOutput` agent for the duration of a
   run; without either, a console can find the adapter but never complete a
   connection. `--verbose` prints both states.

`--live` captures the radio itself when `btmon` is installed
(`sudo apt install bluez`), and reads the trace for you, which splits `0x6001`
into two very different diagnoses:

- **`0x6001`** — nothing on the radio even tried. The console never reached us,
  so no adapter tweak will help: look at the console's own state (stale
  pairing, not on the grip screen) and at distance.
- **`0x6003`** — a console *did* reach us and the link did not complete. The
  fault is on this side, and the trace names the step that failed.

Reading a trace by hand is thankless: a busy room fills it with LE
advertisements from phones, watches and hearing aids, none of which is a
console — the Switch speaks classic BR/EDR. The markers worth grepping for are
`Connect Request`, `IO Capability`, `Link Key Request` and
`Simple Pairing Complete`:

```bash
grep -nE "Connect Request|IO Capability|Link Key|Simple Pairing|Auth" /tmp/anyctrl-hci-*.log
```

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
