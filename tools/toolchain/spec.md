# Toolchain: spec

How the toolchain is built, against its [requirements](prd.md). Decisions: ADR 0009. Method adapted from nand2anything's host setup (`wiki/bkm/bkm_host.md` in that repository).

## Parts

| Part | Role |
|---|---|
| Versions file (`cfg/toolchain.json`) | The single source of every pinned version, install location and checksum |
| Readiness check (`tools/toolchain/check.py`) | Reads the versions file; prints PASS, WARN or FAIL per tool with the fix; exit code is nonzero on any FAIL; never changes the host |
| Install procedure (this spec, Install) | Verbatim, checksum-pinned steps per tool for a Linux host |
| Cloud session hook (`.claude/hooks/session-start.sh`) | Installs the pinned tools at session start in a cloud agent session; fetches Quartus from private storage named by an environment variable |

## Versions

| Tool | Version | Source and notes |
|---|---|---|
| Verilator | 5.052 | Built from the upstream tag; cocotb needs 5.036 or newer; 5.054 is newer but unproven here |
| cocotb | 2.1.0 | pip, with a coverage library for functional coverage (choose in the build issue) |
| Python | 3.12 or newer | |
| Yosys, SymbiYosys and solvers | one dated OSS CAD Suite release (to verify) | One bundle keeps Yosys, SymbiYosys and the SMT solvers mutually compatible |
| Quartus Prime Lite | 25.1std.0 Build 1129 | Proven for both boards in nand2anything |

Quartus files, downloaded in a browser from Altera's *Quartus Prime Lite 25.1 for Linux* page, "Individual Files" tab (scripted downloads are refused):

| File | SHA-1 | For |
|---|---|---|
| `QuartusLiteSetup-25.1std.0.1129-linux.run` | `ce0773469eacab5b7035c175484625f4ec3737d1` | Quartus |
| `max10-25.1std.0.1129.qdz` | `8936dac1b092853f3b49a67fef22968ec10f8087` | DE10-Lite, MAX 10 `10M50DAF484C7G` |
| `cyclone-25.1std.0.1129.qdz` | `835d2b1732549294eed625b692d044135499b5e8` | DE2-115, Cyclone IV E `EP4CE115F29C7` |

## Install (Quartus)

1. Put the three files in one directory and verify them with `sha1sum`.
2. Install headless: `./QuartusLiteSetup-25.1std.0.1129-linux.run --mode unattended --unattendedmodeui none --accept_eula 1 --installdir $HOME/intelFPGA_lite/25.1` (accept the licence first).
3. The installer picks up the MAX 10 file but not Cyclone IV: unzip `cyclone-25.1std.0.1129.qdz` into the install directory.
4. Check that `quartus_sh --version` reports `25.1std.0 Build 1129` and that the device families include both `Cyclone IV E` and `MAX 10`.

Known host issues: a `setlocale` warning needs an `en_US.UTF-8` locale; on Arch, `quartus_map` and friends need `libxcrypt-compat`.

The other tools' install steps are written by the build issue that installs them.

## Boards

| Board | Device | Programmer |
|---|---|---|
| DE2-115 | Cyclone IV E EP4CE115F29C7 | On-board USB-Blaster |
| DE10-Lite | MAX 10 10M50DAF484C7G | On-board USB-Blaster |

On the board host: a udev rule granting the Altera USB devices (vendor `09fb`) to the user; the JTAG daemon runs on that host; `quartus_pgm -l` lists the cable and `quartus_pgm -m jtag -o "p;<file>.sof"` programs. The FTDI serial port needs the `dialout` group. Where builds and programming run is in `wiki/bkm/fpga/fpga.md`.

## Open

- Which coverage library the cocotb testbenches use for functional coverage.
- Which OSS CAD Suite release to pin.
- Where the private Quartus files are stored for cloud sessions, and the environment variable that names it.
- The mapping of each tool to regression levels, added to `wiki/bkm/workflow/ci-levels.md` once the tools are installed.
