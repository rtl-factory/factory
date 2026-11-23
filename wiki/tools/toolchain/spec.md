# Toolchain: spec

How the toolchain is built, against its [requirements](prd.md). Decisions: ADR 0009. Method adapted from nand2anything's host setup (`wiki/bkm/bkm_host.md` in that repository).

## Parts

| Part | Role |
|---|---|
| Versions file (`cfg/toolchain.json`) | The single source of every pinned tool version, install location and checksum; Python packages are pinned in `tools/sim/requirements.txt` |
| Readiness check (`tools/toolchain/check.py`) | Reads the versions file; prints PASS, WARN or FAIL per tool (Verilator, Bazel, OSS CAD Suite, Quartus) with the fix (`--tool verilator`, `bazel`, `oss-cad-suite` or `quartus` checks one); exit code is nonzero on any FAIL; never changes the host |
| Install procedure (this spec, Install) | Verbatim, checksum-pinned steps per tool for a Linux host |
| Cloud session hook (`.claude/hooks/session-start.sh`) | Installs the Ubuntu packages, Bazelisk (with a Bazel download mirror when the default hosts are blocked), Verilator, the OSS CAD Suite and Quartus at session start in a cloud agent session, so `bazel test --config=l0 //...` runs with no manual setup; Quartus is fetched from private storage named by an environment variable |

## Versions

| Tool | Version | Source and notes |
|---|---|---|
| Verilator | 5.052 | Built from the upstream tag; pinned in `cfg/toolchain.json`; cocotb needs 5.036 or newer; 5.054 is newer but unproven here |
| Bazelisk | 1.25.0 | GitHub release binary `bazelisk-linux-amd64`, pinned by SHA-256 in `cfg/toolchain.json`; it runs the Bazel that `.bazelversion` pins |
| cocotb | 2.1.0 | pip, pinned with the other simulation packages in `tools/sim/requirements.txt`; a coverage library for functional coverage is chosen in the build issue |
| Python | 3.12 or newer | |
| Yosys, SymbiYosys, solvers and yosys-slang | OSS CAD Suite `2026-11-01` (Yosys 0.70+286, SymbiYosys 0.70, Yices 2.7.0) | One dated bundle keeps Yosys, SymbiYosys, the solvers and the yosys-slang SystemVerilog front end mutually compatible; pinned by release date and SHA-256 in `cfg/toolchain.json` |
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

## Cloud session hook

`.claude/hooks/session-start.sh` (registered in `.claude/settings.json`) runs at the start of every cloud agent session and does nothing elsewhere. It makes `bazel test --config=l0 //...` work in a fresh session with no manual setup. Each step runs the readiness check first and is skipped when it passes; a failed step is logged and does not stop the others; nothing is written inside the repository.

