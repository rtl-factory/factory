# Common RAMs MAS

## Purpose

The on-chip RAMs every project's design stores in: one single-port RAM with a read-first or a write-through collision rule, one simple-dual-port RAM and one true-dual-port RAM, all on one clock. Product RTL instantiates the module by its contract name and never picks a backend; the build's source list does (`default` for Verilator, `intel` for Quartus, per [Libraries](../../../../tools/bazel/spec.md#libraries)). `common` has no HAS; each project's HAS names these RAMs where its blocks store. Code: `src/common/rtl/memory/`. Ported from the common memory HAS in nand2anything ([aeebe92](https://github.com/amichai-bd/nand2anything/blob/aeebe92/wiki/src/common/memory/HAS.md)), written here from the code.

## Interface

### Parameters

Every RAM takes the same three:

| Parameter | Default | Meaning |
|---|---|---|
| `DATA_WIDTH` | 32 | Word width, at least 1. |
| `DEPTH` | 256 | Words, a power of two and at least 2. |
| `ADDR_WIDTH` | `$clog2(DEPTH)` | Must equal `$clog2(DEPTH)`; #126 removes it. |

An illegal value fails elaboration through `ASSERT_PARAM` (`g_invalid_data_width`, `g_invalid_depth`, `g_invalid_addr_width`), naming the module and the rule. The Intel backends add `INTEL_DEVICE_FAMILY` (`"MAX 10"`) and `INTEL_RAM_BLOCK_TYPE` (`"M9K"`), which another Intel board may override without changing the contract.

### Modules

All ports are in the `clk_i` domain. A read is registered: the word at the address presented before an edge is on the read port after that edge, on every edge. Writes store the full word. There is no reset, no initialization and no byte enable; the contents start unknown.

| Module | Ports | Same-edge, same-address collision |
|---|---|---|
| `n2a_sync_ram` | `write_enable_i`, `address_i`, `write_data_i`, `read_data_o` | Read-first: returns the old word. |
| `n2a_sync_ram_wt` | as `n2a_sync_ram` | Write-through: returns the new word. |
| `n2a_sdp_ram` | `write_enable_i`, `write_address_i`, `write_data_i`, `read_address_i`, `read_data_o` | Read-first across the two ports: returns the old word. |
| `n2a_sdp_ram_enabled` | as `n2a_sdp_ram`, plus `read_enable_i` | As `n2a_sdp_ram`; with `read_enable_i` low the read port holds its last word. |
| `n2a_tdp_ram` | per port `a_`/`b_`: `write_enable_i`, `address_i`, `write_data_i`, `read_data_o` | Read-first on every port relationship (same-port and mixed-port). Two enabled writes to one address in one edge are forbidden; no winner is promised. |

## Microarchitecture

Each portable backend is the conventional inferred template, through the `MEM_*` macros of `src/common/rtl/pkg/macros.vh`: one array, one read and one write process per port, so nonblocking semantics give the read-first rule with no bypass. `n2a_sync_ram_wt` keeps that read-first array and registers the written word and a one-bit "this edge wrote" flag beside it; the output selects the written word after a write edge, which is exactly write-through on a single port.

The Intel backends instantiate `altsyncram` with unregistered outputs and `OLD_DATA` read-during-write: `SINGLE_PORT` for `n2a_sync_ram`, `DUAL_PORT` (port A writes, port B reads) for `n2a_sdp_ram_enabled`, and `BIDIR_DUAL_PORT` with `OLD_DATA` on both ports and across them for `n2a_tdp_ram`. That is the same one-edge, read-first behaviour as the portable file.

| Module | Portable file | Intel file |
|---|---|---|
| `n2a_sync_ram` | `n2a_sync_ram.sv` | `n2a_sync_ram_intel.sv` |
| `n2a_sync_ram_wt` | `n2a_sync_ram_wt.sv` | none |
| `n2a_sdp_ram`, `n2a_sdp_ram_enabled` | `n2a_sdp_ram_portable.sv` | `n2a_sdp_ram_intel.sv` |
| `n2a_tdp_ram` | `n2a_tdp_ram_portable.sv` | `n2a_tdp_ram_intel.sv` |

The two files of one module define the same module name, so each waives Verilator's `DECLFILENAME` for itself only. An Intel file never enters a Verilator product target; Verilator lints its wiring against the port stub `src/common/dv/memory/n2a_altsyncram_port_stub.sv`.

## Clocks and resets

One domain, `clk_i`. No reset, and no crossing.

## Design choices

- One module name per contract with backends chosen by the source list, against a backend parameter in product RTL: the design never names a vendor, and a new backend is one file.
- Read-first collisions, because that is what the inferred template and `altsyncram` with `OLD_DATA` both give with no bypass logic. Write-through is a separate module, so a consumer that needs the new word says so by name.
- No reset or initialization: an M9K cannot reset its contents, and a reset that only simulation honours would hide a bug the board shows.

## Verification

The contract tests are the oracle, not either backend: `src/common/dv/memory/test_sync_ram.py`, `test_sync_ram_wt.py` and `test_tdp_ram.py`, registered in nand2doom's `targets.json` as the `common_sync_ram*`, `common_sync_ram_wt*` and `common_tdp_ram*` targets. They run the default and byte-plane geometries, the Intel backends through the port stub (`common_sync_ram_intel_stub`, `common_tdp_ram_intel_stub`, flavor `intel`), and every illegal parameter as an expected elaboration failure. `n2a_sdp_ram` is checked through its consumers (the nand2doom fetch BTB and UART stream queue). On the board, the Quartus report gate checks that each array maps to M9K blocks in the expected mode and geometry ([DE10-Lite board spec](../../../nand2doom/fpga/de10_lite/board-spec.md#memory-implementation)); a new consumer adds its arrays to that gate.
