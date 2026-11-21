// Intel Virtual JTAG: manual instance 0, two-bit instruction register.
// Board-only vendor wrapper (sld_virtual_jtag); simulation fixtures drive the
// neutral scan ports of n2a_jtag_keyboard directly. See wiki/src/common/jtag.
module n2a_jtag_virtual (
    output logic tck_o, tdi_o, capture_o, shift_o, update_o, update_ir_o,
    output logic [1:0] ir_o,
    input logic tdo_i
);
    sld_virtual_jtag #(.sld_auto_instance_index("NO"), .sld_instance_index(0),
        .sld_ir_width(2)) u_vjtag (
        .tck(tck_o), .tdi(tdi_o), .tdo(tdo_i), .ir_in(ir_o), .ir_out(2'b00),
        .virtual_state_cdr(capture_o), .virtual_state_sdr(shift_o),
        .virtual_state_udr(update_o), .virtual_state_uir(update_ir_o));
endmodule
