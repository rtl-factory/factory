# nand2doom scripts

The `scripts` discipline of nand2doom: host scripts bound to this project ([repository layout](../../../bkm/workflow/repo-layout.md)). Code: `src/nand2doom/scripts/`, tests in `src/nand2doom/scripts/tests/`. They came over flat from nand2anything's `tools/`, as nand2mario's did; their PRDs, the specs not written yet, and one folder per script are tracked in a follow-up issue.

| Script | What it does | Spec |
|---|---|---|
| `quartus.py` | Builds a board (`--board de10_lite` or `de2_115`) with Quartus and gates the fit, timing and reviewed warnings | in the [FPGA spec](../fpga/de10_lite/board-spec.md) |
| `n2d_image.py` | Builds the freestanding RV32IM validation image | in the [runtime spec](../sw/runtime/spec.md) |
| `n2d_doom.py` | Fetches the pinned inputs and builds the Doom image | in the [Doom port spec](../sw/doom/spec.md) |
| `n2d_uart.py`, `n2d_uart_debug_host.py` | UART host: debug commands, manifest loading, hardware-free planning | [n2d_uart](n2d_uart/spec.md) |
| `n2d_play.py` | Plays Doom on a board with one command | [n2d_play](n2d_play/spec.md) |
| `n2d_control.py`, `n2d_control_terminal.py` | Window and terminal controls for Doom over UART or JTAG | [n2d_control](n2d_control/spec.md) |
| `n2d_jtag_keyboard.py`, `n2d_jtag_device.py`, `n2d_jtag_devices.tcl` | Doom keys over the JTAG keyboard; JTAG device resolution | [n2d_jtag_keyboard](n2d_jtag_keyboard/spec.md) |
| `n2d_capture.py` | One bounded coherent frame snapshot | [n2d_capture](n2d_capture/spec.md) |
| `n2d_frame_decode.py` | Offline framebuffer decoder | [n2d_frame_decode](n2d_frame_decode/spec.md) |
| `n2d_perf.py`, `n2d_sw_profile.py`, `n2d_render_projection.py` | Performance harness runs, ELF-symbol profiles, render oracle checks | not yet |
| `n2d_timing.py` | Compiles a board at another clock and tabulates its timing (analysis only) | not yet |
