# Board console: spec

The board console is `tools/board/board.py`, with its terminal UI in `tui.py`, the key translation in `keys.py`, and the JTAG chain helpers in `jtag_chain.py` and `jtag_devices.tcl`. It needs Python 3.10+ and a terminal; nothing outside the standard library besides pyserial. Live use needs `jtagconfig`, `quartus_pgm` and `quartus_stp` on `PATH` or under `--quartus-bin`, and pyserial for the project loaders. [Requirements](prd.md).

It is a tool, not a script ([repository layout](../../bkm/layout.md#repository-layout)): it speaks no block's protocol itself. Keys go through the common JTAG keyboard host ([spec](../../src/common/scripts/jtag/spec.md)), loads go through the loader each project names, and the chain helpers know only Quartus and IDCODEs.

## Board declarations

Each project declares its boards in `src/<project>/cfg/board.json`. The console reads every `src/*/cfg/board.json` and names each board `project/board`.

```json
{
  "schema": "n2a-board/1",
  "project": "nand2mario",
  "program": {"label": "Cartridge image", "patterns": ["*.gb", "*.bin"], "default": ""},
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
| `program.label`, `program.patterns` | What the program file is called in the window, and its file-picker patterns |
| `program.default` | The program file when none is chosen; empty means the user must choose one |
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

`TerminalKeys` turns terminal input into press, repeat and release events. Under the kitty keyboard protocol (flags 1, 2 and 8: every key, modifiers included, as an escape code with its event type) a key's release is its own event. Plain terminal bytes carry presses only: printable characters, Enter, Tab, Backspace, the CSI and SS3 forms of the arrows, Home, End, F1 to F12 and the `~` keys, and a lone Escape once 50 ms pass with nothing after it. Ctrl+] (byte `0x1d`, or kitty `CSI 93;5u`) is the leave event, never a board key.

`KeyState` sends one make when a key goes down and ignores repeats while it is held, and one break on its release. `TimedKeys`, for a terminal without releases, holds a key while it repeats and breaks it 150 ms after its last press or repeat; a held key therefore breaks once during the terminal's initial repeat delay. Leaving play mode breaks every held key. The bytes go through one common JTAG keyboard session (`Session`, `send_all`), in order. A key with no session connected is reported in the log, not sent.

## Terminal UI

`board.py` with no command (or `tui`) takes over the terminal: raw mode, the alternate screen, restored on exit. It asks the terminal for the kitty keyboard flags followed by primary attributes, which every terminal answers; a flags reply first means the protocol is used, and the log's first lines say which. Bytes typed while it waits are kept as input.

Every option is on the screen with its key, beside the value it acts on, so nothing has to be remembered. Top to bottom: the mode (command or play); **Setup**: the board (`project/board`, IDCODE), the SOF (marked when not built), the project's program file, the UART port and rate; **JTAG keyboard**: the cable check, the keyboard state (not connected, connecting, connected on which cable, or failed with the reason), the last key sent with how many of its bytes the board accepted and the count sent, and whether the terminal reports key releases; **Next**: what to do now, from the cable check and the keyboard state; then the log: every command run and its output, and every error.

The cable check (`check_chain`, one bounded `jtagconfig` run, at start-up, on `c`, on a board change and before every keyboard connect) turns each state into a finding and a next step: `jtagconfig` missing (put Quartus on `PATH` or pass `--quartus-bin`), port permissions (install the [udev rule](../toolchain/spec.md#boards)), the chain in use by another program, no cable, several cables, no device (board off), a different board's IDCODE (press `b`), or a match. While this console's keyboard is connected it holds the chain, so the check reports that instead of running.

The console opens on the board `--project`/`--board` name; without them, one bounded `jtagconfig` run selects the board whose IDCODE is alone on the one chain, and among projects on that board the one with the most recently built SOF. With no cable, several cables or devices, or an unknown IDCODE it opens on the first board. The log says which board was selected and why. The UART port comes from `--port`, or is the port when pyserial lists exactly one.

| Key | Action |
|---|---|
| `p` | Program the SOF into the board |
| `l` | Load the program file over the UART port with the project's loader |
| `c` | Check the cable again |
| `k` | Connect the JTAG keyboard: check the cable, find the device by IDCODE and open the session |
| `t` | Test the keyboard: send one Space press and release (connecting first if needed) and show how many bytes the board accepted |
| `i` | Play: connect the keyboard if it is not, then forward every key press and release to the board until Ctrl+]. Under the kitty protocol the console pushes flags 1, 2 and 8 on entry and pops them on leaving |
| `b` | The next board; its SOF and program file are filled in, the keyboard is closed and the cable checked |
| `s`, `f`, `u` | Edit the SOF path, the program file or the UART port on the key line (Enter keeps, Esc cancels). A SOF whose file name is one board's default SOF selects that board |
| `q`, Ctrl+C | Quit, closing the keyboard session |

One worker thread runs the actions and the key sends in order, so the screen never blocks. Programming closes the keyboard session first, because `quartus_pgm` needs the cable alone. After programming, the user loads the program and connects the keyboard again. A failed key send closes the session and logs why.

`tools/board/tests/test_tui.py` drives the console through a pseudo-terminal against a fake board, answering the protocol query as a kitty terminal and as a plain one.

## Command line

```
board.py [tui] [--project X] [--board B] [--port P]   the terminal console (default)
board.py list                                projects, boards, built SOFs, default programs, serial ports
board.py program --project X --board B [--sof S] [--cable C] [--yes]
board.py load    --project X --board B [--program F] --port P [--yes]
board.py keys    --project X --board B [--cable C] [--device D] [--yes] TEXT
```

`--project` and `--board` must together match exactly one board; one alone is enough when it is unique. All commands take `--quartus-bin`. Without `--yes`, `program`, `load` and `keys` print a JSON plan with the exact command and run nothing: no `jtagconfig`, no port. Refusals exit 2 with `board: <reason>` on stderr.
