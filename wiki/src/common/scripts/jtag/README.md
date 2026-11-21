# JTAG keyboard host

Sends key presses from a host to a board over its USB-Blaster cable, through the `common` JTAG keyboard endpoint, so a design gets keyboard input without a PS/2 keyboard plugged in. Code: `src/common/scripts/jtag/`.

It is a script of `common`, not a tool: it is the host side of the JTAG keyboard block and changes with it ([repository layout](../../../../bkm/layout.md#repository-layout), Example).

- [Requirements](prd.md)
- [Spec](spec.md)
