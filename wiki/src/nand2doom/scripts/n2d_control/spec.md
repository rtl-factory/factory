# nand2doom local controls

Plays an already running nand2doom image in real time: held host keys reach
Doom as make on key down and break on key up, combined actions work, and
nothing stays held after the window or terminal loses focus or closes. VGA
remains the display (no video streaming). The operator must own the bench
before Connect (a running-state Ping proves neither wiring, game identity nor
ownership); only the assigned operator runs live trials. The
[play command](../n2d_play/spec.md) launches these controls after loading.
Framing, baud, deadlines and transports belong to the
[UART tools](../n2d_uart/spec.md) and [JTAG library](../../../common/scripts/jtag/spec.md). No
installation, network service, global keyboard hook, capture, framebuffer,
driver or RTL change.

## Command line

n2d_control.py (`src/nand2doom/scripts/n2d_control.py`), Python 3.10+
(native Windows with Tk 8.6 supported).

```text
python -B -S src/nand2doom/scripts/n2d_control.py --help
python -B -S src/nand2doom/scripts/n2d_control.py --plan
python -B -S src/nand2doom/scripts/n2d_control.py --smoke-test
python -B src/nand2doom/scripts/n2d_control.py --port COM7
python3 src/nand2doom/scripts/n2d_control.py --baud de2_115 --port PORT --connect
python3 src/nand2doom/scripts/n2d_control.py --transport jtag --cable 'USB-Blaster [1-1]' \
    --device '@1: EP4CE115 (0x020F70DD)' --terminal
```

