# Driving a console from a Raspberry Pi

A Pi with built-in Bluetooth is the least fiddly way to run any-ctrl: no bridge
board, no serial adapter, no wiring. The Pi *is* the controller, and you drive
it from wherever you like over SSH.

This is also the answer for Mac owners. macOS cannot present itself as a
Bluetooth HID device or as a USB device, so a Mac alone cannot drive a console
at all — but a Pi on the same network can, and you can write, check and launch
macros from the Mac.

## What works

| model                  | Bluetooth | notes                              |
| ---------------------- | --------- | ---------------------------------- |
| Pi Zero 2 W            | built in  | cheapest option that just works    |
| Pi 3 / 4 / 5           | built in  | plenty of headroom                 |
| Pi Zero (original) / 1 | none      | needs a USB Bluetooth dongle       |

Any 64-bit Raspberry Pi OS release is fine. Other Debian-based distributions
work the same way.

## Setup

On the Pi:

```bash
git clone https://github.com/lyte-codes/any-ctrl
cd any-ctrl
./scripts/setup-pi.sh
```

The script installs the system packages, builds a virtualenv, installs
any-ctrl, and offers to disable `bluetoothd`'s input plugin. Then:

```bash
sudo .venv/bin/anyctrl doctor
```

Every line should read `ok`.

### Doing it by hand

```bash
sudo apt install python3-venv python3-dbus bluez bluez-tools
python3 -m venv --system-site-packages .venv     # so python3-dbus is visible
.venv/bin/pip install .
```

Two details that catch people out:

- **`python3-dbus` from apt, not pip.** The pip build needs D-Bus headers and a
  compiler; on a Pi Zero that is a slow detour with a good chance of failing.
  The `--system-site-packages` flag is what lets the virtualenv see it.
- **`bluetoothd` must not load its input plugin**, or it will hold L2CAP ports
  17 and 19 before any-ctrl can. Find the current command with
  `systemctl cat bluetooth | grep ExecStart`, then add ` -P input` to it via
  `sudo systemctl edit bluetooth`. Real Bluetooth input devices stop working on
  the Pi until you undo it, so use a wired keyboard or SSH.

## Running a macro

```bash
sudo .venv/bin/anyctrl run macros/connection-test.macro
```

`sudo` is needed because binding those L2CAP ports is privileged. Call the
virtualenv's `anyctrl` by its full path — `sudo` resets `PATH`, so a bare
`sudo anyctrl` usually ends in "command not found".

On the console open **System Settings → Controllers → Change Grip/Order** and
stay there. Once any-ctrl reports a player number, the macro starts. Afterwards
the console remembers the Pi, so you can reconnect from the HOME screen:

```bash
sudo .venv/bin/anyctrl run macro.macro --address AA:BB:CC:DD:EE:FF
```

Add `--console switch2` for a Switch 2.

## Driving it from a Mac

Write and check macros locally — that half needs no hardware:

```bash
anyctrl check macros/hello.macro --timeline
```

Then copy and run:

```bash
scp macros/mine.macro pi@raspberrypi.local:any-ctrl/macros/
ssh pi@raspberrypi.local 'cd any-ctrl && sudo .venv/bin/anyctrl run macros/mine.macro'
```

For a long or endless macro, start it under `tmux` so it survives the SSH
session ending:

```bash
ssh pi@raspberrypi.local
tmux new -s ctrl
cd any-ctrl && sudo .venv/bin/anyctrl run macros/anti-idle.macro
# detach with Ctrl-B then D; reattach later with: tmux attach -t ctrl
```

Stopping it with Ctrl-C releases every button before disconnecting.

## Troubleshooting

Everything in [troubleshooting.md](troubleshooting.md) applies. Two things are
particular to the Pi:

- **The onboard Bluetooth shares hardware with the serial console** on some
  models. If Bluetooth behaves erratically, check that `enable_uart` and any
  `dtoverlay=disable-bt` line in `/boot/firmware/config.txt` are not fighting
  you.
- **Wi-Fi and Bluetooth share an antenna** on the Zero 2 W. Heavy Wi-Fi traffic
  during a macro can cause dropped reports; if a long run disconnects, try the
  Pi closer to the console, or on Ethernet where that is an option.
