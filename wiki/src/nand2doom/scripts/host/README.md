# Host

Everything a host does with a running nand2doom board over its UART and JTAG links: debug commands and image loading, one-command play on the DE2-115 or the DE10-Lite, real-time controls, Doom keys over the JTAG keyboard, coherent frame capture and the offline frame decoder. Code: `src/nand2doom/scripts/host/` (`n2d_uart.py`, `n2d_play.py`, `n2d_control.py`, `n2d_jtag_keyboard.py`, `n2d_capture.py` and `n2d_frame_decode.py` are the commands; `n2d_uart_debug_host.py` and `n2d_control_terminal.py` are their parts; the JTAG chain helpers are the [board console](../../../../tools/board/README.md)'s).

- [Requirements](prd.md)
- [Spec](spec.md)
