// Exact board-top composition diagnostics of the common JTAG keyboard
// endpoint (wiki/src/nand2mario/rtl/has.md#verification-strategy). Listed by every target that
// elaborates n2m_de10_lite. Each endpoint reset synchronizer (system clock
// and TCK) asserts asynchronously and releases in its own domain: bit0
// shifts into bit1 and bit1 drives the local asynchronous reset with
// synchronous release; sys_reset also masks inject_valid. Verilator's
// public-depth view merges the packed stages into one signal and reports
// both uses. These waivers grant no timing exception: the SDC still times
// release and stage0-to-stage1.
`verilator_config
lint_off -rule SYNCASYNCNET -file "*src/common/rtl/sync/n2a_reset_sync.sv" -match "*'*u_jtag_keyboard.u_sys_reset.stages_q'"
lint_off -rule SYNCASYNCNET -file "*src/common/rtl/sync/n2a_reset_sync.sv" -match "*'*u_jtag_keyboard.u_scan_reset.stages_q'"
