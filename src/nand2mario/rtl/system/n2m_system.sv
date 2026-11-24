`default_nettype none
`include "src/common/rtl/pkg/macros.vh"

// nand2mario system composition in the 25 MHz domain: the Game Boy rate
// timebase, the LR35902 CPU, the OAM DMA block on the CPU bus, the Game Boy
// memory map, the cartridge mapper with its backing store on the memory's
// cartridge port and, on the memory's peripheral port, the timer, the
// interrupt-flag register, the PPU's LCD registers, the DMA register, the
// joypad, the serial port and the powered-off APU (audio masks, wave RAM).
// A small I/O fan-out answers each forwarded
// $FF00-$FF7F request from the block that claims its address and returns
// open-bus $FF for unclaimed addresses. The PPU (VBlank, STAT), timer,
// serial and joypad blocks request their interrupt flags; the joypad also
// supplies the CPU's STOP policy input and wake request. The DMA block gates
// the CPU's requests in front of the memory map and copies OAM through the
// memory's DMA write port; the PPU reads VRAM and OAM through the memory
// map and blocks CPU VRAM/OAM access by mode; its pixel stream leaves on the
// outputs and feeds the display tier, whose frame bridge stores complete
// frames and whose scanout drives the 3x scaled 640x480 VGA output; the
// loader can pin the displayed frame and read it back over the UART.
//
// The UART link (n2m_uart_link) puts the common UART memory endpoint in
// front of the cartridge loader, so either protocol can hold the core and
// load the backing. The cartridge backing is the on-chip store sized by parameter, with
// CART_SDRAM set the DE10-Lite SDRAM behind the adapted controller or, with
// CART_EXTERNAL set, the off-chip cartridge bus of the ASIC chip top. With
// DISPLAY clear the display tier's outputs are not used (VGA pins parked,
// the loader sees no snapshot), so synthesis removes the tier. The
// UART link and the backing live in the system reset domain; the
// console core (everything else) is held in reset while the link holds it
// and until the backing is initialized. The direct load port remains the
// simulation preload path and shares the backing's load port with the
// loader. This is the single growing full-system simulation top; later
// subsystems attach here.
//
// reset_i is the synchronous system reset; the common UART PHYs and endpoint
// inside the link assert it asynchronously by their own contract, and the
// mixed use is intended (the link documents the waiver).
/* verilator lint_off SYNCASYNCNET */
module n2m_system #(
    parameter int unsigned CART_ROM_BYTES = 2097152,
    parameter int unsigned CART_RAM_BYTES = 32768,
    // 0: on-chip backing sized above; 1: SDRAM backing on the dram_* pins.
    parameter int unsigned CART_SDRAM = 0,
    // 1: backing on the off-chip cartridge bus (cart_bus_* pins).
    parameter int unsigned CART_EXTERNAL = 0,
    // 1: the display tier drives the VGA pins and serves frame snapshots.
    parameter int unsigned DISPLAY = 1,
    // 1: backing contains the pinned 16-slot library; boot its menu on release.
    parameter int unsigned CART_LIBRARY = 0,
    // Loader UART bit period: UART_CLKS_PER_BIT + UART_FRAC_NUM/UART_FRAC_DEN
    // clocks. 12 1/2 is the 2 Mbaud board rate at 25 MHz; simulation targets
    // shorten it.
    parameter int unsigned UART_CLKS_PER_BIT = 12,
    parameter int unsigned UART_FRAC_NUM = 1,
    parameter int unsigned UART_FRAC_DEN = 2
) (
    input  logic                               clk_i,
    input  logic                               reset_i,
    // Direct cartridge preload port (simulation): one ROM write per accepted
    // edge into the backing; the mapper reads the header bytes as they pass.
    // Not used together with the loader.
    input  logic                               rom_load_write_i,
    input  n2m_cartridge_pkg::t_rom_address    rom_load_address_i,
    input  logic [7:0]                         rom_load_data_i,
    output logic                               rom_load_ready_o,
    // Board UART (8-N-1): common UART memory endpoint and cartridge loader.
    input  logic                               uart_rx_i,
    output logic                               uart_tx_o,
    // Console core reset level: reset_i, a hold on the UART link, an
    // uninitialized backing or the boot VRAM sweep still running.
    output logic                               core_reset_o,
    // Active-high buttons: Right, Left, Up, Down, A, B, Select, Start.
    input  logic [7:0]                         buttons_i,
    // Each byte the serial port transmitted, for one cycle per completion.
    output logic                               serial_tx_valid_o,
    output logic [7:0]                         serial_tx_data_o,
    // Architectural observation and execution status from the CPU.
    output n2m_interfaces_pkg::t_retirement    retirement_o,
    output logic                               halted_o,
    output logic                               stopped_o,
    output logic                               locked_o,
    output logic                               fault_o,
    // PPU pixel stream (one-cycle pulses) with line/frame markers.
    output logic                               pixel_valid_o,
    output logic [1:0]                         pixel_shade_o,
    output logic [7:0]                         pixel_x_o,
    output logic [7:0]                         pixel_y_o,
    output logic                               line_start_o,
    output logic                               frame_start_o,
    // VGA output of the display tier: 4:4:4 RGB, active-low syncs, the pulse
    // on the first clock of each output frame and the bank-policy observation.
    output logic [3:0]                         vga_red_o,
    output logic [3:0]                         vga_green_o,
    output logic [3:0]                         vga_blue_o,
    output logic                               vga_hsync_o,
    output logic                               vga_vsync_o,
    output logic                               pixel_start_o,
    output logic                               frame_swapped_o,
    output logic                               frame_dropped_o,
    output logic                               frame_repeated_o,
    output logic                               display_valid_o,
    // DE10-Lite SDRAM pins (idle when CART_SDRAM is 0); the board top
    // resolves the split data bus onto DRAM_DQ.
    output logic [12:0]                        dram_addr_o,
    output logic [1:0]                         dram_ba_o,
    output logic                               dram_cas_n_o,
    output logic                               dram_cke_o,
    output logic                               dram_clk_o,
    output logic                               dram_cs_n_o,
    output logic                               dram_dqml_o,
    output logic                               dram_dqmh_o,
    output logic                               dram_ras_n_o,
    output logic                               dram_we_n_o,
    output logic [15:0]                        dram_dq_o,
    output logic                               dram_dq_oe_o,
    input  logic [15:0]                        dram_dq_i,
    // Off-chip cartridge bus (idle when CART_EXTERNAL is 0): backing records
    // with ready/response, one outstanding request (g_external below). The
    // other backings ignore the inputs (waived here, so no placeholder object
    // reaches a Quartus build).
    output n2m_cartridge_pkg::t_backing_request  cart_bus_request_o,
    /* verilator lint_off UNUSEDSIGNAL */
    input  logic                                 cart_bus_ready_i,
    input  n2m_cartridge_pkg::t_backing_response cart_bus_response_i
    /* verilator lint_on UNUSEDSIGNAL */
);
    logic                                core_reset, library_hold;
    logic                                legacy_cart_ready;
    n2m_interfaces_pkg::t_bus_request    legacy_cart_request;
    n2m_interfaces_pkg::t_bus_response   legacy_cart_response;
    n2m_cartridge_pkg::t_backing_request legacy_backing_request;
    n2m_cartridge_pkg::t_backing_response legacy_backing_response;
    logic                                dot_enable, machine_cycle_enable;
    logic [4:0]                          interrupt_enable;
    logic [4:0]                          interrupt_flags;
    logic [4:0]                          interrupt_ack;
    logic [4:0]                          interrupt_sources;
    logic                                divider_reset;
    logic                                timer_interrupt, vblank_interrupt, stat_interrupt;
    logic                                serial_interrupt, joypad_interrupt;
    logic                                timer_selected, interrupts_selected, ppu_selected;
    logic                                joypad_selected, serial_selected, dma_selected;
    logic                                apu_selected;
    logic                                apu_sequencer_bit;
    logic                                serial_clock;
    logic                                joyp_selected_active, wake;
    n2m_interfaces_pkg::t_bus_request    cpu_request, request;
    n2m_interfaces_pkg::t_bus_response   cpu_response, response;
    n2m_interfaces_pkg::t_bus_request    io_request;
    n2m_interfaces_pkg::t_bus_response   io_response;
    n2m_interfaces_pkg::t_bus_response   timer_response, interrupts_response, ppu_response;
    n2m_interfaces_pkg::t_bus_response   joypad_response, serial_response, dma_response;
    n2m_interfaces_pkg::t_bus_response   apu_response;
    logic                                ppu_vram_read, ppu_vram_valid;
    logic [12:0]                         ppu_vram_address;
    logic [7:0]                          ppu_vram_data;
    logic                                ppu_oam_read, ppu_oam_valid;
    logic [7:0]                          ppu_oam_address, ppu_oam_data;
    logic                                dma_oam_write, dma_active;
    logic [7:0]                          dma_oam_address, dma_oam_data;
    logic                                vram_blocked, oam_blocked;
    n2m_interfaces_pkg::t_bus_request    cart_request;
    n2m_interfaces_pkg::t_bus_response   cart_response;
    logic                                cart_ready, backing_ready, backing_initialized;
    n2m_cartridge_pkg::t_backing_request  backing_request;
    n2m_cartridge_pkg::t_backing_response backing_response;
    n2m_cartridge_pkg::t_backing_request  loader_request, direct_request, load_request;
    n2m_cartridge_pkg::t_backing_response load_response, loader_response;
    logic                                load_owner_direct_q;
    logic                                load_outstanding_q, load_outstanding_d, direct_valid;
    logic                                load_ready, loader_ready, loader_hold, loader_busy;
    logic                                load_accept, header_write;
    // Why: the snapshot joins two reset domains (loader: system reset; display:
    // core reset) in one clock domain, so it needs reset handling but no
    // synchronizer.
    logic                                snapshot_pin, snapshot_release, snapshot_pinned, snapshot_ready;
    n2m_snapshot_pkg::t_snapshot_request  snapshot_request;
    n2m_snapshot_pkg::t_snapshot_response snapshot_response;
    logic                                lcd_enable;
    logic [3:0]                          display_red, display_green, display_blue;
    logic                                display_hsync, display_vsync, display_pixel_start;
    logic                                display_swapped, display_dropped, display_repeated;
    logic                                display_valid, display_pinned, display_snapshot_ready;
    n2m_snapshot_pkg::t_snapshot_response display_snapshot_response;

    // Why: no boot ROM is modelled, so the core reset sweeps $00 over all of
    // VRAM, one byte per edge, and outlasts a shorter request until the sweep
    // ends (wiki/src/nand2mario/rtl/has.md#clocks-and-resets).
    logic        core_reset_request, vram_swept_q, vram_swept_d, vram_hold_q, vram_hold_d;
    logic [12:0] vram_clear_q, vram_clear_d;
    assign core_reset_request = reset_i || loader_hold || !backing_initialized || library_hold;
    assign core_reset = core_reset_request || vram_hold_q;
    assign core_reset_o = core_reset;
    assign vram_clear_d = core_reset ? vram_clear_q + 13'd1 : 13'd0;
    assign vram_swept_d = core_reset && (vram_swept_q || (vram_clear_q == '1));
    assign vram_hold_d = (core_reset_request || vram_hold_q) && !vram_swept_d;
    `DFF(vram_clear_q, vram_clear_d, clk_i)
    `DFF(vram_swept_q, vram_swept_d, clk_i)
    `DFF(vram_hold_q, vram_hold_d, clk_i)

    n2m_timebase u_timebase (
        .clk_i                  (clk_i),
        .reset_i                (core_reset),
        .dot_enable_o           (dot_enable),
        .machine_cycle_enable_o (machine_cycle_enable)
    );

    n2m_cpu u_cpu (
        .clk_i                  (clk_i),
        .reset_i                (core_reset),
        .machine_cycle_enable_i (machine_cycle_enable),
        .interrupt_enable_i     (interrupt_enable),
        .interrupt_flags_i      (interrupt_flags),
        .joyp_selected_active_i (joyp_selected_active),
        .wake_i                 (wake),
        .response_i             (cpu_response),
        .request_o              (cpu_request),
        .retirement_o           (retirement_o),
        .interrupt_ack_o        (interrupt_ack),
        .halted_o               (halted_o),
        .stopped_o              (stopped_o),
        .locked_o               (locked_o),
        .fault_o                (fault_o),
        .divider_reset_o        (divider_reset)
    );

    // Why: the DMA block sits in front of the memory map so one place decides bus
    // ownership: it forwards the CPU's requests, answers restricted ones during a
    // transfer and injects its own source reads (which reach the cartridge port
    // like any CPU read, one outstanding at a time).
    n2m_dma u_dma (
        .clk_i                  (clk_i),
        .reset_i                (core_reset),
        .machine_cycle_enable_i (machine_cycle_enable),
        .io_request_i           (io_request),
        .io_response_o          (dma_response),
        .io_selected_o          (dma_selected),
        .cpu_request_i          (cpu_request),
        .cpu_response_o         (cpu_response),
        .mem_request_o          (request),
        .mem_response_i         (response),
        .oam_write_o            (dma_oam_write),
        .oam_address_o          (dma_oam_address),
        .oam_data_o             (dma_oam_data),
        .active_o               (dma_active)
    );

    n2m_memory u_memory (
        .clk_i              (clk_i),
        .reset_i            (core_reset),
        .vram_clear_i       (core_reset),
        .vram_clear_address_i (vram_clear_q),
        .request_i          (request),
        .response_o         (response),
        .io_request_o       (io_request),
        .io_response_i      (io_response),
        .interrupt_enable_o (interrupt_enable),
        .ppu_vram_read_i    (ppu_vram_read),
        .ppu_vram_address_i (ppu_vram_address),
        .ppu_vram_data_o    (ppu_vram_data),
        .ppu_vram_valid_o   (ppu_vram_valid),
        .ppu_oam_read_i     (ppu_oam_read),
        .ppu_oam_address_i  (ppu_oam_address),
        .ppu_oam_data_o     (ppu_oam_data),
        .ppu_oam_valid_o    (ppu_oam_valid),
        .dma_oam_write_i    (dma_oam_write),
        .dma_oam_address_i  (dma_oam_address),
        .dma_oam_data_i     (dma_oam_data),
        .vram_blocked_i     (vram_blocked),
        .oam_blocked_i      (oam_blocked),
        .cart_request_o     (cart_request),
        .cart_ready_i       (cart_ready),
        .cart_response_i    (cart_response)
    );

    // Why: the mapper learns its type from the header bytes as accepted ROM load
    // writes pass into the backing, so a load needs no separate configuration step.
    assign load_accept = load_request.valid && load_ready;
    assign header_write = load_accept && load_request.write && !load_request.ram;
    n2m_cartridge u_cartridge (
        .clk_i              (clk_i),
        .reset_i            (core_reset),
        .request_i          (legacy_cart_request),
        .ready_o            (legacy_cart_ready),
        .response_o         (legacy_cart_response),
        .backing_request_o  (legacy_backing_request),
        .backing_ready_i    (backing_ready),
        .backing_response_i (legacy_backing_response),
        .load_write_i       (header_write),
        .load_address_i     (load_request.address),
        .load_data_i        (load_request.write_data)
    );

    // Why: the ordinary mapper instance and hierarchy stay unchanged for
    // existing CPU/DMA/cartridge consumers. The library configuration owns the
    // backing reader and serves CPU reads from its private active image instead.
    `ASSERT_PARAM(g_one_backing, (CART_SDRAM == 0) || (CART_EXTERNAL == 0),
        "n2m_system selects one cartridge backing: CART_SDRAM or CART_EXTERNAL")
    `ASSERT_PARAM(g_library_capacity,
        (CART_LIBRARY == 0) || (CART_SDRAM != 0) || (CART_EXTERNAL != 0) ||
        (CART_ROM_BYTES >= n2m_library_pkg::LIBRARY_ROM_BYTES),
        "library backing must hold all 64 window banks (at least 1 MiB)")
    generate
        if (CART_LIBRARY != 0) begin : g_library
            assign legacy_cart_request = '0;
            assign legacy_backing_response = '0;
            `UNUSED_BITS(unused_legacy_cartridge,
                {legacy_cart_ready, legacy_cart_response, legacy_backing_request})
            n2m_library u_library (
                .clk_i (clk_i), .reset_i (reset_i), .core_reset_i (core_reset),
                .host_hold_i (loader_hold), .backing_initialized_i (backing_initialized),
                .request_i (cart_request), .ready_o (cart_ready), .response_o (cart_response),
                .backing_request_o (backing_request), .backing_ready_i (backing_ready),
                .backing_response_i (backing_response), .hold_o (library_hold)
            );
        end else begin : g_ordinary_cartridge
            assign legacy_cart_request = cart_request;
            assign legacy_backing_response = backing_response;
            assign cart_ready = legacy_cart_ready;
            assign cart_response = legacy_cart_response;
            assign backing_request = legacy_backing_request;
            assign library_hold = 1'b0;
        end
    endgenerate

    // Why: the direct preload port wins while it presents a write; the two are
    // never used within one loader frame (asserted below), so no arbitration
    // state is needed.
    always_comb begin
        direct_request.valid = direct_valid;
        direct_request.write = 1'b1;
        direct_request.ram = 1'b0;
        direct_request.address = rom_load_address_i;
        direct_request.write_data = rom_load_data_i;
        load_request = rom_load_write_i ? direct_request : loader_request;
    end
    // Why: the direct port is a ready/valid requester of the backing's load
    // port like the loader: a direct write reaches the backing only while no load
    // is outstanding, so it presents its next write after the previous
    // response. load_outstanding_q resets with the system reset (the backing
    // drops responses then), so a preload during reset proceeds one write per
    // edge; after reset the on-chip backing takes one every second edge and
    // the SDRAM backing one per access.
    assign load_outstanding_d = load_accept || (load_outstanding_q && !load_response.valid);
    `DFF_RST(load_outstanding_q, load_outstanding_d, clk_i, reset_i)
    assign direct_valid = rom_load_write_i && !load_outstanding_q;
    assign rom_load_ready_o = load_ready && !load_outstanding_q;
    assign loader_ready = load_ready && !rom_load_write_i;

    // Why: the owner of each accepted load request is recorded on its
    // acceptance edge and only that owner sees the response. The direct preload
    // port is fire-and-forget (no response consumer). At most one load is
    // outstanding and its response arrives on a later edge, so the owner recorded
    // at acceptance is the owner of the next response.
    `DFF_RST_EN(load_owner_direct_q, rom_load_write_i, clk_i, load_accept, reset_i, 1'b0)
    always_comb begin
        loader_response = load_response;
        loader_response.valid = load_response.valid && !load_owner_direct_q;
    end

    // Why: every backing lives in the system reset domain so the loader can
    // fill it while the core is held.
    generate
        if (CART_SDRAM != 0) begin : g_sdram
            n2m_sdram_backing u_backing (
                .clk_i           (clk_i),
                .reset_i         (reset_i),
                .request_i       (backing_request),
                .ready_o         (backing_ready),
                .response_o      (backing_response),
                .load_request_i  (load_request),
                .load_ready_o    (load_ready),
                .load_response_o (load_response),
                .initialized_o   (backing_initialized),
                .dram_addr_o     (dram_addr_o),
                .dram_ba_o       (dram_ba_o),
                .dram_cas_n_o    (dram_cas_n_o),
                .dram_cke_o      (dram_cke_o),
                .dram_clk_o      (dram_clk_o),
                .dram_cs_n_o     (dram_cs_n_o),
                .dram_dqml_o     (dram_dqml_o),
                .dram_dqmh_o     (dram_dqmh_o),
                .dram_ras_n_o    (dram_ras_n_o),
                .dram_we_n_o     (dram_we_n_o),
                .dram_dq_o       (dram_dq_o),
                .dram_dq_oe_o    (dram_dq_oe_o),
                .dram_dq_i       (dram_dq_i)
            );
            assign cart_bus_request_o = '0;
        end else if (CART_EXTERNAL != 0) begin : g_external
            // Why: the ASIC chip keeps the cartridge off-chip, and the mapper must see
            // the on-chip backing's timing from a device that is always ready and answers
            // on the next edge. So the mapper's backing port and the load port share the
            // cartridge bus through the SDRAM backing's arbiter (the mapper first), and the
            // bus carries the backing records unchanged: a request is held until sampled
            // with cart_bus_ready_i, exactly one response follows on a later edge, one
            // request is outstanding and the next is presented after the edge that
            // sampled the response. Each response goes to the owner recorded at
            // acceptance; the system reset clears the outstanding flag, so a response
            // then is dropped.
            logic ext_outstanding_q, ext_outstanding_d, ext_owner_load_q;
            logic ext_accept, ext_response_valid;
            assign backing_ready = !ext_outstanding_q && cart_bus_ready_i;
            assign load_ready = backing_ready && !backing_request.valid;
            always_comb begin
                cart_bus_request_o = backing_request.valid ? backing_request : load_request;
                cart_bus_request_o.valid = !ext_outstanding_q && (backing_request.valid || load_request.valid);
            end
            assign ext_accept = cart_bus_request_o.valid && cart_bus_ready_i;
            assign ext_response_valid = ext_outstanding_q && cart_bus_response_i.valid;
            assign ext_outstanding_d = ext_accept || (ext_outstanding_q && !cart_bus_response_i.valid);
            `DFF_RST(ext_outstanding_q, ext_outstanding_d, clk_i, reset_i)
            `DFF_RST_EN(ext_owner_load_q, !backing_request.valid, clk_i, ext_accept, reset_i, 1'b0)
            assign backing_initialized = 1'b1;
            always_comb begin
                backing_response.valid = ext_response_valid && !ext_owner_load_q;
                backing_response.read_data = cart_bus_response_i.read_data;
                load_response.valid = ext_response_valid && ext_owner_load_q;
                load_response.read_data = cart_bus_response_i.read_data;
            end
            `ASSERT_KNOWN(cart_bus_ready_known, clk_i, reset_i, cart_bus_ready_i)
            `ASSERT_KNOWN(cart_bus_response_valid_known, clk_i, reset_i, cart_bus_response_i.valid)
            `ASSERT(cart_bus_response_requested, clk_i, reset_i,
                !cart_bus_response_i.valid || ext_outstanding_q)
            `ASSERT_NEVER(cart_bus_rom_write_rejected, clk_i, reset_i,
                backing_request.valid && !backing_request.ram && backing_request.write)
            assign dram_addr_o = '0;
            assign dram_ba_o = '0;
            assign dram_cas_n_o = 1'b1;
            assign dram_cke_o = 1'b0;
            assign dram_clk_o = 1'b0;
            assign dram_cs_n_o = 1'b1;
            assign dram_dqml_o = 1'b1;
            assign dram_dqmh_o = 1'b1;
            assign dram_ras_n_o = 1'b1;
            assign dram_we_n_o = 1'b1;
            assign dram_dq_o = '0;
            assign dram_dq_oe_o = 1'b0;
            /* verilator lint_off UNUSEDSIGNAL */
            logic [15:0] unused_dram_dq;
            assign unused_dram_dq = dram_dq_i;
            /* verilator lint_on UNUSEDSIGNAL */
        end else begin : g_onchip
            n2m_cartridge_backing #(
                .ROM_BYTES (CART_ROM_BYTES),
                .RAM_BYTES (CART_RAM_BYTES)
            ) u_backing (
                .clk_i           (clk_i),
                .reset_i         (reset_i),
                .request_i       (backing_request),
                .ready_o         (backing_ready),
                .response_o      (backing_response),
                .load_request_i  (load_request),
                .load_ready_o    (load_ready),
                .load_response_o (load_response),
                .initialized_o   (backing_initialized)
            );
            assign dram_addr_o = '0;
            assign dram_ba_o = '0;
            assign dram_cas_n_o = 1'b1;
            assign dram_cke_o = 1'b0;
            assign dram_clk_o = 1'b0;
            assign dram_cs_n_o = 1'b1;
            assign dram_dqml_o = 1'b1;
            assign dram_dqmh_o = 1'b1;
            assign dram_ras_n_o = 1'b1;
            assign dram_we_n_o = 1'b1;
            assign dram_dq_o = '0;
            assign dram_dq_oe_o = 1'b0;
            /* verilator lint_off UNUSEDSIGNAL */
            logic [15:0] unused_dram_dq;
            assign unused_dram_dq = dram_dq_i;
            /* verilator lint_on UNUSEDSIGNAL */
            assign cart_bus_request_o = '0;
        end
    endgenerate

    // Why: the UART link runs in the system reset domain because it holds the
    // core in reset while it loads.
    n2m_uart_link #(
        .CLKS_PER_BIT   (UART_CLKS_PER_BIT),
        .FRAC_NUM       (UART_FRAC_NUM),
        .FRAC_DEN       (UART_FRAC_DEN),
        .CART_ROM_BYTES (CART_ROM_BYTES),
        .CART_RAM_BYTES (CART_RAM_BYTES)
    ) u_link (
        .clk_i                 (clk_i),
        .reset_i               (reset_i),
        .uart_rx_i             (uart_rx_i),
        .uart_tx_o             (uart_tx_o),
        .hold_o                (loader_hold),
        .backing_initialized_i (backing_initialized),
        .load_request_o        (loader_request),
        .load_ready_i          (loader_ready),
        .load_response_i       (loader_response),
        .load_busy_o           (loader_busy),
        .snapshot_pin_o        (snapshot_pin),
        .snapshot_release_o    (snapshot_release),
        .snapshot_pinned_i     (snapshot_pinned),
        .snapshot_available_i  (display_valid_o),
        .snapshot_request_o    (snapshot_request),
        .snapshot_ready_i      (snapshot_ready),
        .snapshot_response_i   (snapshot_response)
    );

    n2m_timer u_timer (
        .clk_i                  (clk_i),
        .reset_i                (core_reset),
        .machine_cycle_enable_i (machine_cycle_enable),
        .divider_reset_i        (divider_reset),
        .io_request_i           (io_request),
        .io_response_o          (timer_response),
        .io_selected_o          (timer_selected),
        .interrupt_o            (timer_interrupt),
        .sequencer_bit_o        (apu_sequencer_bit),
        .serial_clock_o         (serial_clock)
    );

    n2m_joypad u_joypad (
        .clk_i             (clk_i),
        .reset_i           (core_reset),
        .buttons_i         (buttons_i),
        .io_request_i      (io_request),
        .io_response_o     (joypad_response),
        .io_selected_o     (joypad_selected),
        .selected_active_o (joyp_selected_active),
        .interrupt_o       (joypad_interrupt),
        .wake_o            (wake)
    );

    n2m_serial u_serial (
        .clk_i                  (clk_i),
        .reset_i                (core_reset),
        .shift_clock_i          (serial_clock),
        .io_request_i           (io_request),
        .io_response_o          (serial_response),
        .io_selected_o          (serial_selected),
        .interrupt_o            (serial_interrupt),
        .tx_valid_o             (serial_tx_valid_o),
        .tx_data_o              (serial_tx_data_o)
    );

    // Ref: Pan Docs "Interrupts": IF bit order VBlank, STAT, timer, serial,
    // joypad. Each source is a one-cycle pulse per event.
    assign interrupt_sources = {joypad_interrupt, serial_interrupt, timer_interrupt,
                                stat_interrupt, vblank_interrupt};

    n2m_apu u_apu (
        .clk_i           (clk_i),
        .reset_i         (core_reset),
        .sequencer_bit_i (apu_sequencer_bit),
        .io_request_i  (io_request),
        .io_response_o (apu_response),
        .io_selected_o (apu_selected)
    );

    n2m_interrupts u_interrupts (
        .clk_i             (clk_i),
        .reset_i           (core_reset),
        .io_request_i      (io_request),
        .io_response_o     (interrupts_response),
        .io_selected_o     (interrupts_selected),
        .source_set_i      (interrupt_sources),
        .interrupt_ack_i   (interrupt_ack),
        .interrupt_flags_o (interrupt_flags)
    );

    // Why: the raster observation ports are consumed by the focused video target.
    /* verilator lint_off PINCONNECTEMPTY */
    n2m_ppu u_ppu (
        .clk_i          (clk_i),
        .reset_i        (core_reset),
        .dot_enable_i   (dot_enable),
        .io_request_i   (io_request),
        .io_response_o  (ppu_response),
        .io_selected_o  (ppu_selected),
        .vram_read_o    (ppu_vram_read),
        .vram_address_o (ppu_vram_address),
        .vram_valid_i   (ppu_vram_valid),
        .vram_data_i    (ppu_vram_data),
        .oam_read_o     (ppu_oam_read),
        .oam_address_o  (ppu_oam_address),
        .oam_valid_i    (ppu_oam_valid),
        .oam_data_i     (ppu_oam_data),
        .vram_blocked_o (vram_blocked),
        .oam_blocked_o  (oam_blocked),
        .vblank_irq_o   (vblank_interrupt),
        .stat_irq_o     (stat_interrupt),
        .pixel_valid_o  (pixel_valid_o),
        .pixel_shade_o  (pixel_shade_o),
        .pixel_x_o      (pixel_x_o),
        .pixel_y_o      (pixel_y_o),
        .line_start_o   (line_start_o),
        .frame_start_o  (frame_start_o),
        .lcd_enable_o   (lcd_enable),
        .dot_o          (),
        .ly_o           (),
        .mode_o         ()
    );
    /* verilator lint_on PINCONNECTEMPTY */

    // Why: the frame-completion pulse is consumed by the focused display target.
    /* verilator lint_off PINCONNECTEMPTY */
    n2m_display u_display (
        .clk_i            (clk_i),
        .reset_i          (core_reset),
        .lcd_enable_i     (lcd_enable),
        .pixel_valid_i    (pixel_valid_o),
        .pixel_shade_i    (pixel_shade_o),
        .pixel_x_i        (pixel_x_o),
        .pixel_y_i        (pixel_y_o),
        .vga_red_o        (display_red),
        .vga_green_o      (display_green),
        .vga_blue_o       (display_blue),
        .vga_hsync_o      (display_hsync),
        .vga_vsync_o      (display_vsync),
        .pixel_start_o    (display_pixel_start),
        .frame_complete_o (),
        .frame_swapped_o  (display_swapped),
        .frame_dropped_o  (display_dropped),
        .frame_repeated_o (display_repeated),
        .display_valid_o  (display_valid),
        .snapshot_pin_i      (snapshot_pin),
        .snapshot_release_i  (snapshot_release),
        .snapshot_pinned_o   (display_pinned),
        .snapshot_request_i  (snapshot_request),
        .snapshot_ready_o    (display_snapshot_ready),
        .snapshot_response_o (display_snapshot_response)
    );
    /* verilator lint_on PINCONNECTEMPTY */

    // DISPLAY selection: without the display tier the VGA pins are parked
    // (syncs inactive high), no frame is ever valid or pinned and the loader
    // answers snapshot commands with its no-frame/not-pinned status. Nothing
    // then consumes the tier, so synthesis removes it.
    localparam logic DISPLAY_ON = DISPLAY != 0;
    assign vga_red_o = DISPLAY_ON ? display_red : '0;
    assign vga_green_o = DISPLAY_ON ? display_green : '0;
    assign vga_blue_o = DISPLAY_ON ? display_blue : '0;
    assign vga_hsync_o = DISPLAY_ON ? display_hsync : 1'b1;
    assign vga_vsync_o = DISPLAY_ON ? display_vsync : 1'b1;
    assign pixel_start_o = DISPLAY_ON && display_pixel_start;
    assign frame_swapped_o = DISPLAY_ON && display_swapped;
    assign frame_dropped_o = DISPLAY_ON && display_dropped;
    assign frame_repeated_o = DISPLAY_ON && display_repeated;
    assign display_valid_o = DISPLAY_ON && display_valid;
    assign snapshot_pinned = DISPLAY_ON && display_pinned;
    assign snapshot_ready = DISPLAY_ON && display_snapshot_ready;
    always_comb begin
        snapshot_response = display_snapshot_response;
        snapshot_response.valid = DISPLAY_ON && display_snapshot_response.valid;
    end

    // Why: an unclaimed address must read open-bus $FF and ignore writes as on
    // the DMG, so the fan-out defaults to that; the claiming block answers in the
    // same cycle.
    always_comb begin
        io_response.valid = io_request.valid;
        io_response.fault = 1'b0;
        io_response.read_data = n2m_memory_pkg::OPEN_BUS_DATA;
        if (timer_selected) io_response = timer_response;
        else if (interrupts_selected) io_response = interrupts_response;
        else if (ppu_selected) io_response = ppu_response;
        else if (joypad_selected) io_response = joypad_response;
        else if (serial_selected) io_response = serial_response;
        else if (dma_selected) io_response = dma_response;
        else if (apu_selected) io_response = apu_response;
    end

    // Why: the transfer level is observed by the system tests only.
    logic unused_dma_active;
    assign unused_dma_active = dma_active;

    `ASSERT(system_io_claim_exclusive, clk_i, core_reset,
        $onehot0({timer_selected, interrupts_selected, ppu_selected, joypad_selected,
                  serial_selected, dma_selected, apu_selected}))
    `ASSERT_NEVER(system_load_ports_exclusive, clk_i, reset_i,
        rom_load_write_i && (loader_request.valid || loader_busy))
    `ASSERT(system_load_response_requested, clk_i, reset_i,
        !load_response.valid || load_outstanding_q)
    `ASSERT_NEVER(system_load_one_outstanding, clk_i, reset_i,
        load_accept && load_outstanding_q)
endmodule
/* verilator lint_on SYNCASYNCNET */
