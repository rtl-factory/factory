# Toolchain: spec

How the toolchain is built, against its [requirements](prd.md). Decisions: ADR 0009. Method adapted from nand2anything's host setup (`wiki/bkm/bkm_host.md` in that repository).

## Parts

| Part | Role |
|---|---|
| Versions file (`cfg/toolchain.json`) | The single source of every pinned tool version, install location and checksum; Python packages are pinned in `tools/sim/requirements.txt` |
| Readiness check (`tools/toolchain/check.py`) | Reads the versions file; prints PASS, WARN or FAIL per tool (Verilator, Bazel, OSS CAD Suite, Quartus) with the fix (`--tool verilator`, `--tool bazel`, `--tool oss-cad-suite` or `--tool quartus` checks one; the cloud session hook and CI check only the tool they install); exit code is nonzero on any FAIL; never changes the host |
| Install procedure (this spec, Install) | Verbatim, checksum-pinned steps per tool for a Linux host |
| Cloud session hook (`.claude/hooks/session-start.sh`) | Installs the Ubuntu packages, Bazelisk (with a Bazel download mirror when the default hosts are blocked), Verilator, the OSS CAD Suite and Quartus at session start in a cloud agent session, so `bazel test --config=l0 //...` runs with no manual setup; Quartus is fetched from private storage named by an environment variable |

## Versions

