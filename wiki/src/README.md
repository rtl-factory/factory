# Blocks

Specs and docs for each project's blocks, mirroring the code: a block's code lives under `src/<project>/<type>/<block>/` (type: `arch`, `rtl`, `dv`, `fv`, `fpga`) and its spec under `wiki/src/<project>/<type>/<block>/`. A project tool's code lives under `src/<project>/tools/<tool>/` and its PRD and spec under `wiki/src/<project>/tools/<tool>/`. Code and documents change in the same pull request ([repository layout](../bkm/workflow/repo-layout.md)).

| Project | What it is |
|---|---|
| [common](common/README.md) | Shared blocks and tools every project can reuse: UART, VGA, memory, synchronizers, the JTAG keyboard |
| [nand2mario](nand2mario/README.md) | A Game Boy-compatible system for the DE10-Lite and DE2-115 |
