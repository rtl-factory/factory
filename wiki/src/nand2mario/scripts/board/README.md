# Board flow

Everything a host does with a nand2mario board: build and gate the Quartus image, program it, load a cartridge over UART, read back the displayed frame, and press joypad keys over JTAG. Code: `src/nand2mario/scripts/board/` (`n2m_board.py` is the command; `n2m_quartus.py`, `cart_loader.py` and `frame_snapshot.py` are its parts).

- [Requirements](prd.md)
- [Spec](spec.md)
