// Common SDRAM package: the x16 IS42S16320D mode register values and the
// byte-address maps the controller and the device model share
// (wiki/src/common/rtl/sdram/mas.md). No state, no timing.
package n2a_sdram_pkg;
    // Ref: IS42S16320D datasheet mode register: sequential, CAS latency two,
    // burst length in A[2:0].
    localparam logic [12:0] SDRAM_MODE_BL1_CL2 = 13'h020;
    localparam logic [12:0] SDRAM_MODE_BL8_CL2 = 13'h023;

    typedef enum logic {
        // bank [25:24], row [23:11], column [10:1].
        SDRAM_MAP_BANK_ROW_COLUMN,
        // row [25:13], bank [12:11] XOR [14:13], column [10:1].
        SDRAM_MAP_ROW_XOR_BANK
    } t_sdram_map;

    function automatic logic [12:0] sdram_mode(input int unsigned burst_beats);
        return burst_beats == 8 ? SDRAM_MODE_BL8_CL2 : SDRAM_MODE_BL1_CL2;
    endfunction

    /* verilator lint_off UNUSEDSIGNAL */
    function automatic logic [1:0] sdram_bank(input t_sdram_map map, input logic [25:0] address);
        return map == SDRAM_MAP_ROW_XOR_BANK ? address[12:11] ^ address[14:13] : address[25:24];
    endfunction

    function automatic logic [12:0] sdram_row(input t_sdram_map map, input logic [25:0] address);
        return map == SDRAM_MAP_ROW_XOR_BANK ? address[25:13] : address[23:11];
    endfunction

    function automatic logic [9:0] sdram_column(input logic [25:0] address);
        return address[10:1];
    endfunction

    // The device word {bank, row, column} that holds a byte address; bit 0 is
    // the byte lane.
    function automatic logic [24:0] sdram_word(input t_sdram_map map, input logic [25:0] address);
        return {sdram_bank(map, address), sdram_row(map, address), sdram_column(address)};
    endfunction
    /* verilator lint_on UNUSEDSIGNAL */
endpackage
