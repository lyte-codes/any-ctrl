# Switch 1 and Switch 2

Both generations are supported by the same code, because both accept an
original Pro Controller. Tell any-ctrl which one you are driving so it uses the
right timings:

```bash
anyctrl run macro.macro --console switch2
```

## What the flag changes

| setting        | `switch1` | `switch2` | why                                          |
| -------------- | --------- | --------- | -------------------------------------------- |
| report tick    | 15 ms     | 15 ms     | both expect roughly 60 reports a second       |
| settle delay   | 1.0 s     | 2.5 s     | the newer console keeps configuring the pad after the handshake looks finished |
| pair timeout   | 120 s     | 180 s     | binding takes longer, and the grip screen is slower to appear |

The wire protocol itself is unchanged: the same HID reports, the same
subcommands, the same SPI calibration reads.

## Practical differences

**Pairing is stricter.** The Switch 2 is less forgiving of an adapter that
looks only half like a controller. If the console refuses a Bluetooth
connection that a Switch 1 accepts, check that the device class was actually
applied (`hciconfig hci0 class` should read `0x002508`) and that the SDP record
registered without error — `anyctrl doctor` reports both.

**The settle delay is not optional.** Pressing buttons immediately after the
player LED is assigned frequently produces a run where nothing happens: the
console has bound the controller but is not yet routing its input to the game.
That is what the longer `settle` is for.

**The USB bridge is the more reliable path.** A wired controller is a much
smaller surface than a Bluetooth one, and it is unaffected by pairing changes
in console firmware updates. If Bluetooth gives you trouble on a Switch 2, the
bridge board in [`firmware/`](../firmware/README.md) is the answer.

**Switch 2 controllers are a different thing.** any-ctrl emulates a *Switch 1
Pro Controller*, which the Switch 2 accepts as a legacy controller. It does not
emulate the Switch 2's own controllers, so features exclusive to them — the
mouse mode of the new Joy-Cons, the C button — are out of scope.

## If a macro does nothing

Work through it in this order:

1. `anyctrl run macros/connection-test.macro --console switch2` on the HOME
   screen. If the cursor moves, the connection is fine and the problem is the
   macro's timing, not the link.
2. Add `--show-inputs` to confirm any-ctrl is sending what you think it is.
3. Increase the waits. Menus on a Switch 2 animate at different speeds than
   their Switch 1 equivalents, and a macro tuned on one console usually needs
   its waits stretched on the other.
