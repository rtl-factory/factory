# DE10-Lite board spec (common)

The parts of the DE10-Lite board that every project shares. Each project's own board spec (its top level, pins, budget and build) links here for the board clock.

## Board

Terasic DE10-Lite, Intel MAX 10 10M50DAF484C7G. The common part is the 50 MHz reference oscillator on `MAX10_CLK1_50`.

## Top level

`n2a_de10_lite_clock` is the board clock every project's DE10-Lite board top instantiates as `u_clock`:

| Port | Direction | Meaning |
| --- | --- | --- |
| `reference_clk_i` | in | 50 MHz board reference (`MAX10_CLK1_50`) |
| `reset_i` | in | asynchronous reset of the PLL (`areset`) and the portable divider |
| `clk_25_o` | out | 25 MHz system clock |
| `locked_o` | out | high once the clock is stable; the board top holds the design in reset until then |

Two backend files define the module and are never compiled together; the board top never selects one:

| Backend | File | Behaviour |
| --- | --- | --- |
| Intel (Quartus source list) | `src/common/fpga/de10_lite/n2a_de10_lite_clock_intel.sv` | `altpll` `u_pll`: 20,000 ps reference, multiply 1, divide 2, 50% duty, zero phase, dynamic phase and scan ports unused; `locked_o` is the PLL lock |
| Portable (simulation targets) | `src/common/fpga/de10_lite/n2a_de10_lite_clock_portable.sv` | divides the reference by two and raises `locked_o` three reference edges after reset release |

## Pins and I/O

`src/common/fpga/de10_lite/pins.json` holds every DE10-Lite pin a project uses, by its name in the board manual, with its I/O standard (or `null` where the project's settings fragment sets it), and the board's family, device, reference clock and clock module. A project's Quartus manifest (`src/<project>/cfg/`) names the pins its top uses and may wire a port to a pin of another name; the [Quartus tool](../../../../tools/quartus/spec.md#board-pin-maps) resolves them.

## Clocks and constraints

One clock: `clk_25_o`, generated from the reference by `u_pll`. Each project's SDC constrains it as `u_clock|u_pll|...|clk[0]`.

## Budget

None of its own: one PLL, reported in each project's board budget.

## Build and program

Built as part of each project's board image; see the project board specs.
