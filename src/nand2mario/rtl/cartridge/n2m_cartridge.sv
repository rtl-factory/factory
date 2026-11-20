`default_nettype none
`include "src/common/rtl/pkg/macros.vh"

// Game Boy cartridge mapper between the memory responder and the cartridge
// backing store. The memory map forwards every ROM ($0000-$7FFF) and
// cartridge RAM ($A000-$BFFF) request here on its acceptance cycle; this
// module answers MBC1 register writes, ignored ROM writes and disabled or
// absent RAM locally one edge later, and translates every other access into
// one backing request whose bank bits come from the MBC1 registers. The
// mapper kind and bank masks come from the header bytes $0147-$0149, captured
// as they pass on the ROM load port; types other than ROM-only and
// MBC1(+RAM) behave as ROM-only without RAM. The backing may stall: the
// request is held until the backing is ready and the response is passed back
// when it arrives, so the CPU-side latency is one edge plus the backing's
// own delay. Only one request is outstanding at a time.
module n2m_cartridge (
  input  logic                                   clk_i,
  input  logic                                   reset_i,
  // Request from the memory responder (live CPU request while it decodes to
  // ROM or cartridge RAM); held until ready_o is sampled high.
  input  n2m_interfaces_pkg::bus_request_t       request_i,
  output logic                                   ready_o,
  output n2m_interfaces_pkg::bus_response_t      response_o,
  // Backing store port.
  output n2m_cartridge_pkg::backing_request_t    backing_request_o,
  input  logic                                   backing_ready_i,
  input  n2m_cartridge_pkg::backing_response_t   backing_response_i,
  // ROM load port observation: header bytes are captured as they are loaded
  // into the backing, regardless of reset.
  input  logic                                   load_write_i,
  input  n2m_cartridge_pkg::rom_address_t        load_address_i,
  input  logic [7:0]                             load_data_i
);
  import n2m_cartridge_pkg::*;

  // Header registers: defined only after the header has been loaded; a
  // console reset keeps the inserted cartridge.
  logic [7:0] header_type_q, header_rom_size_q, header_ram_size_q;
  logic       type_load, rom_size_load, ram_size_load;
  logic       mbc1, ram_present;
  rom_bank_t  rom_mask;
  ram_bank_t  ram_mask;

  // MBC1 registers.
  logic [4:0] bank1_q, bank1_d;
  logic [1:0] bank2_q, bank2_d;
  logic       mode_q, mode_d;
  logic       ramg_q, ramg_d;
  logic       register_write, bank1_write, bank2_write, mode_write, ramg_write;

  // Request classification and translation.
  logic       rom_window, ram_window, ram_enabled;
  logic       local_request, local_accept, local_q;
  logic       backing_accept, backing_wait_d, backing_wait_q;
  logic [4:0] bank1_effective;
  rom_bank_t  low_bank, high_bank, rom_bank;
  ram_bank_t  ram_bank;
  rom_address_t rom_address, ram_address, backing_address;

  // ---------------------------------------------------------------------
  // Header capture from the load port.
  assign type_load = load_write_i && (load_address_i == HEADER_TYPE_OFFSET);
  assign rom_size_load = load_write_i && (load_address_i == HEADER_ROM_SIZE_OFFSET);
  assign ram_size_load = load_write_i && (load_address_i == HEADER_RAM_SIZE_OFFSET);
  `N2A_DFF_EN(header_type_q, load_data_i, clk_i, type_load)
  `N2A_DFF_EN(header_rom_size_q, load_data_i, clk_i, rom_size_load)
  `N2A_DFF_EN(header_ram_size_q, load_data_i, clk_i, ram_size_load)
  assign mbc1 = is_mbc1(header_type_q);
  assign ram_present = has_ram(header_type_q) && has_ram_capacity(header_ram_size_q);
  assign rom_mask = rom_bank_mask(header_rom_size_q);
  assign ram_mask = ram_bank_mask(header_ram_size_q);

  // ---------------------------------------------------------------------
  // Request classification. The memory map forwards only the two cartridge
  // windows; a write inside the ROM window is an MBC1 register write (or
  // nothing for ROM-only), a RAM access without enabled RAM is local.
  assign rom_window = request_i.address <= 16'h7FFF;
  assign ram_window = (request_i.address >= 16'hA000) && (request_i.address <= 16'hBFFF);
  assign ram_enabled = ram_present && ramg_q;
  assign local_request = rom_window ? request_i.write : !ram_enabled;
  assign ready_o = local_request ? 1'b1 : backing_ready_i;
  assign local_accept = request_i.valid && local_request;
  assign register_write = local_accept && rom_window && mbc1;
  assign bank1_write = register_write && (request_i.address[14:13] == MBC1_BANK1_SELECT);
  assign bank2_write = register_write && (request_i.address[14:13] == MBC1_BANK2_SELECT);
  assign mode_write = register_write && (request_i.address[14:13] == MBC1_MODE_SELECT);
  assign ramg_write = register_write && (request_i.address[14:13] == MBC1_RAMG_SELECT);

  // MBC1 registers: five-bit BANK1 (reset 1), two-bit BANK2, MODE and RAMG;
  // the full register value is written and masking happens on translation.
  assign bank1_d = request_i.write_data[4:0];
  assign bank2_d = request_i.write_data[1:0];
  assign mode_d = request_i.write_data[0];
  assign ramg_d = request_i.write_data[3:0] == MBC1_RAM_ENABLE_NIBBLE;
  `N2A_DFF_RST_EN(bank1_q, bank1_d, clk_i, bank1_write, reset_i, MBC1_BANK1_RESET)
  `N2A_DFF_RST_EN(bank2_q, bank2_d, clk_i, bank2_write, reset_i, 2'b00)
  `N2A_DFF_RST_EN(mode_q, mode_d, clk_i, mode_write, reset_i, 1'b0)
  `N2A_DFF_RST_EN(ramg_q, ramg_d, clk_i, ramg_write, reset_i, 1'b0)

  // ---------------------------------------------------------------------
  // Translation. BANK1 of zero selects bank one before masking (the whole
  // five-bit register is compared). The low ROM window is bank zero in mode
  // 0 and BANK2 << 5 in mode 1; the high window is {BANK2, BANK1}; both are
  // masked to the header's bank count. RAM is bank zero in mode 0 and BANK2
  // in mode 1, masked to the RAM bank count. A ROM-only (or unsupported)
  // type selects the identity explicitly: the MBC1 registers may hold stale
  // values from an earlier header without an intervening reset.
  assign bank1_effective = (bank1_q == 5'd0) ? 5'd1 : bank1_q;
  assign low_bank = (mbc1 && mode_q) ? {bank2_q, 5'd0} : 7'd0;
  assign high_bank = mbc1 ? {bank2_q, bank1_effective} : 7'd1;
  assign rom_bank = (request_i.address[14] ? high_bank : low_bank) & rom_mask;
  assign ram_bank = (mbc1 && mode_q ? bank2_q : 2'b00) & ram_mask;
  assign rom_address = {rom_bank, request_i.address[13:0]};
  assign ram_address = {6'd0, ram_bank, request_i.address[12:0]};
  assign backing_address = ram_window ? ram_address : rom_address;

  always_comb begin
    backing_request_o.valid = request_i.valid && !local_request;
    backing_request_o.write = request_i.write;
    backing_request_o.ram = ram_window;
    backing_request_o.address = backing_address;
    backing_request_o.write_data = request_i.write_data;
  end

  // ---------------------------------------------------------------------
  // Response. A local request is answered on the edge after acceptance from
  // the local_q register; a forwarded request is answered by the backing's
  // response, which is passed through in the same cycle.
  assign backing_accept = backing_request_o.valid && backing_ready_i;
  assign backing_wait_d = backing_accept || (backing_wait_q && !backing_response_i.valid);
  `N2A_DFF_RST(local_q, local_accept, clk_i, reset_i)
  `N2A_DFF_RST(backing_wait_q, backing_wait_d, clk_i, reset_i)

  always_comb begin
    response_o.valid = local_q || backing_response_i.valid;
    response_o.fault = 1'b0;
    response_o.read_data = local_q ? OPEN_BUS_DATA : backing_response_i.read_data;
  end

  // ---------------------------------------------------------------------
  // Protocol assertions (simulation only): known requests inside the two
  // cartridge windows, one outstanding request, and no backing response
  // without an accepted request.
  `N2A_ASSERT(request_known, clk_i, reset_i,
      !request_i.valid || !$isunknown(request_i))
  `N2A_ASSERT(request_in_cartridge_windows, clk_i, reset_i,
      !request_i.valid || rom_window || ram_window)
  `N2A_ASSERT(request_quiet_while_outstanding, clk_i, reset_i,
      !(local_q || backing_wait_q) || !request_i.valid)
  `N2A_ASSERT(backing_response_requested, clk_i, reset_i,
      !backing_response_i.valid || backing_wait_q)
  `N2A_ASSERT(backing_response_known, clk_i, reset_i,
      !backing_wait_q || !$isunknown(backing_response_i))
  `N2A_ASSERT_KNOWN(backing_ready_known, clk_i, reset_i, backing_ready_i)
  `N2A_ASSERT_KNOWN(load_port_known, clk_i, reset_i, load_write_i)
endmodule
