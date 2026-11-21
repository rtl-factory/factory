# Board console: spec

The board console is `tools/board/board.py`, with its window in `gui.py`, the key translation in `keys.py`, and the JTAG chain helpers in `jtag_chain.py` and `jtag_devices.tcl`. It needs Python 3.10+. The window needs Tk (`python3-tk`). Live use needs `jtagconfig`, `quartus_pgm` and `quartus_stp` on `PATH` or under `--quartus-bin`, and pyserial for the project loaders. [Requirements](prd.md).

It is a tool, not a script ([repository layout](../../bkm/workflow/repo-layout.md)): it speaks no block's protocol itself. Keys go through the common JTAG keyboard host ([spec](../../src/common/scripts/jtag/spec.md)), loads go through the loader each project names, and the chain helpers know only Quartus and IDCODEs.

## Board declarations

Each project declares its boards in `src/<project>/cfg/board.json`. The console reads every `src/*/cfg/board.json` and names each board `project/board`.

```json
{
  "schema": "n2a-board/1",
  "project": "nand2mario",
  "program": {"label": "Cartridge image", "patterns": ["*.gb", "*.bin"], "default": ""},
  "load": ["{python}", "src/nand2mario/scripts/board/n2m_board.py", "load", "{program}",
           "--board", "{board}", "--port", "{port}", "--baud", "{baud}", "--yes"],
  "boards": {
    "de10_lite": {"idcode": "031050DD", "baud": 115200,
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
| nand2doom | `n2d_play.py --skip-program --controls none`: Ping and Halt, stream load with digest skip, reset PC and Resume | DE10-Lite 1 Mbaud, DE2-115 3 Mbaud |
| nand2mario | `n2m_board.py load --yes`: hold, write and verify the cartridge, release | 115200 on both |

Issue #129 moves both projects to the common UART memory endpoint at 2 Mbaud, after which both declarations name one loader.

## JTAG chain

`jtag_chain.py` holds the Quartus facts every board flow shares. Every external call is bounded (30 s for listings, 180 s for programming) and needs exactly one match; nothing is guessed.

- **Cable**: one `jtagconfig` run. The console accepts only a listing with exactly one cable whose chain is exactly the board's IDCODE, and refuses anything else, naming what it found.
- **Device name**: `quartus_stp -t jtag_devices.tcl CABLE` prints one `N2A_DEVICE <name>` line per device, read-only. The name whose `(0xXXXXXXXX)` suffix is the board's IDCODE is chosen. The same IDCODE has different names on different Quartus installs (`@1: EP4CE115 (0x020F70DD)` or `@1: 10CL120(Y|Z)/EP3C120/.. (0x020F70DD)`), so the name is never written into a config.
- **Programming**: `quartus_pgm -c CABLE -m jtag -o p;SOF@1`. It succeeds only on exit 0 together with the line `Programmer was successful`.

nand2doom's `n2d_play.py` and `n2d_jtag_keyboard.py`, and nand2mario's `n2m_board.py` (`program`, and `joypad` without `--device`), use these helpers.

## Keyboard

The window reports keys by Tk keysym. `keys.py` maps the physical key to its PS/2 Set-2 make and break codes, so the program decides what a key means. The keys covered are the letters, digits and punctuation, Space, Enter, Tab, Escape, Backspace, both Shifts, both Ctrls, both Alts, Caps Lock, F1 to F12, the arrows, Home, End, Page Up, Page Down, Insert, Delete and keypad Enter (E0-prefixed where Set-2 says). Shifted keysyms map back to their key. Keys outside the table are dropped.

`KeyState` sends one make when a key goes down and ignores repeated presses while it is held. A release is held back for 30 ms and cancelled if the same key is pressed again within that time, because X11 auto-repeat reports a held key as a release followed at once by a press. Losing focus breaks every held key. The bytes go through one common JTAG keyboard session (`Session`, `send_all`), in order.

## Window

From top to bottom:

1. **Board**: every `project/board`. Choosing one fills in its SOF and default program.
2. **SOF**, a browse button, and **Program FPGA**. A browsed SOF whose file name is one board's default SOF selects that board.
3. **Program file**, labelled by the project, a browse button, and **Load program**.
4. **UART port**: the serial ports pyserial lists, or a typed path.
5. **The keyboard pane**: click it and type. **Connect keyboard** finds the cable and device by IDCODE and opens the session. The status line names the board and the cable.
6. **A log**: every command run and its output, and every error.

One worker thread runs the actions and the key sends in order, so the window never blocks. Programming closes the keyboard session first, because `quartus_pgm` needs the cable alone. After programming, the user loads the program and connects the keyboard again. A failed key send closes the session and logs why.

## Command line

```
board.py [gui] [--port P]                    the window (default)
board.py list                                projects, boards, built SOFs, default programs, serial ports
board.py program --project X --board B [--sof S] [--cable C] [--yes]
board.py load    --project X --board B [--program F] --port P [--yes]
board.py keys    --project X --board B [--cable C] [--device D] [--yes] TEXT
```

`--project` and `--board` must together match exactly one board; one alone is enough when it is unique. All commands take `--quartus-bin`. Without `--yes`, `program`, `load` and `keys` print a JSON plan with the exact command and run nothing: no `jtagconfig`, no port. Refusals exit 2 with `board: <reason>` on stderr.
