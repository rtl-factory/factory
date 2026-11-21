`default_nettype none

// OAM DMA facts owned by the nand2mario DMA subsystem: the DMA register
// address, the transfer length, the source page aliasing and the CPU
// destinations that stay reachable during a transfer.
package n2m_dma_pkg;
    localparam logic [15:0] DMA_ADDRESS    = 16'hFF46;
    localparam int unsigned TRANSFER_BYTES = 160;  // Ref: Pan Docs "OAM DMA": 160 bytes, one per machine cycle.

    // The written value is the source page: $XX00-$XX9F. Pages $E0-$FF read
    // the WRAM echo, that is page $XX - $20 (DMG behavior for the external
    // bus; OAM, the I/O window and HRAM are never a source).
    function automatic logic [7:0] source_page(input logic [7:0] page);
        return (page >= 8'hE0) ? (page - 8'h20) : page;
    endfunction

    // While a transfer runs the CPU loses the bus the transfer reads from and
    // OAM (DMG: separate external and VRAM buses). A VRAM source ($80-$9F)
    // blocks VRAM $8000-$9FFF; any other source blocks the external bus
    // (ROM, cartridge RAM, WRAM and its echo, $0000-$7FFF and $A000-$FDFF).
    // OAM and the unusable range $FE00-$FEFF are always blocked; the I/O
    // window (so the DMA register itself), HRAM and IE stay reachable. A
    // blocked access reads open bus and drops its write.
    function automatic logic vram_page(input logic [7:0] page);
        return page[7:5] == 3'b100;
    endfunction

    function automatic logic restricted(input logic [15:0] address, input logic [7:0] page);
        logic vram;
        vram = address[15:13] == 3'b100;
        if (address[15:8] == 8'hFE) return 1'b1;
        if (address >= 16'hFF00) return 1'b0;
        return vram == vram_page(source_page(page));
    endfunction
endpackage
