`default_nettype none
`include "src/common/rtl/pkg/macros.vh"

// Combinational Game Boy address decode: one destination and a store
// offset for every 16-bit CPU address. Echo RAM selects the WRAM store
// through the same offset bits as its base address, so aliasing is a decode
// fact rather than a copy. Service and read values belong to n2m_memory.
module n2m_memory_decode (
    input  n2m_interfaces_pkg::t_address  address_i,
    output n2m_memory_pkg::t_destination  destination_o,
    output n2m_memory_pkg::t_offset       offset_o
);
    import n2m_memory_pkg::*;

    // Why: the decode below relies on the map covering the whole 16-bit space
    // without gaps or overlaps, each backed region matching its store geometry.
    `ASSERT_PARAM(g_map_not_contiguous,
        (ROM_START == 16'h0000) && (VRAM_START == ROM_END + 16'd1) &&
        (CART_RAM_START == VRAM_END + 16'd1) && (WRAM_START == CART_RAM_END + 16'd1) &&
        (ECHO_START == WRAM_END + 16'd1) && (OAM_START == ECHO_END + 16'd1) &&
        (UNUSABLE_START == OAM_END + 16'd1) && (IO_START == UNUSABLE_END + 16'd1) &&
        (HRAM_START == IO_END + 16'd1) && (IE_ADDRESS == HRAM_END + 16'd1) &&
        (IE_ADDRESS == 16'hFFFF),
        "n2m_memory_pkg regions must tile the 16-bit address space")
    `ASSERT_PARAM(g_store_geometry,
        (ROM_BYTES == 32'(ROM_END) - 32'(ROM_START) + 1) &&
        (VRAM_BYTES == 32'(VRAM_END) - 32'(VRAM_START) + 1) &&
        (WRAM_BYTES == 32'(WRAM_END) - 32'(WRAM_START) + 1) &&
        (WRAM_BYTES > 32'(ECHO_END) - 32'(ECHO_START) + 1) &&
        (OAM_BYTES == 32'(OAM_END) - 32'(OAM_START) + 1) &&
        (OAM_STORE_DEPTH >= OAM_BYTES) &&
        (HRAM_BYTES == 32'(HRAM_END) - 32'(HRAM_START) + 1) &&
        (HRAM_STORE_DEPTH >= HRAM_BYTES) &&
        ((1 << OFFSET_WIDTH) == ROM_BYTES),
        "n2m_memory_pkg store geometry must match its regions")

    always_comb begin
        destination_o = DEST_UNUSABLE;
        offset_o = '0;
        if (address_i <= ROM_END) begin
            destination_o = DEST_ROM;
            offset_o = address_i[14:0];
        end else if (address_i <= VRAM_END) begin
            destination_o = DEST_VRAM;
            offset_o = {2'b00, address_i[12:0]};
        end else if (address_i <= CART_RAM_END) begin
            destination_o = DEST_CART_RAM;
        end else if (address_i <= ECHO_END) begin
            // Ref: Pan Docs memory map: echo RAM $E000-$FDFF mirrors $C000-$DDFF,
            // so both share address bits [12:0].
            destination_o = DEST_WRAM;
            offset_o = {2'b00, address_i[12:0]};
        end else if (address_i <= OAM_END) begin
            destination_o = DEST_OAM;
            offset_o = {7'b0, address_i[7:0]};
        end else if (address_i <= UNUSABLE_END) begin
            destination_o = DEST_UNUSABLE;
        end else if (address_i <= IO_END) begin
            destination_o = DEST_IO;
            offset_o = {8'b0, address_i[6:0]};
        end else if (address_i <= HRAM_END) begin
            destination_o = DEST_HRAM;
            offset_o = {8'b0, address_i[6:0]};
        end else begin
            destination_o = DEST_IE;
        end
    end
endmodule
