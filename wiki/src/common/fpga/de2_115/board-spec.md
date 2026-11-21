# DE2-115 board spec (common)

The parts of the DE2-115 board that every project shares. Each project's own board spec (its top level, pins, budget and build) links here for the board clock.

## Board

Terasic DE2-115, Intel Cyclone IV E EP4CE115F29C7. The common part is the 50 MHz reference oscillator on `CLOCK_50`.

## Top level

`n2a_de2_115_clock` is the board clock every project's DE2-115 board top instantiates as `u_clock`:

| Port | Direction | Meaning |
| --- | --- | --- |
| `reference_clk_i` | in | 50 MHz board reference (`CLOCK_50`) |
| `reset_i` | in | asynchronous reset of the PLL (`areset`) and the portable divider |
| `clk_25_o` | out | 25 MHz system clock |
| `locked_o` | out | high once the clock is stable; the board top holds the design in reset until then |

Two backend files define the module and are never compiled together; the board top never selects one:

| Backend | File | Behaviour |
| --- | --- | --- |
| Intel (Quartus source list) | `src/common/fpga/de2_115/n2a_de2_115_clock_intel.sv` | `altpll` `u_pll`: 20,000 ps reference, multiply 1, divide 2, 50% duty, zero phase, dynamic phase and scan ports unused; `locked_o` is the PLL lock |
| Portable (simulation targets) | `src/common/fpga/de2_115/n2a_de2_115_clock_portable.sv` | divides the reference by two and raises `locked_o` three reference edges after reset release |

## Pins and I/O

None here: pins live in each project's Quartus manifest (`src/<project>/cfg/`).

## Clocks and constraints

One clock: `clk_25_o`, generated from the reference by `u_pll`. Each project's SDC constrains it as `u_clock|u_pll|...|clk[0]`.

## Budget

None of its own: one PLL, reported in each project's board budget.

## Build and program

Built as part of each project's board image; see the project board specs.