| Option | Contract |
| --- | --- |
| `--transport uart` (default) or `jtag` | Selects the link. |
| `--port` / `--cable --device [--quartus-bin]` | Prepopulate the link field only; nothing opens until Connect. The JTAG device is the exact `quartus_stp` name (install-dependent; `n2d_play` passes the one it [resolved by IDCODE](../n2d_jtag_keyboard/spec.md#device-resolution)). |
| `--baud` | Image link rate or board name (`de2_115` = 3,000,000). |
| `--timeout` | 0.01..1 s per UART transaction, default 1. |
| `--connect` | Connect at start (otherwise a human Connect click). |
| `--plan`, `--dry-run`, `--help` | JSON bindings, transport and policy without importing Tk, serial or Quartus, creating output or opening handles. |
| `--smoke-test` | Opens a disconnected window for 300 ms with Connect disabled. |
| `--terminal` | [Terminal controls](#terminal-controls), also chosen when Python lacks Tk (other Tk failures exit 3); they connect at once, so link arguments are required. |

No default port, scan, reconnection, Halt, Resume, reset, memory operation or
capture. Missing pyserial or Quartus is an actionable error. Exit 2 arguments,
3 unavailable GUI; live errors stay visible in the window.

## Supported logical actions

The GUI binding policy; the firmware
[keyboard map](../../sw/doom/spec.md#doom-keyboard-map) maps every
make code; events use the [ten-bit Set-2 contract](../../rtl/io/mas.md#ps2-keyboard-receiver).
Break ORs 0x100, preserving extended bit 0x200.

| Action | Keyboard keys | Make | Break |
| --- | --- | ---: | ---: |
| Forward | Up, W | 0x275 | 0x375 |
| Backward | Down, S | 0x272 | 0x372 |
| Turn left | Left, A | 0x26b | 0x36b |
| Turn right | Right, D | 0x274 | 0x374 |
| Fire | Control left/right, F | 0x014 | 0x114 |
| Use | Space, E | 0x029 | 0x129 |
| Run | Shift left/right, R | 0x012 | 0x112 |
| Strafe modifier | Alt left/right, Q | 0x011 | 0x111 |
| Enter | Return, keypad Enter | 0x05a | 0x15a |
| Escape | Escape | 0x076 | 0x176 |
| Yes | Y | 0x035 | 0x135 |
| No | N | 0x031 | 0x131 |
| Map | Tab | 0x00d | 0x10d |
| Weapon 1..7 | 1..7 | 0x016, 0x01e, 0x026, 0x025, 0x02e, 0x036, 0x03d | make OR 0x100 |

WASD is forward/back/turn, not strafe (hold Strafe with a turn arrow). Keys
apply only in this application's focused window or terminal; the link field
never injects keys.

## Transports

| Link | Contract |
| --- | --- |
| UART | One `n2d_uart.Client` on one serial handle at `--baud`; each make or break is one CRC-checked `CMD_INPUT`. Connect requires a checked Ping with identity and halted=0. |
| JTAG | One persistent `quartus_stp` transport with exact names (fixed original USB-Blaster at 6 MHz, instance 0) and one Session whose construction checks endpoint status identity; each event becomes Set-2 bytes (`E0` extended, `F0` break) delivered by `send_all` within 3 s. Connect proves the keyboard endpoint, not that Doom runs. A poisoned Session latches; a close not reporting release is a fault. |

## Lifecycle and concurrency

One worker thread owns the Session and its one handle; Tk callbacks never wait
on the link. Connect (button or `--connect`) is asynchronous; input stays
disabled until the check completes; nothing mutates before it. A halted, wrong
or malformed endpoint closes without input or recovery. A healthy disconnect
allows a new Connect; failures latch the application (new invocation needed).

| Rule | Contract |
| --- | --- |
| Queue | Bounded FIFO of 32 operations (at most 32 queued plus one in flight before a new barrier); failure stops at the first uncertain command; overflow faults the session (a break is never dropped). |
| Ownership | Each press has a source (physical keycode or mouse) and action: the first source sends make, the last release sends break, repeats send nothing; auto-repeat release/press pairs cancel an idle-deferred release; physical keycodes keep Shift/case changes from stranding keys. |
| Release All, focus loss | Clear keyboard and mouse ownership through a queued barrier; held keys must be released before reactivating; mouse uses a local grab (release outside the button is seen); internal focus moves are not focus loss. |
| Close | Disconnect, close or Ctrl-C in the launching terminal set a stop flag, disable submissions and discard unexecuted input; an issued transaction may finish once; a stop before worker start never opens a transport; then a healthy Session releases at most one break per owned action (20 actions) and closes (worst case one command plus releases, each at the transaction bound: the UART timeout or 3 s per JTAG event); the UI stays responsive. Serial open/close run off the UI thread, never duplicated or forcibly retried. |
| Failure | Any command/open/close exception, overflow or poisoned Client latches failure, disables submissions, discards work and closes once; no break, Ping or retry follows; the UI warns that key state may be uncertain. An input ACK proves FIFO acceptance, not consumption. |
| Disposition | The worker publishes an immutable terminal result before exiting, which the UI consumes before discarding the worker; a missing result is a latched fault. Only the UI thread changes widgets, focus and grab. |

## Terminal controls

n2d_control_terminal.py (`src/nand2doom/scripts/n2d_control_terminal.py`)
connects the same Session, sets raw mode, enables focus reporting (`CSI ?1004
h`) and queries the [kitty keyboard protocol](https://sw.kovidgoyal.net/kitty/keyboard-protocol/)
(`CSI ? u` then primary device attributes, 0.5 s bound):

| Mode | Behavior |
| --- | --- |
| Press/release (kitty, foot, Ghostty, WezTerm, recent Alacritty) | Flags 1+2+8 pushed; every key including bare Shift, Ctrl, Alt reports press, repeat, release: press makes, release breaks, repeats send nothing. |
| Tap (other terminals, multiplexers that do not pass the protocol) | Make on the first byte, break 0.5 s later, extended 0.15 s per auto-repeat byte. Bare modifiers send nothing (use F, R, Q); no chords with Shift/Ctrl/Alt; holds shorter than the auto-repeat delay may stutter; a lone Escape is recognized after 50 ms. |

Focus-out releases every held key. F10 or Ctrl-C quits (in press/release mode
Ctrl-C is Ctrl+C after Ctrl's make); every exit, interrupts included, releases
held keys and closes once, then pops the flags, disables focus reporting and
restores the terminal. Failures latch with the uncertain-state warning. One
status line shows held actions.

## Tests

```text
python -B -m unittest discover -s src/nand2doom/scripts/tests -p test_n2d_control.py -v
```

test_n2d_control.py (`src/nand2doom/scripts/tests/test_n2d_control.py`)
uses the real Client on a fake framed transport: exact make/break bytes,
identity and running gating, mixed ownership and repeats, combined holds,
Release All, poison/status/CRC/timeouts/short writes/close failures, no command
after failure; bounded backlog, in-flight close, dropped queued presses, one
handle; keycode repeat and deferred-release cancel, mouse release, focus and
close without hardware. JTAG tests drive the common Session and `send_all`
against the common wire peer: every action's make and break decoded from the
Set-2 bytes, release on close, uncertain delivery latching without release,
unconfirmed close, open failure. Terminal tests: kitty parser (press, repeat,
release, modifiers, arrows, focus, replies, split reads), tap parser,
detection, event-loop holds, focus-loss release, tap repeat extension, device
fault, interrupt still releasing. CLI tests: transport plan and terminal
dispatch. Native Tk smoke proves window construction only.