1. Ubuntu packages: installs those listed under `packages` in `cfg/toolchain.json` that are missing (the Verilator build, FST waveforms, the nand2doom C programs), as root or with passwordless `sudo`; without either it logs the missing ones.
2. Bazelisk, per Install (Bazelisk).
3. Bazel mirror: when the session proxy blocks `releases.bazel.build`, it writes `BAZELISK_BASE_URL` (Bazel releases on GitHub) to `~/.bazeliskrc`; when it blocks `bcr.bazel.build`, it writes `common --registry=` (the registry's GitHub copy) to `~/.bazelrc`. Both URLs are in `cfg/toolchain.json`. These are machine-only files, never the repository's `.bazelrc`, and a setting already there is kept. Bazelisk then downloads the pinned Bazel.
4. Side by side: builds Verilator from the pinned tag into `~/verilator` (per Install (Verilator, CI)), installs the OSS CAD Suite (per Install (OSS CAD Suite)) and installs Quartus (Cloud sessions (Quartus) below).
5. Adds `~/.local/bin` and `~/verilator/bin` (and Quartus's `bin`) to `PATH` through the session's environment file, then prints the full readiness check.

In a fresh session the Verilator build sets the time: about 5 to 8 minutes on 4 idle cores, and much longer on a loaded machine, so the hook's timeout in `.claude/settings.json` is 30 minutes. A session whose tools pass the check starts in seconds.

## Install (Bazelisk)

1. Download `bazelisk-linux-amd64` from the Bazelisk release pinned in `cfg/toolchain.json` and check it with `sha256sum` against the pin.
2. Install it as `~/.local/bin/bazelisk` (mode 755) with a link `~/.local/bin/bazel` to it, and put `~/.local/bin` on `PATH`.
3. Behind a proxy that blocks `releases.bazel.build` or `bcr.bazel.build`, set the mirror as in Cloud session hook, step 3.
4. `python3 tools/toolchain/check.py --tool bazel` passes for the pinned binary, warns for another `bazel` (such as CI's `setup-bazel`) and fails for none.

## Cloud sessions (Quartus)

The captain keeps the three Quartus files in a private, link-shared Google Drive folder, split into parts under about 95 MB (`<file>.part00`, `.part01`, ...). The cloud environment's setting `QUARTUS_FILES_FOLDER` holds the folder's URL; `drive.google.com` and `drive.usercontent.google.com` must be on the environment's allowed hosts.

At session start, the cloud session hook:

1. Exits early when the readiness check already passes.
2. Lists the folder through its embedded view and downloads every `.partNN` file from `drive.usercontent.google.com`, outside the repository.
3. Concatenates the parts of each file and checks every file against the SHA-1 pins in `cfg/toolchain.json`; it installs nothing on a mismatch, and never runs a script from the folder.
4. Installs per the steps above, deletes the downloads, runs the readiness check and adds `PATH`, `QUARTUS_ROOTDIR` and `LC_ALL` to the session.

A first install takes about three minutes (about 40 s download, 2 min install, 9.4 GB installed). Without `QUARTUS_FILES_FOLDER` the hook prints a note and the session starts without Quartus.

Known host issues: a `setlocale` warning needs an `en_US.UTF-8` locale; on Arch, `quartus_map` and friends need `libxcrypt-compat`.

## Install (OSS CAD Suite)

1. Download `oss-cad-suite-linux-x64-20261101.tgz` from the `2026-11-01` release of `YosysHQ/oss-cad-suite-build` on GitHub (the URL is in `cfg/toolchain.json`; about 750 MB) and check it with `sha256sum` against the pin.
2. Unpack it into the home directory: `tar -xzf oss-cad-suite-linux-x64-20261101.tgz -C ~` gives `~/oss-cad-suite/` (about 2.5 GB).
3. Do not add `~/oss-cad-suite/bin` to `PATH`: the suite carries its own Verilator, Python and cocotb, which would shadow the pinned ones. The [formal runner](../fv/spec.md) calls `~/oss-cad-suite/bin/sby` directly, and the suite's wrappers find their own tools.
4. `python3 tools/toolchain/check.py --tool oss-cad-suite` checks the release stamp (`~/oss-cad-suite/VERSION`), the Yosys version, `sby`, `yosys-smtbmc`, `yosys-abc` and `yices-smt2`, and that the `slang` plugin loads.

The cloud session hook runs these steps when the check fails (about 45 s), beside the Verilator build and the Quartus steps; a failure is logged and does not stop the others. The `l0` CI job runs steps 1 and 2 when the check fails: on every GitHub-hosted run, and on the self-hosted runner only after a pin change.

## Install (Verilator, CI)

On a GitHub-hosted runner, the `l0` CI job ([CI levels](../../bkm/flow.md#ci-levels)) builds Verilator from the pinned tag on Ubuntu 24.04 (`bison`, `flex`, `libfl-dev`, `help2man`, `zlib1g-dev`; `autoconf`, `./configure --prefix=~/verilator`, `make`, `make install`) and caches the install by the pinned tag, so only the first run after a pin change rebuilds it. The same steps install it on a Linux host. The job also installs `liblz4-dev` (FST waveform targets) and, for the nand2doom C-program targets, `gcc-riscv64-unknown-elf` and `picolibc-riscv64-unknown-elf` from Ubuntu's archive.

Configure with `ac_cv_prog_OBJCACHE=` (`ac_cv_prog_OBJCACHE= ./configure --prefix=~/verilator`): otherwise a `ccache` found on `PATH` is baked into the installed `verilated.mk` (`OBJCACHE ?= ccache`), and every model build in Bazel's sandbox, where `ccache` is missing, fails.

`python3 tools/toolchain/check.py --tool verilator` checks the Verilator that Bazel's `@verilator` finds (`$VERILATOR`, then `PATH`, then `~/verilator/bin`) against the pin, and fails an install whose `verilated.mk` names a compiler cache. The cloud session hook builds it with the same steps when the check fails.

The other tools' install steps are written by the build issue that installs them.

## Boards

| Board | Device | Programmer |
|---|---|---|
| DE2-115 | Cyclone IV E EP4CE115F29C7 | On-board USB-Blaster |
| DE10-Lite | MAX 10 10M50DAF484C7G | On-board USB-Blaster |

On the board host, install the udev rule `tools/toolchain/51-fpga-boards.rules` once. It opens the USB-Blaster (vendor `09fb`, products `6001`, `6010`, `6810`) and the FTDI TTL-232R UART cable (`0403:6001`, the stock FT232R ID, so every FT232R adapter on the host) to every local user, so both the desktop user and the CI runner's user service reach them with no group change or re-login:

```bash
sudo install -m 644 tools/toolchain/51-fpga-boards.rules /etc/udev/rules.d/ && sudo udevadm control --reload && sudo udevadm trigger
```

Without it, `jtagconfig` reports `Insufficient port permissions` and the serial port belongs to the distro's serial group (`dialout` on Debian and Ubuntu, `uucp` on Arch). With it installed, `jtagconfig` lists the cable and the board's IDCODE, the JTAG daemon runs on that host, and `quartus_pgm -m jtag -o "p;<file>.sof"` programs. Where builds and programming run is in `wiki/bkm/fpga.md`.

## Open

- Which coverage library the cocotb testbenches use for functional coverage.
- The mapping of each tool to regression levels, added to `wiki/bkm/flow.md` once the tools are installed.
