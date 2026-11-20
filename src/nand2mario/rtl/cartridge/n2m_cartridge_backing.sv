`default_nettype none
`include "src/common/rtl/pkg/macros.vh"

// On-chip cartridge backing store behind the cartridge backing port: one
// inferred ROM store written and read back through the load port and read by
// the mapper, and one inferred cartridge RAM store read and written by the
// mapper (and by the load port). Both are sized by parameter; address bits
// above a store's size are ignored. The backing port is always ready and
// answers every accepted request on the next edge (read data from the
// store's registered output), which is the fixed one-edge latency the
// simulation uses. The load port has the same records and latency; it is
// ready except on an edge where it targets the RAM store while the mapper
// accesses it, and it accepts a write on every edge. The SDRAM backing
// (n2m_sdram_backing) implements the same two ports and may stall. Store
// contents have no reset or power-up value; this backing is initialized from
// the first edge.
module n2m_cartridge_backing #(
  parameter int unsigned ROM_BYTES = 32768,
  parameter int unsigned RAM_BYTES = 8192
) (
  input  logic                                   clk_i,
  input  logic                                   reset_i,
  // Backing port.
  input  n2m_cartridge_pkg::backing_request_t    request_i,
  output logic                                   ready_o,
  output n2m_cartridge_pkg::backing_response_t   response_o,
  // Load port: ROM/RAM reads and writes with the backing records, accepted
  // regardless of reset (reset only drops the response).
  input  n2m_cartridge_pkg::backing_request_t    load_request_i,
  output logic                                   load_ready_o,
  output n2m_cartridge_pkg::backing_response_t   load_response_o,
  // Constant: an on-chip store needs no initialization.
  output logic                                   initialized_o
);
  import n2m_cartridge_pkg::*;

  localparam int unsigned ROM_ADDRESS_BITS = $clog2(ROM_BYTES);
  localparam int unsigned RAM_ADDRESS_BITS = $clog2(RAM_BYTES);

  `N2A_ASSERT_PARAM(g_invalid_rom_bytes,
      (ROM_BYTES >= MIN_ROM_BYTES) && (ROM_BYTES <= MAX_ROM_BYTES) &&
      ((ROM_BYTES & (ROM_BYTES - 1)) == 0),
      "n2m_cartridge_backing ROM_BYTES must be a power of two between 32 KiB and 2 MiB")
  `N2A_ASSERT_PARAM(g_invalid_ram_bytes,
      (RAM_BYTES >= MIN_RAM_BYTES) && (RAM_BYTES <= MAX_RAM_BYTES) &&
      ((RAM_BYTES & (RAM_BYTES - 1)) == 0),
      "n2m_cartridge_backing RAM_BYTES must be a power of two between 8 KiB and 32 KiB")

  logic                        accept, rom_read, ram_access, ram_read, ram_write;
  logic                        load_accept, load_rom, load_rom_read, load_rom_write;
  logic                        load_ram, load_ram_read, load_ram_write;
  logic [ROM_ADDRESS_BITS-1:0] rom_offset, load_offset;
  logic [RAM_ADDRESS_BITS-1:0] ram_offset, mapper_ram_offset, load_ram_offset;
  logic [7:0]                  ram_write_data;
  logic [7:0]                  rom_data, ram_data, load_rom_data;
  logic                        rom_valid, ram_valid, load_rom_valid;
  logic                        valid_q, ram_q;
  logic                        load_valid_q, load_ram_q;
  logic                        load_reset;

  // Always ready on the backing port: the mapper presents at most one
  // request at a time and every store answers on the next edge. The load
  // port yields the RAM store to the mapper on a shared edge.
  assign ready_o = 1'b1;
  assign initialized_o = 1'b1;
  assign accept = request_i.valid;
  assign ram_access = accept && request_i.ram;
  assign rom_read = accept && !request_i.ram && !request_i.write;
  assign load_ready_o = !(load_request_i.ram && ram_access);
  assign load_accept = load_request_i.valid && load_ready_o;
  assign load_rom = load_accept && !load_request_i.ram;
  assign load_ram = load_accept && load_request_i.ram;
  assign load_rom_read = load_rom && !load_request_i.write;
  assign load_rom_write = load_rom && load_request_i.write;
  assign load_ram_read = load_ram && !load_request_i.write;
  assign load_ram_write = load_ram && load_request_i.write;
  assign ram_read = (ram_access && !request_i.write) || load_ram_read;
  assign ram_write = (ram_access && request_i.write) || load_ram_write;
  assign rom_offset = request_i.address[ROM_ADDRESS_BITS-1:0];
  assign mapper_ram_offset = request_i.address[RAM_ADDRESS_BITS-1:0];
  assign load_ram_offset = load_request_i.address[RAM_ADDRESS_BITS-1:0];
  assign ram_offset = ram_access ? mapper_ram_offset : load_ram_offset;
  assign ram_write_data = ram_access ? request_i.write_data : load_request_i.write_data;
  assign load_offset = load_request_i.address[ROM_ADDRESS_BITS-1:0];

  // ROM: port A is the load port (never reset), port B the mapper's read
  // port; a same-edge load write and mapper read of one byte read first.
  assign load_reset = 1'b0;
  /* verilator lint_off PINCONNECTEMPTY */
  n2m_dual_port_ram #(
    .DATA_WIDTH (8),
    .DEPTH      (ROM_BYTES),
    .DUAL_CLOCK (1'b0)
  ) u_rom (
    .clk_a_i         (clk_i),
    .reset_a_i       (load_reset),
    .read_a_i        (load_rom_read),
    .write_a_i       (load_rom_write),
    .address_a_i     (load_offset),
    .write_data_a_i  (load_request_i.write_data),
    .byte_enable_a_i (1'b1),
    .read_data_a_o   (load_rom_data),
    .read_valid_a_o  (load_rom_valid),
    .clk_b_i         (clk_i),
    .reset_b_i       (reset_i),
    .read_b_i        (rom_read),
    .address_b_i     (rom_offset),
    .read_data_b_o   (rom_data),
    .read_valid_b_o  (rom_valid)
  );

  // Cartridge RAM: the mapper, or the load port when the mapper is not
  // accessing RAM, reads and writes through port A; port B is reserved.
  n2m_dual_port_ram #(
    .DATA_WIDTH (8),
    .DEPTH      (RAM_BYTES),
    .DUAL_CLOCK (1'b0)
  ) u_ram (
    .clk_a_i         (clk_i),
    .reset_a_i       (load_reset),
    .read_a_i        (ram_read),
    .write_a_i       (ram_write),
    .address_a_i     (ram_offset),
    .write_data_a_i  (ram_write_data),
    .byte_enable_a_i (1'b1),
    .read_data_a_o   (ram_data),
    .read_valid_a_o  (ram_valid),
    .clk_b_i         (clk_i),
    .reset_b_i       (reset_i),
    .read_b_i        (1'b0),
    .address_b_i     ('0),
    .read_data_b_o   (),
    .read_valid_b_o  ()
  );
  /* verilator lint_on PINCONNECTEMPTY */

  // Responses one edge after acceptance; the registered store selection
  // picks the data. Write responses carry no meaningful data.
  `N2A_DFF_RST(valid_q, accept, clk_i, reset_i)
  `N2A_DFF_RST_EN(ram_q, request_i.ram, clk_i, accept, reset_i, 1'b0)
  `N2A_ASSERT_HISTORY_RST(logic, read_q, accept ? !request_i.write : read_q, clk_i, reset_i)
  always_comb begin
    response_o.valid = valid_q;
    response_o.read_data = ram_q ? ram_data : rom_data;
  end

  `N2A_DFF_RST(load_valid_q, load_accept, clk_i, reset_i)
  `N2A_DFF_RST_EN(load_ram_q, load_request_i.ram, clk_i, load_accept, reset_i, 1'b0)
  `N2A_ASSERT_HISTORY_RST(logic, load_read_q,
      load_accept ? !load_request_i.write : load_read_q, clk_i, reset_i)
  always_comb begin
    load_response_o.valid = load_valid_q;
    load_response_o.read_data = load_ram_q ? ram_data : load_rom_data;
  end

  `N2A_ASSERT(request_known, clk_i, reset_i,
      !request_i.valid || !$isunknown(request_i))
  `N2A_ASSERT(load_request_known, clk_i, reset_i,
      !load_request_i.valid || !$isunknown(load_request_i))
  `N2A_ASSERT_NEVER(rom_write_rejected, clk_i, reset_i,
      request_i.valid && !request_i.ram && request_i.write)
  `N2A_ASSERT(request_one_outstanding, clk_i, reset_i,
      !valid_q || !request_i.valid)
  `N2A_ASSERT(load_read_one_outstanding, clk_i, reset_i,
      !(load_valid_q && load_read_q) || !load_request_i.valid)
  `N2A_ASSERT(read_served, clk_i, reset_i,
      !(valid_q && read_q) || (ram_q ? ram_valid : rom_valid))
  `N2A_ASSERT(load_read_served, clk_i, reset_i,
      !(load_valid_q && load_read_q) || (load_ram_q ? ram_valid : load_rom_valid))
endmodule
