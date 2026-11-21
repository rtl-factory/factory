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
    input  n2m_interfaces_pkg::t_bus_request       request_i,
    output logic                                   ready_o,
    output n2m_interfaces_pkg::t_bus_response      response_o,
    // Backing store port.
    output n2m_cartridge_pkg::t_backing_request    backing_request_o,
    input  logic                                   backing_ready_i,
    input  n2m_cartridge_pkg::t_backing_response   backing_response_i,
    // ROM load port observation: header bytes are captured as they are loaded
    // into the backing, regardless of reset.
    input  logic                                   load_write_i,
    input  n2m_cartridge_pkg::t_rom_address        load_address_i,
    input  logic [7:0]                             load_data_i
);
    import n2m_cartridge_pkg::*;

    // Why: the header registers have no reset so that a console reset keeps the
    // inserted cartridge; they are defined once the header has been loaded.
    logic [7:0] header_type_q, header_rom_size_q, header_ram_size_q;
    logic       type_load, rom_size_load, ram_size_load;
    logic       mbc1, ram_present;
    t_rom_bank  rom_mask;
    t_ram_bank  ram_mask;

    logic [4:0] bank1_q, bank1_d;
    logic [1:0] bank2_q, bank2_d;
    logic       mode_q, mode_d;
    logic       ramg_q, ramg_d;
    logic       register_write, bank1_write, bank2_write, mode_write, ramg_write;

    logic       rom_window, ram_window, ram_enabled;
    logic       local_request, local_accept, local_q;
    logic       backing_accept, backing_wait_d, backing_wait_q;
    logic [4:0] bank1_effective;
    t_rom_bank  low_bank, high_bank, rom_bank;
    t_ram_bank  ram_bank;
    t_rom_address rom_address, ram_address, backing_address;

    assign type_load = load_write_i && (load_address_i == HEADER_TYPE_OFFSET);
    assign rom_size_load = load_write_i && (load_address_i == HEADER_ROM_SIZE_OFFSET);
    assign ram_size_load = load_write_i && (load_address_i == HEADER_RAM_SIZE_OFFSET);
    `DFF_EN(header_type_q, load_data_i, clk_i, type_load)
    `DFF_EN(header_rom_size_q, load_data_i, clk_i, rom_size_load)
    `DFF_EN(header_ram_size_q, load_data_i, clk_i, ram_size_load)
    assign mbc1 = is_mbc1(header_type_q);
    assign ram_present = has_ram(header_type_q) && has_ram_capacity(header_ram_size_q);
    assign rom_mask = rom_bank_mask(header_rom_size_q);
    assign ram_mask = ram_bank_mask(header_ram_size_q);

    // Ref: Pan Docs "MBC1": the memory map forwards only the two cartridge
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

    // Ref: Pan Docs "MBC1": five-bit BANK1 (reset 1), two-bit BANK2, MODE and
    // RAMG. Why: the full value is stored and masked only on translation,
    // because the BANK1-zero check must see all five bits.
    assign bank1_d = request_i.write_data[4:0];
    assign bank2_d = request_i.write_data[1:0];
    assign mode_d = request_i.write_data[0];
    assign ramg_d = request_i.write_data[3:0] == MBC1_RAM_ENABLE_NIBBLE;
    `DFF_RST_EN(bank1_q, bank1_d, clk_i, bank1_write, reset_i, MBC1_BANK1_RESET)
    `DFF_RST_EN(bank2_q, bank2_d, clk_i, bank2_write, reset_i, 2'b00)
    `DFF_RST_EN(mode_q, mode_d, clk_i, mode_write, reset_i, 1'b0)
    `DFF_RST_EN(ramg_q, ramg_d, clk_i, ramg_write, reset_i, 1'b0)

    // Ref: Pan Docs "MBC1": BANK1 of zero selects bank one before masking (the
    // whole five-bit register is compared). The low ROM window is bank zero in mode
    // 0 and BANK2 << 5 in mode 1; the high window is {BANK2, BANK1}; both are
    // masked to the header's bank count. RAM is bank zero in mode 0 and BANK2
    // in mode 1, masked to the RAM bank count. Why: a ROM-only (or unsupported)
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

    // Why: a local answer comes from local_q one edge after acceptance, the
    // backing's own next-edge timing, so a game sees one latency; a forwarded
    // request passes the backing's response through in the same cycle and adds
    // no cycle.
    assign backing_accept = backing_request_o.valid && backing_ready_i;
    assign backing_wait_d = backing_accept || (backing_wait_q && !backing_response_i.valid);
    `DFF_RST(local_q, local_accept, clk_i, reset_i)
    `DFF_RST(backing_wait_q, backing_wait_d, clk_i, reset_i)

    always_comb begin
        response_o.valid = local_q || backing_response_i.valid;
        response_o.fault = 1'b0;
        response_o.read_data = local_q ? OPEN_BUS_DATA : backing_response_i.read_data;
    end

    `ASSERT(request_known, clk_i, reset_i,
        !request_i.valid || !$isunknown(request_i))
    `ASSERT(request_in_cartridge_windows, clk_i, reset_i,
        !request_i.valid || rom_window || ram_window)
    `ASSERT(request_quiet_while_outstanding, clk_i, reset_i,
        !(local_q || backing_wait_q) || !request_i.valid)
    `ASSERT(backing_response_requested, clk_i, reset_i,
        !backing_response_i.valid || backing_wait_q)
    `ASSERT(backing_response_known, clk_i, reset_i,
        !backing_wait_q || !$isunknown(backing_response_i))
    `ASSERT_KNOWN(backing_ready_known, clk_i, reset_i, backing_ready_i)
    `ASSERT_KNOWN(load_port_known, clk_i, reset_i, load_write_i)
endmodule
