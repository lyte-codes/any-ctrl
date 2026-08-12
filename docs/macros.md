# The macro language

A macro is a plain text file, one statement per line. Blank lines are ignored;
`#` and `//` start a comment. Keywords and button names are case insensitive.

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

Check a macro without a console attached:

```bash
anyctrl check macros/hello.macro --timeline
```

## Durations

Anywhere a duration is accepted you can write `250ms`, `1.5s`, `2m`, or a bare
number, which means milliseconds. `press A 250` and `press A 250ms` are the
same statement.

## Statements

### `press <combo> [<duration>] [x<n>] [every <duration>]`

Press a button (or a combination), hold it, release it, then pause before the
next statement. Without a duration it holds for `press_time` (80 ms by default)
and pauses for `gap` (80 ms); `every` overrides that pause.

```
press A                       # a normal tap
press A 500ms                 # a long press
press ZL+ZR                   # both shoulders together
press B x10 every 250ms       # ten taps, four per second
```

`tap` and `click` are aliases.

### `hold <combo>` and `release <combo> | release all`

Press without releasing, and release later. Anything still held when the macro
ends — or when you press Ctrl-C — is released automatically.

```
hold ZL
wait 2s
release ZL
```

`release all` also recentres both sticks.

### `wait <duration>`

Hold everything exactly as it is. `sleep` and `delay` are aliases.

### `stick <L|R> <position> [for <duration>]`

Move an analogue stick. Positions can be written three ways:

```
stick L up                    # a named direction, full deflection
stick L up 0.4                # the same direction, 40% deflection
stick R 0.5 -1.0              # explicit x y, each in [-1, 1], +y is up
stick L angle 45 0.8          # compass bearing: 0 = up, 90 = right
stick L center                # back to neutral
```

With `for <duration>` the stick returns to centre afterwards; without it, the
stick stays where you put it until something else moves it.

Named directions: `up`, `down`, `left`, `right`, `up_left`, `up_right`,
`down_left`, `down_right`, `center`.

### `dpad <direction> [<duration>]`

```
dpad up
dpad down_left 200ms
dpad none
```

The d-pad's four directions are also ordinary buttons (`UP`, `DOWN`, `LEFT`,
`RIGHT`), so `press UP` works too. `dpad` is the clearer way to express
diagonals and to return to neutral.

### `loop <n> { ... }` and `loop forever { ... }`

```
loop 3 {
    press A
}

loop forever {
    press A
    wait 5s
}
```

Loops are not unrolled, so `loop 100000 { ... }` costs nothing extra, and
`forever` runs until you stop it with Ctrl-C or `--timeout`. `repeat` is an
alias. Braces may sit on their own lines or share a line: `loop 3 { press A }`.

### `def <name> { ... }` and `call <name>`

Name a block of statements once and use it repeatedly. Definitions live at the
top level (they cannot be nested), may be called from anywhere including
inside loops, and may call other blocks. Recursion is rejected at compile time.

### `include "<path>"`

Paste in another macro file, resolved relative to the file doing the
including. Included definitions and metadata are merged; a definition that
collides with an existing one is an error, and circular includes are rejected.

### `log "<message>"`

Print a message while the macro plays. `echo` is an alias.

### `set <option> <value>`

Change a default for the statements that follow:

| option       | meaning                                | default |
| ------------ | -------------------------------------- | ------- |
| `press_time` | how long `press` holds a button        | 80 ms   |
| `gap`        | the pause after each `press`           | 80 ms   |
| `tick`       | interval between controller reports    | 15 ms   |

`set` is applied at compile time, so it affects every statement written after
it, including statements inside a loop that runs later.

### `meta <key> <value>`

Free-form metadata. `name` is shown while the macro plays; `tick` is honoured
by the player; everything else is documentation. The first value wins, so
metadata in an included file never overrides the file that included it.

## Buttons

```
A B X Y L R ZL ZR PLUS MINUS HOME CAPTURE LSTICK RSTICK UP DOWN LEFT RIGHT
```

Aliases: `+` `-` `start` `select` `l1` `r1` `l2` `r2` `l3` `r3` `lclick`
`rclick` `screenshot` `cap` `dup` `ddown` `dleft` `dright`. Combine with `+`:
`ZL+ZR`, `A+B`, `L+R+PLUS`. `anyctrl buttons` prints the list.

## Timing, and why it is exact

The player keeps a virtual clock. Every wait advances it by exactly what the
macro asked for, and real sleeps are computed from the start of playback, so
scheduling jitter never accumulates: a macro of ten thousand 20 ms presses ends
after exactly the time it should, not two seconds late.

Waits are quantised to the report tick (15 ms by default) for the purpose of
sending reports, but the *total* duration is preserved — the last slice of a
wait is shortened rather than rounded up.

`--speed 2` halves every duration; `--speed 0.5` doubles them. It is a debugging
aid: most games have their own timing, and a macro that runs faster than the
game reacts will drift out of step with it.