| Tool | Version | Source and notes |
|---|---|---|
| Verilator | 5.053 devel, rev `v5.052-654-gdd8a7d5e4` | The one inside the pinned OSS CAD Suite (below), pinned in `cfg/toolchain.json` by version and revision; every host runs the same files, so Bazel cache keys match across hosts and nothing builds Verilator ([#237](https://github.com/rtl-factory/factory/issues/237)). It moves with the suite: a suite bump re-pins Verilator and reruns the full regression. cocotb needs 5.036 or newer |
| Bazelisk | 1.25.0 | GitHub release binary `bazelisk-linux-amd64`, pinned by SHA-256 in `cfg/toolchain.json`; it runs the Bazel that `.bazelversion` pins |
| cocotb | 2.1.0 | pip, pinned with the other simulation packages in `tools/sim/requirements.txt` |
| cocotb-coverage | 2.0 | pip, pinned in `tools/sim/requirements.txt`: functional coverage (`CoverPoint`, `CoverCross`) for the cocotb testbenches; 2.0 is the release for cocotb 2.x (it requires `cocotb>=2.0`), proven here on cocotb 2.1.0 and Verilator 5.052 by `n2a_uart_wire`. Chosen over pyvsc (a constraint solver and covergroups not tied to cocotb, with a native solver dependency we do not need) and over an in-repo helper (cocotb-coverage is maintained and already the cocotb community's choice). Its `python-constraint` dependency is a source-only package that the Python rules build once. Reporting: [sim spec, Coverage](../sim/spec.md#coverage) |
| Python | 3.12 or newer | |
| Yosys, SymbiYosys, solvers and yosys-slang | OSS CAD Suite `2026-11-01` (Yosys 0.70+286, SymbiYosys 0.70, Yices 2.7.0) | One dated bundle keeps Yosys, SymbiYosys, the solvers and the yosys-slang SystemVerilog front end mutually compatible; pinned by release date and SHA-256 in `cfg/toolchain.json` |
| Quartus Prime Lite | 25.1std.0 Build 1129 | Proven for both boards in nand2anything |
| RV32 GCC | xPack `riscv-none-elf-gcc` 15.2.0-1 | GitHub release `xpack-riscv-none-elf-gcc-15.2.0-1-linux-x64.tar.gz`, pinned by SHA-256 in `cfg/toolchain.json` (`rv32_gcc`); Bazel fetches it ([Install (RV32 GCC)](#install-rv32-gcc)). Chosen over a host compiler because Arch's `riscv64-elf-gcc` is built `--disable-tls` and cannot link the pinned Picolibc's thread-local `errno` (#225) |

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

1. Ubuntu packages: installs those listed under `packages` in `cfg/toolchain.json` that are missing (the compiler and `make` for Verilator models, FST waveforms, the nand2doom C programs), as root or with passwordless `sudo`; without either it logs the missing ones.
2. Bazelisk, per Install (Bazelisk).
3. Bazel mirror: when the session proxy blocks `releases.bazel.build`, it writes `BAZELISK_BASE_URL` (Bazel releases on GitHub) to `~/.bazeliskrc`; when it blocks `bcr.bazel.build`, it writes `common --registry=` (the registry's GitHub copy) to `~/.bazelrc`. Both URLs are in `cfg/toolchain.json`. These are machine-only files, never the repository's `.bazelrc`, and a setting already there is kept. Bazelisk then downloads the pinned Bazel.
4. Shared Bazel cache: when the cloud environment sets both `BAZEL_REMOTE_CACHE` (the bucket URL, `https://storage.googleapis.com/<bucket>`) and `BAZEL_REMOTE_CACHE_KEY` (a service account key's JSON), it writes the key to `~/.config/rtl-factory/bazel-cache-key.json`, readable by the session user only, and adds `--config=remote-cache`, `--remote_cache` and `--google_credentials` to `~/.bazelrc` inside a marked block it rewrites each session ([Bazel spec, Remote cache](../bazel/spec.md#remote-cache)). Without both it removes that block, so the cache is off.
5. Side by side: installs the OSS CAD Suite, which carries Verilator (per Install (OSS CAD Suite)), and installs Quartus (Cloud sessions (Quartus) below).
6. Adds `~/.local/bin` (and Quartus's `bin`) to `PATH` through the session's environment file, then prints the full readiness check.

In a fresh session the downloads set the time: about a minute for the suite, longer for Quartus, so the hook's timeout in `.claude/settings.json` is 30 minutes. A session whose tools pass the check starts in seconds.

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
3. Do not add `~/oss-cad-suite/bin` to `PATH`: the suite carries its own Python and cocotb, which would shadow the pinned ones; Bazel links its Verilator ([Install (Verilator)](#install-verilator)). The [formal runner](../fv/spec.md) calls `~/oss-cad-suite/bin/sby` directly, and the suite's wrappers find their own tools.
4. `python3 tools/toolchain/check.py --tool oss-cad-suite` checks the release stamp (`~/oss-cad-suite/VERSION`), the Yosys version, `sby`, `yosys-smtbmc`, `yosys-abc` and `yices-smt2`, and that the `slang` plugin loads.

The cloud session hook runs these steps when the check fails (about 45 s), beside the Quartus steps; a failure is logged and does not stop the others. The `l0` CI job runs steps 1 and 2 when the check fails: on every GitHub-hosted run, and on the self-hosted runner only after a pin change.

## Install (RV32 GCC)

Nothing to install for `bazel test`: the `@rv32_gcc` repository downloads the pinned archive, checks its SHA-256 and unpacks it into Bazel's repository cache (`tools/bazel/extensions.bzl`). The nand2doom C-program tests receive its compiler's path as `N2D_RISCV_PINNED`, and the RV32 tool resolver (`src/nand2doom/scripts/sw_build/n2d_image.py`) uses it and its sibling binutils ahead of `N2D_RISCV_GCC` and `PATH`. A direct (non-Bazel) run still uses `N2D_RISCV_GCC`, else `riscv64-unknown-elf-*` on `PATH`.

## Install (Verilator)

Verilator comes with the OSS CAD Suite (Install (OSS CAD Suite)); nothing else installs it. Bazel's `@verilator` links `bin/verilator` and its tools from the suite's install directory (`$OSS_CAD_SUITE`, else `install_dir`), never from `PATH`, so a host's own Verilator cannot stand in for the pinned one; `$VERILATOR` overrides it for a one-off experiment. The suite's `bin/verilator_bin` is a wrapper that runs the bundled binary with the suite's own libraries, so it runs on any x86-64 Linux host. Models are still compiled by the host's `g++` and `make` ([Bazel spec, Remote cache](../bazel/spec.md)).

`python3 tools/toolchain/check.py --tool verilator` checks it: `verilator` from `VERILATOR` or the suite, its `--version` against the pinned version and revision, `verilator_coverage` beside it for [coverage reports](../sim/spec.md#coverage), and that its `verilated.mk` names no compiler cache.

## Install (Python packages)

No host install: Bazel's Python rules fetch the packages of `tools/sim/requirements_lock.txt` (cocotb, cocotb-coverage, pytest and the rest, each hash-checked) for the tests that need them. Change a pin in `tools/sim/requirements.txt` and regenerate the lock with the `uv pip compile` command in its header; the host needs only `python3` (3.12 or newer) to run the readiness check and read the registries.

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

- The mapping of each tool to regression levels, added to `wiki/bkm/flow.md` once the tools are installed.
