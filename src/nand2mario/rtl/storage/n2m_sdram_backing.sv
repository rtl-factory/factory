`default_nettype none
`include "src/common/rtl/pkg/macros.vh"

// SDRAM cartridge backing: the cartridge backing port and the cartridge load
// port served by one n2m_sdram_ctrl over the DE10-Lite SDRAM. The ROM image
// lives at device byte addresses 0 to 2 MiB - 1 and the cartridge RAM at
// 2 MiB to 2 MiB + 32 KiB - 1, one byte per lane of the x16 words. Both
// ports share the controller through a fixed-priority arbiter (the mapper
// first): a request is accepted only while the controller is idle, ready
// and initialized, forwarded in the same cycle, and answered when the
// controller's completion pulse returns (nine clocks after acceptance for
// reads, ten for writes, more when a refresh was due). The mapper's ready is
// a function of state only; the load port's ready also excludes an edge on
// which the mapper presents a request. Registers reset with the system
// reset, so a core reset leaves the load path, the controller and the SDRAM
// contents untouched.
module n2m_sdram_backing (
  input  logic                                   clk_i,
  input  logic                                   reset_i,
  // Cartridge backing port (the mapper).
  input  n2m_cartridge_pkg::backing_request_t    request_i,
  output logic                                   ready_o,
  output n2m_cartridge_pkg::backing_response_t   response_o,
  // Load port (the loader and the simulation preload path): ROM and RAM
  // reads and writes with the same records and one outstanding request.
  input  n2m_cartridge_pkg::backing_request_t    load_request_i,
  output logic                                   load_ready_o,
  output n2m_cartridge_pkg::backing_response_t   load_response_o,
  // High once the SDRAM finished its power-up initialization.
  output logic                                   initialized_o,
  // DE10-Lite SDRAM pins (data bus split; the board top resolves the pin).
  output logic [12:0]                            dram_addr_o,
  output logic [1:0]                             dram_ba_o,
  output logic                                   dram_cas_n_o,
  output logic                                   dram_cke_o,
  output logic                                   dram_clk_o,
  output logic                                   dram_cs_n_o,
  output logic                                   dram_dqml_o,
  output logic                                   dram_dqmh_o,
  output logic                                   dram_ras_n_o,
  output logic                                   dram_we_n_o,
  output logic [15:0]                            dram_dq_o,
  output logic                                   dram_dq_oe_o,
  input  logic [15:0]                            dram_dq_i
);
  import n2m_cartridge_pkg::*;
  import n2m_sdram_pkg::*;

  // Device byte address bit that selects the cartridge RAM region.
  localparam int unsigned RAM_REGION_BIT = ROM_ADDRESS_WIDTH;

  logic                           ctrl_ready, ctrl_response_valid, ctrl_idle;
  logic [SDRAM_DATA_BITS-1:0]     ctrl_response_data;
  logic                           accept_mapper, accept_loader, accept;
  logic                           selected_write, selected_ram;
  rom_address_t                   selected_address;
  logic [7:0]                     selected_write_data;
  logic [SDRAM_ADDRESS_BITS-1:0]  device_address;
  logic [SDRAM_DATA_BITS-1:0]     write_word;
  logic [SDRAM_BYTE_LANES-1:0]    byte_enable;
  logic                           busy_q, busy_d, source_q, lane_q;
  logic                           source_d, lane_d, complete;
  logic [7:0]                     read_byte;

  // Why: the mapper wins an edge on which both present a request because the
  // CPU waits on it, while a load simply retries.
  assign ready_o = !busy_q && ctrl_ready;
  assign load_ready_o = ready_o && !request_i.valid;
  assign accept_mapper = request_i.valid && ready_o;
  assign accept_loader = load_request_i.valid && load_ready_o;
  assign accept = accept_mapper || accept_loader;
  assign selected_write = accept_mapper ? request_i.write : load_request_i.write;
  assign selected_ram = accept_mapper ? request_i.ram : load_request_i.ram;
  assign selected_address = accept_mapper ? request_i.address : load_request_i.address;
  assign selected_write_data = accept_mapper ? request_i.write_data : load_request_i.write_data;

  // Why: RAM starts at 2 MiB, above the largest MBC1 ROM, so the two never
  // overlap. The byte lane is address bit 0 and the written byte is duplicated
  // on both lanes so the mask alone selects it.
  always_comb begin
    device_address = '0;
    device_address[RAM_REGION_BIT] = selected_ram;
    if (selected_ram) device_address[RAM_ADDRESS_WIDTH-1:0] = selected_address[RAM_ADDRESS_WIDTH-1:0];
    else device_address[ROM_ADDRESS_WIDTH-1:0] = selected_address;
    device_address[0] = 1'b0;
  end
  assign write_word = {selected_write_data, selected_write_data};
  assign byte_enable = selected_address[0] ? 2'b10 : 2'b01;

  n2m_sdram_ctrl u_ctrl (
    .clk_i                 (clk_i),
    .reset_i               (reset_i),
    .request_valid_i       (accept),
    .request_write_i       (selected_write),
    .request_address_i     (device_address),
    .request_write_data_i  (write_word),
    .request_byte_enable_i (byte_enable),
    .request_ready_o       (ctrl_ready),
    .response_valid_o      (ctrl_response_valid),
    .response_data_o       (ctrl_response_data),
    .idle_o                (ctrl_idle),
    .initialized_o         (initialized_o),
    .dram_addr_o           (dram_addr_o),
    .dram_ba_o             (dram_ba_o),
    .dram_cas_n_o          (dram_cas_n_o),
    .dram_cke_o            (dram_cke_o),
    .dram_clk_o            (dram_clk_o),
    .dram_cs_n_o           (dram_cs_n_o),
    .dram_dqml_o           (dram_dqml_o),
    .dram_dqmh_o           (dram_dqmh_o),
    .dram_ras_n_o          (dram_ras_n_o),
    .dram_we_n_o           (dram_we_n_o),
    .dram_dq_o             (dram_dq_o),
    .dram_dq_oe_o          (dram_dq_oe_o),
    .dram_dq_i             (dram_dq_i)
  );

  // Why: the controller serves one access at a time, so one transaction is in
  // flight; busy_q records which port owns it and which byte lane it reads so
  // the completion pulse reaches that port.
  assign complete = busy_q && ctrl_response_valid;
  assign busy_d = accept || (busy_q && !ctrl_response_valid);
  assign source_d = accept_loader;
  assign lane_d = selected_address[0];
  `N2A_DFF_RST(busy_q, busy_d, clk_i, reset_i)
  `N2A_DFF_RST_EN(source_q, source_d, clk_i, accept, reset_i, 1'b0)
  `N2A_DFF_RST_EN(lane_q, lane_d, clk_i, accept, reset_i, 1'b0)
  assign read_byte = lane_q ? ctrl_response_data[15:8] : ctrl_response_data[7:0];

  always_comb begin
    response_o.valid = complete && !source_q;
    response_o.read_data = read_byte;
    load_response_o.valid = complete && source_q;
    load_response_o.read_data = read_byte;
  end

  `N2A_ASSERT(request_known, clk_i, reset_i,
      !request_i.valid || !$isunknown(request_i))
  `N2A_ASSERT(load_request_known, clk_i, reset_i,
      !load_request_i.valid || !$isunknown(load_request_i))
  `N2A_ASSERT_NEVER(rom_write_rejected, clk_i, reset_i,
      request_i.valid && !request_i.ram && request_i.write)
  `N2A_ASSERT(request_one_outstanding, clk_i, reset_i,
      !(busy_q && !source_q) || !request_i.valid)
  `N2A_ASSERT(load_request_one_outstanding, clk_i, reset_i,
      !(busy_q && source_q) || !load_request_i.valid)
  `N2A_ASSERT(completion_requested, clk_i, reset_i,
      !ctrl_response_valid || busy_q)
  `N2A_ASSERT(ready_implies_idle, clk_i, reset_i,
      !ready_o || ctrl_idle)
endmodule
