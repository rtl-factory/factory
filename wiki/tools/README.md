# Tools

The repo tools: tools any project can use unchanged. Each has its code in `tools/<tool>/` and its requirements (`prd.md`) and spec (`spec.md`) here, in `wiki/tools/<tool>/`, mirroring the code ([repository layout](../bkm/workflow/repo-layout.md)). Tools bound to one project live with that project and are documented under [Blocks](../src/README.md).

| Tool | What it does |
|---|---|
| [pages](pages/README.md) | Builds the wiki into the GitHub Pages site |
| [sim](sim/README.md) | Runs every project's simulation targets on Verilator and cocotb |
| [toolchain](toolchain/README.md) | Pins, installs and checks the host tools |
| [tracker](tracker/README.md) | Publishes status, regression and metric pages on the site |

Tools we use but do not build (Verilator, cocotb, SymbiYosys, Quartus Prime Lite) are chosen in ADR 0009 and installed by the toolchain tool, which pins their versions and checks each host.
