`default_nettype none
`include "src/common/rtl/pkg/macros.vh"

// OAM DMA: the $FF46 register, the transfer engine and the CPU bus gate.
// The block sits between the CPU and the memory map as the second bus
// master. A write to $FF46 stores the source page; at the first machine
// tick after the write the transfer is armed and at the next tick it
// starts: for 160 machine cycles one byte per cycle is read from
// $XX00 + offset through the memory map (the ordinary CPU path, so the
// memory's own rules such as PPU VRAM blocking apply to the read) and
// written into OAM through the dedicated DMA write port. If the backing
// has not answered a byte by the next tick (SDRAM shared with a load), the
// offset holds and the transfer is one machine cycle longer. A second write
// before the transfer starts restarts it after the same delay; the register
// reads back the last written page. While the transfer runs (active_o) a
// CPU request to OAM or to the bus the transfer reads from (VRAM for a
// $80-$9F source, else the external bus; n2m_dma_pkg::restricted) is
// answered by this block with open bus $FF (writes dropped) one edge after
// its acceptance, exactly like a blocked memory access; every other request
// (the other bus, I/O, HRAM, IE) is forwarded unchanged. The CPU and the engine never hold the memory at the
// same time: an engine read waits for the CPU's outstanding response, and a
// CPU request waits for the engine's.
module n2m_dma (
  input  logic                                clk_i,
  input  logic                                reset_i,
  input  logic                                machine_cycle_enable_i,
  // DMA register on the peripheral port.
  input  n2m_interfaces_pkg::bus_request_t    io_request_i,
  output n2m_interfaces_pkg::bus_response_t   io_response_o,
  output logic                                io_selected_o,
  // CPU side: the CPU's requests and the responses it samples.
  input  n2m_interfaces_pkg::bus_request_t    cpu_request_i,
  output n2m_interfaces_pkg::bus_response_t   cpu_response_o,
  // Memory side: the request the memory map serves and its response.
  output n2m_interfaces_pkg::bus_request_t    mem_request_o,
  input  n2m_interfaces_pkg::bus_response_t   mem_response_i,
  // OAM write port of the memory map.
  output logic                                oam_write_o,
  output logic [7:0]                          oam_address_o,
  output logic [7:0]                          oam_data_o,
  // Transfer in progress (the CPU restriction level).
  output logic                                active_o
);
  import n2m_dma_pkg::*;
  import n2m_interfaces_pkg::*;

  logic        io_write, tick;
  logic [7:0]  page_q;
  logic        pending_q, pending_d, armed_q, armed_d;
  logic        active_q, active_d;
  logic [7:0]  offset_q, offset_d;
  logic        last_byte;
  logic        need_q, need_d, busy_q, busy_d, engine_start, engine_present;
  logic [15:0] source_address;
  logic        cpu_restricted, cpu_accept, cpu_block_now, cpu_forward;
  logic        cpu_busy_q, cpu_busy_d, blocked_q, blocked_d;

  assign io_selected_o = io_request_i.address == DMA_ADDRESS;
  assign io_write = io_request_i.valid && io_request_i.write && io_selected_o;
  `N2A_DFF_RST_EN(page_q, io_request_i.write_data, clk_i, io_write, reset_i, 8'h00)
  always_comb begin
    io_response_o.valid = io_request_i.valid && io_selected_o;
    io_response_o.fault = 1'b0;
    io_response_o.read_data = page_q;
  end

  assign tick = machine_cycle_enable_i;
  assign last_byte = offset_q == 8'(TRANSFER_BYTES - 1);
  always_comb begin
    pending_d = pending_q;
    armed_d = armed_q;
    active_d = active_q;
    offset_d = offset_q;
    need_d = need_q;
    if (tick) begin
      pending_d = 1'b0;
      // Why: a (re)start waits for a byte still owed by the running transfer, so
      // that byte's OAM write is never lost.
      armed_d = pending_q || (armed_q && (need_q || busy_q));
      if (armed_q && !need_q && !busy_q) begin
        active_d = 1'b1;
        offset_d = 8'd0;
        need_d = 1'b1;
      end else if (active_q && !need_q && !busy_q) begin
        // Why: a byte still owed at a tick (a slow backing such as SDRAM busy with
        // a load) holds the offset; the transfer stretches by that cycle.
        if (last_byte) begin
          active_d = 1'b0;
        end else begin
          offset_d = offset_q + 8'd1;
          need_d = 1'b1;
        end
      end
    end
    if (io_write) pending_d = 1'b1;
    if (engine_start) need_d = 1'b0;
  end
  `N2A_DFF_RST(pending_q, pending_d, clk_i, reset_i)
  `N2A_DFF_RST(armed_q, armed_d, clk_i, reset_i)
  `N2A_DFF_RST(active_q, active_d, clk_i, reset_i)
  `N2A_DFF_RST(offset_q, offset_d, clk_i, reset_i)
  `N2A_DFF_RST(need_q, need_d, clk_i, reset_i)
  assign active_o = active_q;

  // Why: the memory serves one master at a time, so the engine takes it only
  // when a byte is due and no CPU request is outstanding; it holds its read
  // until the response, which is written into OAM on the same edge.
  assign engine_start = need_q && !cpu_busy_q;
  assign busy_d = engine_start || (busy_q && !mem_response_i.valid);
  `N2A_DFF_RST(busy_q, busy_d, clk_i, reset_i)
  assign engine_present = engine_start || busy_q;
  assign source_address = {source_page(page_q), offset_q};
  assign oam_write_o = busy_q && mem_response_i.valid;
  assign oam_address_o = offset_q;
  assign oam_data_o = mem_response_i.read_data;

  // Why: one master holds the memory at a time, so a CPU request is accepted
  // only with no CPU response owed and the engine not holding the memory. A
  // restricted request during a transfer is answered here one edge later, like
  // a blocked memory access; any other is forwarded until its response.
  assign cpu_restricted = restricted(cpu_request_i.address, page_q);
  assign cpu_accept = cpu_request_i.valid && !cpu_busy_q && !engine_present;
  assign cpu_block_now = cpu_accept && active_q && cpu_restricted;
  assign cpu_forward = cpu_request_i.valid && !engine_present && !blocked_q &&
                       !(active_q && cpu_restricted && !cpu_busy_q);
  assign cpu_busy_d = cpu_accept || (cpu_busy_q && !cpu_response_o.valid);
  assign blocked_d = cpu_block_now;
  `N2A_DFF_RST(cpu_busy_q, cpu_busy_d, clk_i, reset_i)
  `N2A_DFF_RST(blocked_q, blocked_d, clk_i, reset_i)

  always_comb begin
    if (engine_present) begin
      mem_request_o.valid = 1'b1;
      mem_request_o.write = 1'b0;
      mem_request_o.kind = ACCESS_DATA;
      mem_request_o.address = source_address;
      mem_request_o.write_data = 8'h00;
    end else begin
      mem_request_o = cpu_request_i;
      mem_request_o.valid = cpu_forward;
    end
    if (blocked_q) begin
      cpu_response_o.valid = 1'b1;
      cpu_response_o.fault = 1'b0;
      cpu_response_o.read_data = n2m_memory_pkg::OPEN_BUS_DATA;
    end else begin
      cpu_response_o.valid = cpu_busy_q && mem_response_i.valid;
      cpu_response_o.fault = cpu_busy_q && mem_response_i.fault;
      cpu_response_o.read_data = mem_response_i.read_data;
    end
  end

  `N2A_ASSERT(byte_not_rearmed_while_owed, clk_i, reset_i, !(need_q && busy_q))
  `N2A_ASSERT(one_master_outstanding, clk_i, reset_i, !(busy_q && cpu_busy_q))
  `N2A_ASSERT(response_has_owner, clk_i, reset_i,
      !mem_response_i.valid || busy_q || (cpu_busy_q && !blocked_q))
  `N2A_ASSERT(offset_in_transfer, clk_i, reset_i,
      !active_q || (offset_q < 8'(TRANSFER_BYTES)))
  `N2A_ASSERT(cpu_request_held, clk_i, reset_i,
      (cpu_request_i.valid && !cpu_response_o.valid) |=> $stable(cpu_request_i))
  `N2A_ASSERT_KNOWN(dma_controls_known, clk_i, reset_i,
      {machine_cycle_enable_i, cpu_request_i.valid, io_request_i.valid, mem_response_i.valid})
endmodule
