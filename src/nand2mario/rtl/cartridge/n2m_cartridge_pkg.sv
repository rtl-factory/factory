`default_nettype none

// Cartridge facts owned by the nand2mario cartridge block: the backing port
// records, the MBC1 address reach, the header bytes the mapper reads and the
// MBC1 register windows. The package holds facts and pure helpers only;
// n2m_cartridge owns the mapper behavior and n2m_cartridge_backing the
// on-chip store behind the backing port.
package n2m_cartridge_pkg;
  // Published facts: the mapper, the backing and the DV each use a subset,
  // so an elaboration need not consume every constant.
  /* verilator lint_off UNUSEDPARAM */
  // MBC1 reaches 128 ROM banks of 16 KiB (2 MiB) and 4 RAM banks of 8 KiB
  // (32 KiB). The backing address is wide enough for the full reach; a
  // smaller backing ignores the upper address bits.
  localparam int unsigned ROM_BANK_WIDTH    = 7;
  localparam int unsigned RAM_BANK_WIDTH    = 2;
  localparam int unsigned ROM_BANK_BYTES    = 16384;
  localparam int unsigned RAM_BANK_BYTES    = 8192;
  localparam int unsigned ROM_ADDRESS_WIDTH = 21;
  localparam int unsigned RAM_ADDRESS_WIDTH = 15;
  localparam int unsigned MAX_ROM_BYTES     = 1 << ROM_ADDRESS_WIDTH;
  localparam int unsigned MAX_RAM_BYTES     = 1 << RAM_ADDRESS_WIDTH;
  localparam int unsigned MIN_ROM_BYTES     = 32768;
  localparam int unsigned MIN_RAM_BYTES     = RAM_BANK_BYTES;

  typedef logic [ROM_ADDRESS_WIDTH-1:0] rom_address_t;
  typedef logic [ROM_BANK_WIDTH-1:0]    rom_bank_t;
  typedef logic [RAM_BANK_WIDTH-1:0]    ram_bank_t;

  // Backing port: the mapper presents one request and holds it until the
  // backing samples it with ready high; exactly one response follows on a
  // later edge. `ram` selects the cartridge RAM store, otherwise the ROM.
  typedef struct packed {
    logic         valid;
    logic         write;
    logic         ram;
    rom_address_t address;
    logic [7:0]   write_data;
  } backing_request_t;

  typedef struct packed {
    logic       valid;
    logic [7:0] read_data;
  } backing_response_t;

  // Header bytes the mapper observes on the load port.
  localparam rom_address_t HEADER_TYPE_OFFSET     = 21'h000147;
  localparam rom_address_t HEADER_ROM_SIZE_OFFSET = 21'h000148;
  localparam rom_address_t HEADER_RAM_SIZE_OFFSET = 21'h000149;

  // Cartridge types this block implements; every other type behaves as
  // ROM-only without cartridge RAM.
  localparam logic [7:0] TYPE_ROM_ONLY         = 8'h00;
  localparam logic [7:0] TYPE_MBC1             = 8'h01;
  localparam logic [7:0] TYPE_MBC1_RAM         = 8'h02;
  localparam logic [7:0] TYPE_MBC1_RAM_BATTERY = 8'h03;

  // MBC1 registers are selected by CPU address bits [14:13] inside the ROM
  // window; RAM is enabled by a low write nibble of $A.
  localparam logic [1:0] MBC1_RAMG_SELECT  = 2'b00;  // $0000-$1FFF
  localparam logic [1:0] MBC1_BANK1_SELECT = 2'b01;  // $2000-$3FFF
  localparam logic [1:0] MBC1_BANK2_SELECT = 2'b10;  // $4000-$5FFF
  localparam logic [1:0] MBC1_MODE_SELECT  = 2'b11;  // $6000-$7FFF
  localparam logic [3:0] MBC1_RAM_ENABLE_NIBBLE = 4'hA;
  localparam logic [4:0] MBC1_BANK1_RESET = 5'd1;

  // Byte returned by a disabled or absent cartridge RAM read.
  localparam logic [7:0] OPEN_BUS_DATA = 8'hFF;
  /* verilator lint_on UNUSEDPARAM */

  function automatic logic is_mbc1(input logic [7:0] cartridge_type);
    return (cartridge_type == TYPE_MBC1) || (cartridge_type == TYPE_MBC1_RAM) ||
           (cartridge_type == TYPE_MBC1_RAM_BATTERY);
  endfunction

  function automatic logic has_ram(input logic [7:0] cartridge_type);
    return (cartridge_type == TYPE_MBC1_RAM) || (cartridge_type == TYPE_MBC1_RAM_BATTERY);
  endfunction

  // Header $0148: code n means 2 << n banks for n <= 6; every larger code is
  // beyond the MBC1 reach and keeps all seven bank bits.
  function automatic rom_bank_t rom_bank_mask(input logic [7:0] rom_size_code);
    rom_bank_t mask;
    case (rom_size_code)
      8'h00: mask = 7'h01;
      8'h01: mask = 7'h03;
      8'h02: mask = 7'h07;
      8'h03: mask = 7'h0F;
      8'h04: mask = 7'h1F;
      8'h05: mask = 7'h3F;
      default: mask = 7'h7F;
    endcase
    return mask;
  endfunction

  // Header $0149: $00 (and the unused $01) declare no RAM; $02 is one 8 KiB
  // bank; $03 (32 KiB) and the larger codes bank the RAM in four 8 KiB banks.
  // Cartridge RAM exists only when the type has RAM and the size declares it.
  function automatic logic has_ram_capacity(input logic [7:0] ram_size_code);
    return (ram_size_code == 8'h02) || (ram_size_code == 8'h03) ||
           (ram_size_code == 8'h04) || (ram_size_code == 8'h05);
  endfunction

  function automatic ram_bank_t ram_bank_mask(input logic [7:0] ram_size_code);
    return ((ram_size_code == 8'h03) || (ram_size_code == 8'h04) || (ram_size_code == 8'h05)) ?
           2'b11 : 2'b00;
  endfunction
endpackage
