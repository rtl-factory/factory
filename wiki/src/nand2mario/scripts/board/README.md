# Board flow

Everything a host does with a nand2mario board: build and gate the Quartus image, program it, load a cartridge over UART, read back the displayed frame, and press joypad keys over JTAG. Code: `src/nand2mario/scripts/board/` (`n2m_board.py` is the command; `cart_loader.py` and `frame_snapshot.py` are its parts; the image is built and gated by the shared [Quartus tool](../../../../tools/quartus/README.md)).

- [Requirements](prd.md)
- [Spec](spec.md)
