# UART memory host

Loads a program image into a board's memory and reads memory back over the board's UART, through the `common` UART memory endpoint (`n2a_uart_mem`). Code: `src/common/scripts/uart_mem/`.

It is a script of `common`, not a tool: it is the host side of the UART memory block and speaks that block's own framing, so it changes with it ([repository layout](../../../../bkm/layout.md#repository-layout)). Ported from nand2anything `tools/bench/uart_mem.py` (issue #78); nand2doom's board tests are its first user, and nand2mario's board link serves it too.

- [Requirements](prd.md)
- [Spec](spec.md)
