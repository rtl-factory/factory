// Exact n2d_system composition diagnostics of the common JTAG keyboard
// endpoint (wiki/src/nand2doom/dv/io/plan.md#jtag-keyboard-input). Listed by every
// target that elaborates n2d_system; never by the standalone input fixture.
// Each endpoint reset synchronizer (system clock and TCK) asserts
// asynchronously and releases in its own domain: bit0 shifts into bit1 and
// bit1 drives the local asynchronous reset with synchronous release;
// sys_reset also masks inject_valid. Verilator's public-depth view merges the
// packed stages into one signal and reports both uses. These waivers grant no
// timing exception: the SDC still times release and stage0-to-stage1.
`verilator_config
lint_off -rule SYNCASYNCNET -file "*src/common/rtl/sync/n2a_reset_sync.sv" -match "*'*u_jtag_keyboard.u_sys_reset.stages_q'"
lint_off -rule SYNCASYNCNET -file "*src/common/rtl/sync/n2a_reset_sync.sv" -match "*'*u_jtag_keyboard.u_scan_reset.stages_q'"
// Board tops elaborate the common n2a_jtag_virtual on the Verilator stand-in
// of the vendor primitive, which keeps the vendor module name in a stub-named
// file (wiki/src/common/jtag/HAS.md).
lint_off -rule DECLFILENAME -file "*src/common/dv/jtag/sld_virtual_jtag_stub.sv" -match "*'sld_virtual_jtag'*"
