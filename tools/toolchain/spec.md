# Toolchain: spec

How the toolchain is built, against its [requirements](prd.md). Decisions: ADR 0009.

!!! warning "To verify before pinning"
    Facts below marked **to verify** come from search results, not from the vendor's own pages. Each build issue confirms them first.

## Images

| Image | Base | Contents |
|---|---|---|
| `rtl-sim` | Ubuntu 24.04 LTS | Verilator, Python 3 with cocotb, Yosys and SymbiYosys with SMT solvers, waveform tools |
| `rtl-fpga` | A Linux supported by the chosen Quartus release | Quartus Prime Lite with the Cyclone IV E and MAX 10 device support only, command-line flow |

The devcontainer uses `rtl-sim`; FPGA builds run in `rtl-fpga` on the runner, which has the boards attached.

## Versions

One versions file pins every tool; the build issues create it.

| Tool | Version | Notes |
|---|---|---|
| Verilator | 5.054 (to verify) | cocotb needs 5.036 or newer |
| cocotb | latest release supporting the pinned Verilator (to verify) | |
| Yosys, SymbiYosys | to choose | |
| Quartus Prime Lite | 25.1std (to verify) | Latest Lite release found; MAX 10 supported. **Cyclone IV E support in 25.1std is unconfirmed**: if dropped, pin the last release that has it, for the DE2-115 at least |

## Boards

| Board | Device | Programmer |
|---|---|---|
| DE2-115 | Cyclone IV E EP4CE115F29C7 | On-board USB-Blaster |
| DE10-Lite | MAX 10 10M50DAF484C7G | On-board USB-Blaster (to verify) |

Programming from Linux needs, on the runner host: a udev rule for the USB-Blaster (vendor `09fb`), the JTAG daemon (`jtagd`) with its parts list, then `quartus_pgm -l` to list cables and `quartus_pgm -m jtag -o "p;<file>.sof"` to program.

## Open

- Licence: whether a Quartus Prime Lite image may be pushed to a registry, or only built and kept on the runner (Altera publishes official images; the licence for a Lite image is unclear).
- Registry for the images.
- Mapping of each tool to regression levels (lint and smoke simulation in L0, FPGA builds higher) belongs in `wiki/bkm/workflow/ci-levels.md` once the images exist.
