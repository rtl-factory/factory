# Common RAMs MAS

## Purpose

The on-chip RAMs every project's design stores in: one single-port RAM, one simple-dual-port RAM and one true-dual-port RAM, all on one clock and all read-first on a collision, and request wrappers over the single-port and true-dual-port RAMs that add read enables, read-valid, a held read word and reset. Product RTL instantiates the module by its contract name and never picks a backend; the build's source list does (`default` for Verilator, `intel` for Quartus, per [Libraries](../../../../tools/bazel/spec.md#libraries)). `common` has no HAS; each project's HAS names these RAMs where its blocks store. Code: `src/common/rtl/memory/`. Ported from the common memory HAS in nand2anything ([aeebe92](https://github.com/amichai-bd/nand2anything/blob/aeebe92/wiki/src/common/memory/HAS.md)), written here from the code.

## Interface

### Parameters

Every RAM takes the same two:

| Parameter | Default | Meaning |
|---|---|---|
| `DATA_WIDTH` | 32 | Word width, at least 1. |
| `DEPTH` | 256 | Words, a power of two and at least 2. The address ports are `$clog2(DEPTH)` bits wide. |

An illegal value fails elaboration through `ASSERT_PARAM` (`g_invalid_data_width`, `g_invalid_depth`), naming the module and the rule; a request wrapper fails through the RAM it instantiates. The Intel backends add `INTEL_DEVICE_FAMILY` (`"MAX 10"`) and `INTEL_RAM_BLOCK_TYPE` (`"M9K"`), which another Intel board may override without changing the contract.

### Modules

All ports are in the `clk_i` domain. A read is registered: the word at the address presented before an edge is on the read port after that edge, on every edge. Writes store the full word. There is no reset, no initialization and no byte enable; the contents start unknown.

| Module | Ports | Same-edge, same-address collision |
|---|---|---|
| `n2a_sync_ram` | `write_enable_i`, `address_i`, `write_data_i`, `read_data_o` | Read-first: returns the old word. |
| `n2a_sdp_ram` | `write_enable_i`, `write_address_i`, `write_data_i`, `read_address_i`, `read_data_o` | Read-first across the two ports: returns the old word. |
| `n2a_sdp_ram_enabled` | as `n2a_sdp_ram`, plus `read_enable_i` | As `n2a_sdp_ram`; with `read_enable_i` low the read port holds its last word. |
| `n2a_tdp_ram` | per port `a_`/`b_`: `write_enable_i`, `address_i`, `write_data_i`, `read_data_o` | Read-first on every port relationship (same-port and mixed-port). Two enabled writes to one address in one edge are forbidden; no winner is promised. |
| `n2a_sync_ram_enabled` | `reset_i`, `read_enable_i`, `write_enable_i`, `address_i`, `write_data_i`, `read_data_o`, `read_valid_o` | As `n2a_sync_ram`: an enabled read and a write of its address in one edge return the old word, with `read_valid_o` high. |
| `n2a_tdp_ram_enabled` | per port `a_`/`b_`: `reset_i`, `read_enable_i`, `write_enable_i`, `address_i`, `write_data_i`, `read_data_o`, `read_valid_o` | As `n2a_tdp_ram`: an enabled read returns the old word when the same port or the other port writes its address in that edge. The forbidden double write is asserted (`write_collision`). |

The two request wrappers serve owners that issue requests rather than read every edge. On each edge, a port outside its reset with `read_enable_i` high presents the word at its address on `read_data_o` with `read_valid_o` high in the next cycle. With `read_enable_i` low, or under the port's `reset_i`, `read_valid_o` is low and `read_data_o` holds the last word read. A write under the port's reset is dropped. A read and a write of one word in one edge, on one port or across the two, are read-first like the RAM inside: the read returns the word from before the edge, and a later enabled read of that address returns the written word. A consumer that needs the written word in the same cycle forwards its own write data; no write-through RAM is offered. `reset_i` is synchronous and per port, so one owner (a loader, a boot clear) can keep its port while the other port's owner is in reset. There is no byte enable and one clock: no consumer needs either.

## Microarchitecture

Each portable backend is the conventional inferred template, through the `MEM_*` macros of `src/common/rtl/pkg/macros.vh`: one array, one read and one write process per port, so nonblocking semantics give the read-first rule with no bypass.

The Intel backends instantiate `altsyncram` with unregistered outputs and `OLD_DATA` read-during-write: `SINGLE_PORT` for `n2a_sync_ram`, `DUAL_PORT` (port A writes, port B reads) for `n2a_sdp_ram_enabled`, and `BIDIR_DUAL_PORT` with `OLD_DATA` on both ports and across them for `n2a_tdp_ram`. That is the same one-edge, read-first behaviour as the portable file.

`n2a_sync_ram_enabled` and `n2a_tdp_ram_enabled` have one file each and no backend of their own: each instantiates its RAM (`u_ram`), so the source list's backend choice reaches it, and keeps per port a reset read-valid register and a hold register in front of the RAM's output (`read_data_o = read_valid_o ? word : held`). The RAM reads on every edge because an M9K in true-dual-port mode has no per-port read enable, so the enable cannot live in the block.

| Module | Portable file | Intel file |
|---|---|---|
| `n2a_sync_ram` | `n2a_sync_ram.sv` | `n2a_sync_ram_intel.sv` |
| `n2a_sdp_ram`, `n2a_sdp_ram_enabled` | `n2a_sdp_ram_portable.sv` | `n2a_sdp_ram_intel.sv` |
| `n2a_tdp_ram` | `n2a_tdp_ram_portable.sv` | `n2a_tdp_ram_intel.sv` |
| `n2a_sync_ram_enabled` | `n2a_sync_ram_enabled.sv`, over `n2a_sync_ram` | the same file |
| `n2a_tdp_ram_enabled` | `n2a_tdp_ram_enabled.sv`, over `n2a_tdp_ram` | the same file |

The two files of one module define the same module name, so each waives Verilator's `DECLFILENAME` for itself only. An Intel file never enters a Verilator product target; Verilator lints its wiring against the port stub `src/common/dv/memory/n2a_altsyncram_port_stub.sv`.

## Clocks and resets

One domain, `clk_i`, and no crossing. The RAMs have no reset; the request wrappers reset only their read-valid registers, per port, synchronously.

## Design choices

- One module name per contract with backends chosen by the source list, against a backend parameter in product RTL: the design never names a vendor, and a new backend is one file.
- Read-first collisions, because that is what the inferred template and `altsyncram` with `OLD_DATA` both give with no bypass logic.
- No write-through RAM. `n2a_sync_ram_wt` (new word on a collision) served only nand2mario's shared VRAM/WRAM store and its ASIC backend, which left with `n2m_dual_port_ram` in #206. No design in this repository instantiates it, and nand2doom's padless ASIC core contract ([HAS](../../../nand2doom/rtl/has.md#padless-asic-core)) keeps the collision semantics of its own memories, which are the read-first RAMs above, so it was deleted with its tests (#207). A future consumer that needs the new word adds the module back with its contract test and names itself as the reason.
- No reset or initialization: an M9K cannot reset its contents, and a reset that only simulation honours would hide a bug the board shows.

## Verification

The contract tests are the oracle, not either backend: `src/common/dv/memory/test_sync_ram.py`, `test_tdp_ram.py` and `test_ram_enabled.py`, registered in nand2doom's `targets.json` as the `common_sync_ram*`, `common_tdp_ram*` and `common_*_ram_enabled*` targets. They run the default and byte-plane geometries, the Intel backends through the port stub (`common_sync_ram_intel_stub`, `common_tdp_ram_intel_stub`, `common_sync_ram_enabled_intel_stub`, `common_tdp_ram_enabled_intel_stub`, flavor `intel`), and every illegal parameter as an expected elaboration failure. `test_ram_enabled.py` checks the request wrappers against a cycle model under seeded random requests on corner addresses, and fails unless it reached a request under reset, a same-port read and write, a hold, a mixed-port read and write and one port in reset alone. The model samples every read before that edge's writes land, so it pins the read-first rule of the request wrappers on the same port and across ports: a wrapper that returned the new word fails it. `common_tdp_ram_enabled_write_collision_fail` proves the double-write assertion fires. `n2a_sdp_ram` is checked through its consumers (the nand2doom fetch BTB and UART stream queue). On the board, the Quartus report gate checks that each array maps to M9K blocks in the expected mode and geometry ([DE10-Lite board spec](../../../nand2doom/fpga/de10_lite/board-spec.md#memory-implementation)); a new consumer adds its arrays to that gate.
