`default_nettype none
`include "src/common/rtl/pkg/macros.vh"

// Game Boy memory responder for the compact CPU bus: decodes every request,
// serves VRAM, WRAM (with its echo), OAM, HRAM and the IE register from
// inferred synchronous stores, answers the unusable window with open-bus
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
  input  n2m_interfaces_pkg::bus_request_t     request_i,
  output n2m_interfaces_pkg::bus_response_t    response_o,
  // Peripheral port: the held request while it decodes to $FF00-$FF7F, and
  // the peripheral's response passed back to the CPU in the same cycle.
  output n2m_interfaces_pkg::bus_request_t     io_request_o,
  input  n2m_interfaces_pkg::bus_response_t    io_response_i,
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
  output n2m_interfaces_pkg::bus_request_t     cart_request_o,
  input  logic                                 cart_ready_i,
  input  n2m_interfaces_pkg::bus_response_t    cart_response_i
);
  import n2m_memory_pkg::*;

  // Acceptance-side decode of the live request (read sampling) and held-side
  // decode of the accepted copy (response, writes, peripheral forward).
  destination_t destination, held_destination;
  offset_t      offset, held_offset;
  n2m_interfaces_pkg::bus_request_t held_q;
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

  // Acceptance edge: the first edge with a valid request and no response
  // owed. Reads sample their store here. The request is held until the edge
  // where the CPU samples the response; a write is applied on that edge.
  assign accept = request_i.valid && !pending_q;
  assign pending_d = accept || (pending_q && !response_o.valid);
  `N2A_DFF_RST(pending_q, pending_d, clk_i, reset_i)
  `N2A_DFF_RST_EN(held_q, request_i, clk_i, accept, reset_i, '0)
  assign held_io = held_destination == DEST_IO;
  assign held_cart = (held_destination == DEST_ROM) || (held_destination == DEST_CART_RAM);
  assign cart_dest = (destination == DEST_ROM) || (destination == DEST_CART_RAM);
  assign write_data = held_q.write_data;

  // Cartridge port: the live request is forwarded from the acceptance cycle
  // (the requester holds it, so the held copy is identical) until the
  // cartridge samples it ready; cart_wait_q then covers the time until the
  // cartridge's response, which is sampled by the CPU on the same edge.
  assign cart_accept = cart_request_o.valid && cart_ready_i;
  assign cart_wait_d = cart_accept || (cart_wait_q && !cart_response_i.valid);
  `N2A_DFF_RST(cart_wait_q, cart_wait_d, clk_i, reset_i)

  // PPU blocking is decided once, on the acceptance edge, for the whole
  // request: the read is not issued and the response returns open bus, or
  // the write is dropped on the pending edge.
  assign blocked_d = ((destination == DEST_VRAM) && vram_blocked_i) ||
                     ((destination == DEST_OAM) && oam_blocked_i);
  `N2A_DFF_RST_EN(blocked_q, blocked_d, clk_i, accept, reset_i, 1'b0)

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
  // Store addresses: the live offset while reading, the held offset while
  // writing; the requester holds the address so both name the same byte.
  assign vram_offset = vram_write ? held_offset[12:0] : offset[12:0];
  assign wram_offset = wram_write ? held_offset[12:0] : offset[12:0];
  assign oam_offset = oam_write ? held_offset[7:0] : offset[7:0];
  assign hram_offset = hram_write ? held_offset[6:0] : offset[6:0];
  // The DMA write port shares OAM port A with the CPU; the DMA block keeps
  // CPU OAM accesses away while it transfers, so the port is never contended.
  assign oam_store_write = oam_write || dma_oam_write_i;
  assign oam_store_address = dma_oam_write_i ? dma_oam_address_i : oam_offset;
  assign oam_store_data = dma_oam_write_i ? dma_oam_data_i : write_data;

  // CPU-side stores use port A; the VRAM and OAM port Bs are the PPU read
  // ports and the other port Bs are reserved for later readers. Unused port outputs of
  // every store are left unconnected until a second reader attaches.
  /* verilator lint_off PINCONNECTEMPTY */
  n2m_dual_port_ram #(
    .DATA_WIDTH (8),
    .DEPTH      (VRAM_BYTES),
    .DUAL_CLOCK (1'b0)
  ) u_vram (
    .clk_a_i         (clk_i),
    .reset_a_i       (reset_i && !vram_clear_i),
    .read_a_i        (vram_read && !vram_clear_i),
    .write_a_i       (vram_write || vram_clear_i),
    .address_a_i     (vram_clear_i ? vram_clear_address_i : vram_offset),
    .write_data_a_i  (vram_clear_i ? 8'h00 : write_data),
    .byte_enable_a_i (1'b1),
    .read_data_a_o   (vram_data),
    .read_valid_a_o  (vram_valid),
    .clk_b_i         (clk_i),
    .reset_b_i       (reset_i),
    .read_b_i        (ppu_vram_read_i),
    .address_b_i     (ppu_vram_address_i),
    .read_data_b_o   (ppu_vram_data_o),
    .read_valid_b_o  (ppu_vram_valid_o)
  );

  n2m_dual_port_ram #(
    .DATA_WIDTH (8),
    .DEPTH      (WRAM_BYTES),
    .DUAL_CLOCK (1'b0)
  ) u_wram (
    .clk_a_i         (clk_i),
    .reset_a_i       (reset_i),
    .read_a_i        (wram_read),
    .write_a_i       (wram_write),
    .address_a_i     (wram_offset),
    .write_data_a_i  (write_data),
    .byte_enable_a_i (1'b1),
    .read_data_a_o   (wram_data),
    .read_valid_a_o  (wram_valid),
    .clk_b_i         (clk_i),
    .reset_b_i       (reset_i),
    .read_b_i        (1'b0),
    .address_b_i     ('0),
    .read_data_b_o   (),
    .read_valid_b_o  ()
  );

  n2m_dual_port_ram #(
    .DATA_WIDTH (8),
    .DEPTH      (OAM_STORE_DEPTH),
    .DUAL_CLOCK (1'b0)
  ) u_oam (
    .clk_a_i         (clk_i),
    .reset_a_i       (reset_i),
    .read_a_i        (oam_read),
    .write_a_i       (oam_store_write),
    .address_a_i     (oam_store_address),
    .write_data_a_i  (oam_store_data),
    .byte_enable_a_i (1'b1),
    .read_data_a_o   (oam_data),
    .read_valid_a_o  (oam_valid),
    .clk_b_i         (clk_i),
    .reset_b_i       (reset_i),
    .read_b_i        (ppu_oam_read_i),
    .address_b_i     (ppu_oam_address_i),
    .read_data_b_o   (ppu_oam_data_o),
    .read_valid_b_o  (ppu_oam_valid_o)
  );

  n2m_dual_port_ram #(
    .DATA_WIDTH (8),
    .DEPTH      (HRAM_STORE_DEPTH),
    .DUAL_CLOCK (1'b0)
  ) u_hram (
    .clk_a_i         (clk_i),
    .reset_a_i       (reset_i),
    .read_a_i        (hram_read),
    .write_a_i       (hram_write),
    .address_a_i     (hram_offset),
    .write_data_a_i  (write_data),
    .byte_enable_a_i (1'b1),
    .read_data_a_o   (hram_data),
    .read_valid_a_o  (hram_valid),
    .clk_b_i         (clk_i),
    .reset_b_i       (reset_i),
    .read_b_i        (1'b0),
    .address_b_i     ('0),
    .read_data_b_o   (),
    .read_valid_b_o  ()
  );
  /* verilator lint_on PINCONNECTEMPTY */

  // IE: all eight bits are stored and read back; the CPU consumes the five
  // interrupt sources. Reset clears it (DMG post-boot value).
  `N2A_DFF_RST_EN(ie_q, write_data, clk_i, ie_write, reset_i, 8'h00)
  assign interrupt_enable_o = ie_q[4:0];

  // Response: the held destination selects the store output captured on the
  // acceptance edge, the IE register, open bus, the peripheral's data or the
  // cartridge's data; a PPU-blocked VRAM/OAM access reads open bus.
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

  // Requester and peripheral protocol: known fields, a request held until
  // its response, and no peripheral response without a forwarded request.
  `N2A_ASSERT(request_known, clk_i, reset_i,
      !request_i.valid || !$isunknown(request_i))
  `N2A_ASSERT(request_held, clk_i, reset_i,
      (request_i.valid && !response_o.valid) |=> $stable(request_i))
  `N2A_ASSERT(request_matches_held, clk_i, reset_i,
      !pending_q || (request_i.valid && request_i == held_q))
  `N2A_ASSERT(io_response_requested, clk_i, reset_i,
      !io_response_i.valid || io_request_o.valid)
  `N2A_ASSERT(io_response_known, clk_i, reset_i,
      !io_request_o.valid || !$isunknown(io_response_i))
  // The cartridge never responds without an accepted request, its response
  // is known while one is outstanding, and it never faults.
  `N2A_ASSERT(cart_response_requested, clk_i, reset_i,
      !cart_response_i.valid || cart_wait_q)
  `N2A_ASSERT(cart_response_known, clk_i, reset_i,
      !cart_wait_q || !$isunknown(cart_response_i))
  `N2A_ASSERT_KNOWN(cart_ready_known, clk_i, reset_i, cart_ready_i)
  `N2A_ASSERT(cart_never_faults, clk_i, reset_i,
      !cart_response_i.valid || !cart_response_i.fault)
  // Every internal read response carries data captured by its own store.
  `N2A_ASSERT(vram_read_served, clk_i, reset_i,
      !(pending_q && !held_q.write && held_destination == DEST_VRAM && !blocked_q) || vram_valid)
  `N2A_ASSERT(wram_read_served, clk_i, reset_i,
      !(pending_q && !held_q.write && held_destination == DEST_WRAM) || wram_valid)
  `N2A_ASSERT(oam_read_served, clk_i, reset_i,
      !(pending_q && !held_q.write && held_destination == DEST_OAM && !blocked_q) || oam_valid)
  `N2A_ASSERT_KNOWN(ppu_port_known, clk_i, reset_i,
      {ppu_vram_read_i, ppu_oam_read_i, vram_blocked_i, oam_blocked_i})
  // The DMA port never meets a CPU access of the OAM store on one edge.
  `N2A_ASSERT(dma_oam_port_uncontended, clk_i, reset_i,
      !(dma_oam_write_i && (oam_read || oam_write)))
  `N2A_ASSERT(dma_oam_write_known, clk_i, reset_i,
      !dma_oam_write_i || !$isunknown({dma_oam_address_i, dma_oam_data_i}))
  `N2A_ASSERT(hram_read_served, clk_i, reset_i,
      !(pending_q && !held_q.write && held_destination == DEST_HRAM) || hram_valid)
endmodule
