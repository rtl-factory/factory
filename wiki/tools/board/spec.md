# Board console: spec

The board console is `tools/board/board.py`, with its terminal app in `tui.py` (Textual), play mode in `play.py`, the key translation in `keys.py`, and the JTAG chain helpers in `jtag_chain.py` and `jtag_devices.tcl`. It needs Python 3.10+, a terminal and Textual 8.2.8 (`sudo pacman -S python-textual` on Arch, `pip install textual==8.2.8` elsewhere; Bazel tests take it from the pinned lock in `tools/sim/requirements_lock.txt`). Live use needs `jtagconfig`, `quartus_pgm` and `quartus_stp` on `PATH` or under `--quartus-bin`, and pyserial for the project loaders. [Requirements](prd.md).

It is a tool, not a script ([repository layout](../../bkm/layout.md#repository-layout)): it speaks no block's protocol itself. Keys go through the common JTAG keyboard host ([spec](../../src/common/scripts/jtag/spec.md)), loads go through the loader each project names, and the chain helpers know only Quartus and IDCODEs.

## Board declarations

Each project declares its boards in `src/<project>/cfg/board.json`. The console reads every `src/*/cfg/board.json` and names each board `project/board`.

```json
{
  "schema": "n2a-board/1",
  "project": "nand2mario",
  "program": {"label": "Cartridge image", "patterns": ["*.gb", "*.bin"],
              "default": "src/nand2mario/sw/roms/private/super-mario-land-rev1.gb"},
  "pad": {"Up": "Up", "Down": "Down", "Left": "Left", "Right": "Right", "A": "x", "B": "z",
          "Select": "space", "Start": "Return"},
  "load": ["{python}", "src/common/scripts/uart_mem/uart_mem_load.py", "{program}",
           "--project", "n2m ", "--port", "{port}", "--baud", "{baud}", "--yes"],
  "boards": {
    "de10_lite": {"idcode": "031050DD", "baud": 2000000,
                  "sof": "workdir/quartus/n2m_de10_lite/output_files/n2m_de10_lite.sof"}
  }
}
```

| Field | Meaning |
|---|---|
| `program.label`, `program.patterns` | What the program file is called in the console, and its file patterns |
| `program.default` | The program file when none is chosen; empty means the user must choose one |
| `pad` | Optional. The project's controller: each pad button (`Up`, `Down`, `Left`, `Right`, `A`, `B`, `Select`, `Start`) and the key name it stands for; play draws the buttons it names. A key outside the key table is refused |
| `load` | The loader command line, run from the repository root. The fields are `{python}` (this interpreter), `{root}`, `{program}`, `{port}`, `{baud}` and `{board}`; any other field is refused |
| `boards.<board>.idcode` | The FPGA's JTAG IDCODE, eight hex digits |
| `boards.<board>.baud` | The board UART rate of this project's image, 9600 to 12,000,000 |
| `boards.<board>.sof` | The built SOF, relative to the repository root |

The declared loaders today:

| Project | Loader | Rates |
|---|---|---|
| nand2doom | `n2d_play.py --skip-program --controls none`: Ping and Halt, stream load with digest skip, reset PC and Resume | 2 Mbaud on both |
| nand2mario | `uart_mem_load.py --project 'n2m ' --yes` ([common UART memory host](../../src/common/scripts/uart_mem/spec.md#load-script)): identify, hold, write and verify the cartridge, entry, release | 2 Mbaud on both |

Both projects run the factory board rate, 2 Mbaud on every board, and both board UARTs answer the common UART memory endpoint (issue #129). nand2doom's declaration keeps `n2d_play.py` because its load also drives the Doom debug protocol.

## JTAG chain

`jtag_chain.py` holds the Quartus facts every board flow shares. Every external call is bounded (30 s for listings, 180 s for programming) and needs exactly one match; nothing is guessed.

- **Cable**: one `jtagconfig` run. The console accepts only a listing with exactly one cable whose chain is exactly the board's IDCODE, and refuses anything else, naming what it found.
- **Device name**: `quartus_stp -t jtag_devices.tcl CABLE` prints one `N2A_DEVICE <name>` line per device, read-only. The name whose `(0xXXXXXXXX)` suffix is the board's IDCODE is chosen. The same IDCODE has different names on different Quartus installs (`@1: EP4CE115 (0x020F70DD)` or `@1: 10CL120(Y|Z)/EP3C120/.. (0x020F70DD)`), so the name is never written into a config.
- **Programming**: `quartus_pgm -c CABLE -m jtag -o p;SOF@1`. It succeeds only on exit 0 together with the line `Programmer was successful`.

nand2doom's `n2d_play.py` and `n2d_jtag_keyboard.py`, and nand2mario's `n2m_board.py` (`program`, and `joypad` without `--device`), use these helpers.

## Keyboard

`keys.py` maps each physical key, by its X11 keysym name, to its PS/2 Set-2 make and break codes, so the program decides what a key means. The keys covered are the letters, digits and punctuation, Space, Enter, Tab, Escape, Backspace, both Shifts, both Ctrls, both Alts, Caps Lock, F1 to F12, the arrows, Home, End, Page Up, Page Down, Insert, Delete and keypad Enter (E0-prefixed where Set-2 says). A shifted character names the key it was typed on. Keys outside the table are dropped.

`TerminalKeys` turns terminal input into press, repeat, release and tap events. Under the kitty keyboard protocol (flags 1, 2 and 8: every key, modifiers included, as an escape code with its event type) a key's release is its own event. Plain terminal bytes and SS3 sequences carry no release, so they are taps: printable characters, Enter, Tab, Backspace, SS3 F1 to F4, and a lone Escape once 50 ms pass with nothing after it. A kitty terminal can still send some keys plainly (one in front of foot sent X, Z and Space as bare characters while arrows and Enter came with releases), and those are taps too. Ctrl+] (byte `0x1d`, or kitty `CSI 93;5u`) is the leave event, never a board key.

`KeyState` sends one make when a key goes down and ignores repeats while it is held, and one break on its release. A tap makes the key and breaks it 150 ms after the last tap of it, so a key the terminal reports without a release is never left held. `TimedKeys`, for a terminal without the protocol, treats every press as a tap: a key is held while it repeats, and breaks once during the terminal's initial repeat delay. Leaving play breaks every held key. The bytes go through one common JTAG keyboard session (`Session`, `send_all`), in order.

## Console

`board.py` with no command (or `tui`) opens the console, a Textual app. Its controls, top to bottom:

1. **Project** and **Board** drop-down lists, and the **UART port**. The console opens on the board `--project`/`--board` name; without them, one bounded `jtagconfig` run selects the board whose IDCODE is alone on the one chain, and among projects on that board the one with the most recently built SOF. If that start-up choice was a fallback (the chain was busy, say), the first cable check that fails looks again and moves to the detected board. The UART port comes from `--port`, or is the USB serial adapter when there is exactly one; `serial_ports` lists only USB adapters, FTDI first, never built-in `ttyS` ports.
2. **FPGA image** and the project's program file (its `program.label`), prefilled with the board's SOF and `program.default`.
3. **Get <project> running on the <BOARD>**: four steps, each with a light (grey to do, yellow running, green done, red failed), what it found, and on a failure one plain line of what to do:
   1. **Cable**: `check_chain` (one bounded `jtagconfig` run) turns each state into a finding and a fix: `jtagconfig` missing (put Quartus on `PATH` or pass `--quartus-bin`), port permissions (install the [udev rule](../toolchain/spec.md#boards)), the chain in use by another program, no cable, several cables, no device (board off), a different board's IDCODE, or a match. It runs at start-up and on every board change.
   2. **FPGA programmed**: programs the SOF; a missing SOF names the `bazel build //src/<project>:<board>_image` command.
   3. **Program loaded**: runs the project's loader over the UART port; a missing port lists the adapters found, and a loader failure points at programming and the UART wires.
   4. **Keyboard**: opens the JTAG keyboard session on the device found by IDCODE.
4. **Next**: one button (or `n`) that runs the first step not green, then Play.
5. **Buttons** for every step, **Test key** (one Space press and release; the line under the buttons shows how many of its bytes the board accepted), **Play** (`g`) and **Show log** (`l`), which shows or hides every command run and its output.

Changing the board closes the keyboard, clears the steps and checks the cable. One worker thread runs the actions and key sends in order and returns results through a queue the app drains, so the screen never blocks. Programming closes the keyboard session first, because `quartus_pgm` needs the cable alone. A failed key send closes the session and turns the Keyboard step red.

## Play

Play connects the keyboard if needed, then hands the whole terminal to `play.py` until Ctrl+]. It asks the terminal for the kitty keyboard flags followed by primary attributes, which every terminal answers; a flags reply first means the protocol is used (flags 1, 2 and 8 pushed on entry, popped on leaving), and the screen says which. When the project declares a `pad`, play draws a generic handheld controller (D-pad, A, B, Select, Start): a button lights while its key is held, from the keyboard or from the mouse, and a left click on a button (xterm SGR mouse reporting) makes its key on the board until the button is released. Other keys are forwarded too. On leaving, every held key breaks and what the terminal still sends for 150 ms is discarded, so the console never reads play-mode escape codes.

`tools/board/tests/test_tui.py` drives play through a pseudo-terminal (as a kitty and as a plain terminal, with keys and pad clicks) and the console with Textual's pilot against a fake board.

## Command line

```
board.py [tui] [--project X] [--board B] [--port P]   the console (default)
board.py list                                projects, boards, built SOFs, default programs, serial ports
board.py program --project X --board B [--sof S] [--cable C] [--yes]
board.py load    --project X --board B [--program F] --port P [--yes]
board.py keys    --project X --board B [--cable C] [--device D] [--yes] TEXT
```

`--project` and `--board` must together match exactly one board; one alone is enough when it is unique. All commands take `--quartus-bin`. Without `--yes`, `program`, `load` and `keys` print a JSON plan with the exact command and run nothing: no `jtagconfig`, no port. Refusals exit 2 with `board: <reason>` on stderr.
