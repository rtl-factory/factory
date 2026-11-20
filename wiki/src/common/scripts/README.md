# common scripts

The `scripts` discipline of `common`: scripts bound to a `common` block, which any project using that block can run ([repository layout](../../../bkm/workflow/repo-layout.md)). Code: `src/common/scripts/`.

| Script | What it does |
|---|---|
| [jtag](jtag/README.md) | JTAG keyboard host: types keys into a board through the JTAG keyboard endpoint |
| [uart_mem](uart_mem/README.md) | UART memory host: loads a program image into a board and reads memory back through the UART memory endpoint |
