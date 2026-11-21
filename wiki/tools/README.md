# Tools

The tools: code any project can use unchanged. Each has its code in `tools/<tool>/` and its requirements (`prd.md`) and spec (`spec.md`) here, in `wiki/tools/<tool>/`, mirroring the code ([repository layout](../bkm/workflow/repo-layout.md)). Code bound to one project is a script: it lives in that project's `scripts` discipline and is documented under [Blocks](../src/README.md).

| Tool | What it does |
|---|---|
| [fv](fv/README.md) | Runs every project's formal proofs and covers with SymbiYosys |
| [pages](pages/README.md) | Builds the wiki into the GitHub Pages site |
| [sim](sim/README.md) | Runs every project's simulation targets on Verilator and cocotb |
| [toolchain](toolchain/README.md) | Pins, installs and checks the host tools |
| [tracker](tracker/README.md) | Publishes status, regression and metric pages on the site |

Tools we use but do not build (Verilator, cocotb, the OSS CAD Suite with Yosys and SymbiYosys, Quartus Prime Lite) are chosen in ADR 0009 and installed by the toolchain tool, which pins their versions and checks each host.
