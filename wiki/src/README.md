# Blocks

Specs and docs for each project's blocks, mirroring the code: code lives under `src/<project>/<discipline>/<name>/` (discipline: `rtl`, `dv`, `fv`, `fpga`, `scripts`, `sw`) and its documents under `wiki/src/<project>/<discipline>/<name>/`: the design's HAS and each block's MAS, DV and FV plans, board specs, and the PRDs and specs of scripts and software ([standard documents](../bkm/design/standard-docs.md)). Code and documents change in the same pull request ([repository layout](../bkm/workflow/repo-layout.md)).

| Project | What it is |
|---|---|
| [common](common/README.md) | Shared blocks and scripts every project can reuse: UART, VGA, memory, synchronizers, the JTAG keyboard |
| [nand2mario](nand2mario/README.md) | A Game Boy-compatible system for the DE10-Lite and DE2-115 |
