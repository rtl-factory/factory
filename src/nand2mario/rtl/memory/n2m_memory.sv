`default_nettype none
`include "src/common/rtl/pkg/macros.vh"

// Game Boy memory responder for the compact CPU bus: decodes every request,
// serves VRAM, WRAM (with its echo), OAM, HRAM and the IE register from
// the common synchronous RAMs, answers the unusable window with open-bus
// $FF, forwards $FF00-$FF7F to the peripheral port and ROM/cartridge RAM
// ($0000-$7FFF, $A000-$BFFF) to the cartridge port. A request is accepted on
// the first edge it is sampled valid and copied into a held register, so no
// response field depends combinationally on the live CPU request. Internal
// destinations respond on the edge after acceptance; the peripheral port
// presents the held request from that cycle on and passes the peripheral's
// response through unchanged; the cartridge port presents the live request
// on the acceptance cycle, holds it until the cartridge is ready and passes
// the cartridge's (registered) response through, so a stalling backing only
// lengthens that request. The PPU reads VRAM and OAM through the stores'
// second ports, and its blocking levels turn CPU VRAM/OAM accesses into
// open-bus reads and dropped writes while the PPU owns those stores. The DMA
// block writes OAM through its own port; it owns the CPU bus in front of
// this responder while a transfer runs, so its writes never meet a CPU OAM
// access.
module n2m_memory (
    input  logic                                 clk_i,
    input  logic                                 reset_i,
    // CPU bus (requester holds the request until the response is sampled).
    input  n2m_interfaces_pkg::t_bus_request     request_i,
    output n2m_interfaces_pkg::t_bus_response    response_o,
    // Peripheral port: the held request while it decodes to $FF00-$FF7F, and
    // the peripheral's response passed back to the CPU in the same cycle.
    output n2m_interfaces_pkg::t_bus_request     io_request_o,
    input  n2m_interfaces_pkg::t_bus_response    io_response_i,
    // IE register bits consumed by the CPU interrupt logic.
    output logic [4:0]                           interrupt_enable_o,
    // PPU read port into the VRAM store: sampled every edge, data valid the
    // next cycle (store port B contract).
    input  logic                                 ppu_vram_read_i,
    input  logic [12:0]                          ppu_vram_address_i,
    output logic [7:0]                           ppu_vram_data_o,
    output logic                                 ppu_vram_valid_o,
    // PPU read port into the OAM store, same contract (store port B).
    input  logic                                 ppu_oam_read_i,
    input  logic [7:0]                           ppu_oam_address_i,
    output logic [7:0]                           ppu_oam_data_o,
    output logic                                 ppu_oam_valid_o,
    // DMA write port into the OAM store: one byte per asserted edge.
    // Boot VRAM clear: while asserted (system reset held) port A writes $00
    // at the given offset, as the DMG boot ROM leaves VRAM zeroed.
    input  logic                                 vram_clear_i,
    input  logic [12:0]                          vram_clear_address_i,
    input  logic                                 dma_oam_write_i,
    input  logic [7:0]                           dma_oam_address_i,
    input  logic [7:0]                           dma_oam_data_i,
    // PPU access blocking levels, sampled with the CPU request on its
    // acceptance edge: a blocked read returns $FF and a blocked write is dropped.
    input  logic                                 vram_blocked_i,
    input  logic                                 oam_blocked_i,
    // Cartridge port: the live request while it decodes to ROM or cartridge
    // RAM, held until the cartridge samples it ready; the cartridge's response
    // completes the CPU response in the same cycle.
    output n2m_interfaces_pkg::t_bus_request     cart_request_o,
    input  logic                                 cart_ready_i,
    input  n2m_interfaces_pkg::t_bus_response    cart_response_i
);
    import n2m_memory_pkg::*;

    // Why: reads must sample their store on the acceptance edge, before the held
    // copy exists, so the live request is decoded too; the held copy drives the
    // response, writes and the peripheral forward.
    t_destination destination, held_destination;
    t_offset      offset, held_offset;
    n2m_interfaces_pkg::t_bus_request held_q;
    logic         held_io, held_cart, cart_dest;
    logic [7:0]   write_data;
    logic         accept, pending_q, pending_d;
    logic         blocked_d, blocked_q;
    logic         cart_accept, cart_wait_d, cart_wait_q;
    logic         store_read, store_write;
    logic         vram_read, wram_read, oam_read, hram_read;
    logic         vram_write, wram_write, oam_write, hram_write, ie_write;
    logic [12:0]  vram_offset, wram_offset;
    logic [7:0]   oam_offset, oam_store_address, oam_store_data;
    logic         oam_store_write;
    logic [6:0]   hram_offset;
    logic [7:0]   vram_data, wram_data, oam_data, hram_data, read_data;
    logic         vram_valid, wram_valid, oam_valid, hram_valid;
    logic [7:0]   ie_q;

    n2m_memory_decode u_decode_live (
        .address_i     (request_i.address),
        .destination_o (destination),
        .offset_o      (offset)
    );
    n2m_memory_decode u_decode_held (
        .address_i     (held_q.address),
        .destination_o (held_destination),
        .offset_o      (held_offset)
    );

    // Why: one request is served at a time, so acceptance waits until no
    // response is owed. Reads sample their store on the acceptance edge; a write
    // is applied on the edge where the CPU samples the response, so it lands
    // exactly once.
    assign accept = request_i.valid && !pending_q;
    assign pending_d = accept || (pending_q && !response_o.valid);
    `DFF_RST(pending_q, pending_d, clk_i, reset_i)
    `DFF_RST_EN(held_q, request_i, clk_i, accept, reset_i, '0)
    assign held_io = held_destination == DEST_IO;
    assign held_cart = (held_destination == DEST_ROM) || (held_destination == DEST_CART_RAM);
    assign cart_dest = (destination == DEST_ROM) || (destination == DEST_CART_RAM);
    assign write_data = held_q.write_data;

    // Why: forwarding the live request from the acceptance cycle saves a cycle
    // and is safe because the requester holds it, so the held copy is identical.
    // cart_wait_q covers the time until the cartridge's response, which the CPU
    // samples on the same edge.
    assign cart_accept = cart_request_o.valid && cart_ready_i;
    assign cart_wait_d = cart_accept || (cart_wait_q && !cart_response_i.valid);
    `DFF_RST(cart_wait_q, cart_wait_d, clk_i, reset_i)

    // Why: blocking is decided once, on the acceptance edge, so a PPU mode change
    // mid-request cannot split it: the read is not issued and the response
    // returns open bus, or the write is dropped on the pending edge.
    assign blocked_d = ((destination == DEST_VRAM) && vram_blocked_i) ||
                       ((destination == DEST_OAM) && oam_blocked_i);
    `DFF_RST_EN(blocked_q, blocked_d, clk_i, accept, reset_i, 1'b0)

    assign store_read = accept && !request_i.write && !blocked_d;
    assign store_write = pending_q && held_q.write && !held_io && !held_cart && !blocked_q;
    assign vram_read = store_read && destination == DEST_VRAM;
    assign wram_read = store_read && destination == DEST_WRAM;
    assign oam_read = store_read && destination == DEST_OAM;
    assign hram_read = store_read && destination == DEST_HRAM;
    assign vram_write = store_write && held_destination == DEST_VRAM;
    assign wram_write = store_write && held_destination == DEST_WRAM;
    assign oam_write = store_write && held_destination == DEST_OAM;
    assign hram_write = store_write && held_destination == DEST_HRAM;
    assign ie_write = store_write && held_destination == DEST_IE;
    // Why: the requester holds the address, so the live offset (reading) and
    // the held offset (writing) name the same byte.
    assign vram_offset = vram_write ? held_offset[12:0] : offset[12:0];
    assign wram_offset = wram_write ? held_offset[12:0] : offset[12:0];
    assign oam_offset = oam_write ? held_offset[7:0] : offset[7:0];
    assign hram_offset = hram_write ? held_offset[6:0] : offset[6:0];
    // Why: the DMA write port shares OAM port A with the CPU; the DMA block keeps
    // CPU OAM accesses away while it transfers, so the port is never contended.
    assign oam_store_write = oam_write || dma_oam_write_i;
    assign oam_store_address = dma_oam_write_i ? dma_oam_address_i : oam_offset;
    assign oam_store_data = dma_oam_write_i ? dma_oam_data_i : write_data;

    // Why: VRAM and OAM are true-dual-port stores: port A is the CPU side and
    // port B the PPU's read port. WRAM and HRAM have one owner, so they are
    // single-port stores. The VRAM boot clear writes through port A while
    // the system reset holds, so it releases that port's reset.
    n2a_tdp_ram_enabled #(
        .DATA_WIDTH (8),
        .DEPTH      (VRAM_BYTES)
    ) u_vram (
        .clk_i            (clk_i),
        .a_reset_i        (reset_i && !vram_clear_i),
        .a_read_enable_i  (vram_read && !vram_clear_i),
        .a_write_enable_i (vram_write || vram_clear_i),
        .a_address_i      (vram_clear_i ? vram_clear_address_i : vram_offset),
        .a_write_data_i   (vram_clear_i ? 8'h00 : write_data),
        .a_read_data_o    (vram_data),
        .a_read_valid_o   (vram_valid),
        .b_reset_i        (reset_i),
        .b_read_enable_i  (ppu_vram_read_i),
        .b_write_enable_i (1'b0),
        .b_address_i      (ppu_vram_address_i),
        .b_write_data_i   (8'h00),
        .b_read_data_o    (ppu_vram_data_o),
        .b_read_valid_o   (ppu_vram_valid_o)
    );

    n2a_sync_ram_enabled #(
        .DATA_WIDTH (8),
        .DEPTH      (WRAM_BYTES)
    ) u_wram (
        .clk_i          (clk_i),
        .reset_i        (reset_i),
        .read_enable_i  (wram_read),
        .write_enable_i (wram_write),
        .address_i      (wram_offset),
        .write_data_i   (write_data),
        .read_data_o    (wram_data),
        .read_valid_o   (wram_valid)
    );

    n2a_tdp_ram_enabled #(
        .DATA_WIDTH (8),
        .DEPTH      (OAM_STORE_DEPTH)
    ) u_oam (
        .clk_i            (clk_i),
        .a_reset_i        (reset_i),
        .a_read_enable_i  (oam_read),
        .a_write_enable_i (oam_store_write),
        .a_address_i      (oam_store_address),
        .a_write_data_i   (oam_store_data),
        .a_read_data_o    (oam_data),
        .a_read_valid_o   (oam_valid),
        .b_reset_i        (reset_i),
        .b_read_enable_i  (ppu_oam_read_i),
        .b_write_enable_i (1'b0),
        .b_address_i      (ppu_oam_address_i),
        .b_write_data_i   (8'h00),
        .b_read_data_o    (ppu_oam_data_o),
        .b_read_valid_o   (ppu_oam_valid_o)
    );

    n2a_sync_ram_enabled #(
        .DATA_WIDTH (8),
        .DEPTH      (HRAM_STORE_DEPTH)
    ) u_hram (
        .clk_i          (clk_i),
        .reset_i        (reset_i),
        .read_enable_i  (hram_read),
        .write_enable_i (hram_write),
        .address_i      (hram_offset),
        .write_data_i   (write_data),
        .read_data_o    (hram_data),
        .read_valid_o   (hram_valid)
    );

    // Ref: Pan Docs "IE ($FFFF)": all eight bits read back; the CPU consumes the
    // five interrupt sources. Reset clears it (DMG post-boot value).
    `DFF_RST_EN(ie_q, write_data, clk_i, ie_write, reset_i, 8'h00)
    assign interrupt_enable_o = ie_q[4:0];

    // Why: a PPU-blocked VRAM/OAM access reads open bus, as on the DMG.
    always_comb begin
        read_data = OPEN_BUS_DATA;
        case (held_destination)
            DEST_ROM, DEST_CART_RAM: read_data = cart_response_i.read_data;
            DEST_VRAM: read_data = vram_data;
            DEST_WRAM: read_data = wram_data;
            DEST_OAM:  read_data = oam_data;
            DEST_HRAM: read_data = hram_data;
            DEST_IE:   read_data = ie_q;
            DEST_IO:   read_data = io_response_i.read_data;
            default:   read_data = OPEN_BUS_DATA;
        endcase
        if (blocked_q) begin
            read_data = OPEN_BUS_DATA;
        end
        response_o.valid = pending_q && (held_io ? io_response_i.valid :
                                         held_cart ? cart_response_i.valid : 1'b1);
        response_o.fault = pending_q && held_io && io_response_i.fault;
        response_o.read_data = read_data;
        io_request_o = held_q;
        io_request_o.valid = pending_q && held_io;
        cart_request_o = request_i;
        cart_request_o.valid = request_i.valid && cart_dest && !cart_wait_q;
    end

    `ASSERT(request_known, clk_i, reset_i,
        !request_i.valid || !$isunknown(request_i))
    `ASSERT(request_held, clk_i, reset_i,
        (request_i.valid && !response_o.valid) |=> $stable(request_i))
    `ASSERT(request_matches_held, clk_i, reset_i,
        !pending_q || (request_i.valid && request_i == held_q))
    `ASSERT(io_response_requested, clk_i, reset_i,
        !io_response_i.valid || io_request_o.valid)
    `ASSERT(io_response_known, clk_i, reset_i,
        !io_request_o.valid || !$isunknown(io_response_i))
    `ASSERT(cart_response_requested, clk_i, reset_i,
        !cart_response_i.valid || cart_wait_q)
    `ASSERT(cart_response_known, clk_i, reset_i,
        !cart_wait_q || !$isunknown(cart_response_i))
    `ASSERT_KNOWN(cart_ready_known, clk_i, reset_i, cart_ready_i)
    `ASSERT(cart_never_faults, clk_i, reset_i,
        !cart_response_i.valid || !cart_response_i.fault)
    `ASSERT(vram_read_served, clk_i, reset_i,
        !(pending_q && !held_q.write && held_destination == DEST_VRAM && !blocked_q) || vram_valid)
    `ASSERT(wram_read_served, clk_i, reset_i,
        !(pending_q && !held_q.write && held_destination == DEST_WRAM) || wram_valid)
    `ASSERT(oam_read_served, clk_i, reset_i,
        !(pending_q && !held_q.write && held_destination == DEST_OAM && !blocked_q) || oam_valid)
    `ASSERT_KNOWN(ppu_port_known, clk_i, reset_i,
        {ppu_vram_read_i, ppu_oam_read_i, vram_blocked_i, oam_blocked_i})
    `ASSERT(dma_oam_port_uncontended, clk_i, reset_i,
        !(dma_oam_write_i && (oam_read || oam_write)))
    `ASSERT(dma_oam_write_known, clk_i, reset_i,
        !dma_oam_write_i || !$isunknown({dma_oam_address_i, dma_oam_data_i}))
    `ASSERT(hram_read_served, clk_i, reset_i,
        !(pending_q && !held_q.write && held_destination == DEST_HRAM) || hram_valid)
endmodule
