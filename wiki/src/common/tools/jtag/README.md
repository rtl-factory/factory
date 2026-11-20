# JTAG keyboard host

Sends key presses from a host to a board over its USB-Blaster cable, through the `common` JTAG keyboard endpoint, so a design gets keyboard input without a PS/2 keyboard plugged in. Code: `src/common/tools/jtag/`.

It is a project tool of `common`, not a repo tool: it is the host side of the JTAG keyboard block and changes with it ([repository layout](../../../../bkm/workflow/repo-layout.md), Example).

- [Requirements](prd.md)
- [Spec](spec.md)
