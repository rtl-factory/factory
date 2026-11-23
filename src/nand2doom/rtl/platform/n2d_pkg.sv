// nand2doom project package: debug-interface port structs, host debug
// protocol constants and pure combinational helpers shared by the UART
// debug engine, bulk framing and the platform. No state, no timing.
package n2d_pkg;

    // Why: each n2d_uart_debug / n2d_uart_debug_engine port-group struct holds
    // one direction only; the ready of the input-event request stays a
    // scalar input because it is the only opposite-direction signal.

    // Engine -> platform control (halt request level, resume invalidation
    // pulse, reset-PC pulse with its value).
    typedef struct packed {
        logic        halt_req;
        logic        cache_invalidate;
        logic        reset_pc_valid;
        logic [31:0] reset_pc;
    } t_n2d_debug_control;

    // Platform -> engine status (halt acknowledged, cache maintenance done).
    typedef struct packed {
        logic halt_ack;
        logic cache_maint_done;
    } t_n2d_debug_status;

    // Engine -> keyboard injected Set-2 event request (valid level, code).
    typedef struct packed {
        logic       valid;
        logic [9:0] code;
    } t_n2d_input_event;

    // Engine -> memory arbitration request level and transaction pulses.
    typedef struct packed {
        logic arb_req;
        logic txn_begin;
        logic txn_commit;
        logic txn_abort;
    } t_n2d_debug_txn_req;

    // Memory -> engine arbitration grant level and transaction completion.
    typedef struct packed {
        logic arb_grant;
        logic txn_done;
        logic txn_error;
    } t_n2d_debug_txn_status;

    // Engine -> memory word request.
    typedef struct packed {
        logic        valid;
        logic        write;
        logic [31:0] addr;
        logic [31:0] wdata;
        logic [3:0]  wstrb;
    } t_n2d_debug_mem_req;

    // Memory -> engine request handshake and word response.
    typedef struct packed {
        logic        ready;
        logic        rsp_valid;
        logic [31:0] rdata;
        logic        rsp_error;
    } t_n2d_debug_mem_rsp;

    // n2d_uart_debug -> platform screen read of the UART memory endpoint:
    // the front framebuffer bank or the scanned palette, by byte offset.
    typedef struct packed {
        logic        valid;
        logic        palette;
        logic [15:0] offset;
    } t_n2d_screen_req;

    // Platform -> n2d_uart_debug screen request handshake and next-clock data.
    typedef struct packed {
        logic        ready;
        logic        rsp_valid;
        logic [31:0] rdata;
    } t_n2d_screen_rsp;

    // UART memory endpoint map (wiki/src/nand2doom/rtl/io/mas.md "UART memory endpoint"): SDRAM is
    // loaded and read while held; the front framebuffer view and the scanned
    // palette view are read while the CPU runs. Windows are {attributes,
    // size, base}, window 0 lowest. Attributes: 1 write, 2 held read, 4 live
    // read (n2a_uart_mem_pkg). The bulk-router fixture elaborates this
    // package without n2d_uart_debug, so these are exempt from the
    // unused-parameter rule, like the engine-only constants below.
    /* verilator lint_off UNUSEDPARAM */
    // N2D_MEM_PROJECT_ID is ASCII "n2d ", first character in the low byte.
    localparam logic [31:0] N2D_MEM_PROJECT_ID     = 32'h2064_326e;
    localparam logic [31:0] N2D_MEM_SCREEN_BASE    = 32'h1002_0000;
    localparam logic [31:0] N2D_MEM_PALETTE_BASE   = 32'h1003_0000;
    localparam logic [287:0] N2D_MEM_WINDOWS = {
        32'h6, 32'h0000_0400, N2D_MEM_PALETTE_BASE,
        32'h6, 32'h0000_fa00, N2D_MEM_SCREEN_BASE,
        32'h3, 32'h0400_0000, 32'h0000_0000};
    localparam int unsigned N2D_MEM_LIVE_GAP_CYCLES = 64;
    /* verilator lint_on UNUSEDPARAM */

    // nand2doom's configuration of the common SDRAM controller (n2a_sdram_ctrl,
    // wiki/src/common/rtl/sdram/mas.md "project configurations"): burst eight,
    // open row and the row-XOR-bank map keep the line latency. The memory
    // controller and every bench fixture take the parameters from here.
    // Why: compositions on the fast line model (n2d_perf_fast and the like)
    // elaborate this package without the controller and read none of these.
    /* verilator lint_off UNUSEDPARAM */
    localparam int unsigned N2D_SDRAM_BURST_BEATS           = 8;
    localparam bit          N2D_SDRAM_OPEN_ROW              = 1'b1;
    localparam bit          N2D_SDRAM_ROW_XOR_BANK          = 1'b1;
    localparam int unsigned N2D_SDRAM_POWERUP_CLOCKS        = 5000;
    localparam int unsigned N2D_SDRAM_INIT_REFRESHES        = 2;
    localparam int unsigned N2D_SDRAM_INIT_WAIT_MIN         = 2;
    localparam int unsigned N2D_SDRAM_WAIT_RCD              = 0;
    localparam int unsigned N2D_SDRAM_WAIT_RP               = 0;
    localparam int unsigned N2D_SDRAM_WAIT_RFC              = 0;
    // Why: open row never waits after a write beat; 1 is the controller default.
    localparam int unsigned N2D_SDRAM_WAIT_WR               = 1;
    localparam int unsigned N2D_SDRAM_WAIT_MRD              = 0;
    localparam int unsigned N2D_SDRAM_REFRESH_DUE           = 163;
    localparam int unsigned N2D_SDRAM_REFRESH_DEADLINE      = 194;
    localparam bit          N2D_SDRAM_REFRESH_AGE_FROM_MODE = 1'b0;
    /* verilator lint_on UNUSEDPARAM */

    // ---------------------------------------------------------------------
    // Host debug protocol constants (wiki/src/nand2doom/rtl/io/mas.md). Compositions that elaborate
    // n2d_uart_bulk without the debug engine (the bulk-router fixture) use
    // none of the engine-only framing/status values, so they are exempt from
    // the unused-parameter rule here; their values are still checked by the
    // engine's own tests.
    // ---------------------------------------------------------------------
    /* verilator lint_off UNUSEDPARAM */
    localparam logic [7:0] N2D_DEBUG_REQUEST_SOF  = 8'ha5;
    localparam logic [7:0] N2D_DEBUG_RESPONSE_SOF = 8'h5a;
    localparam logic [7:0] N2D_DEBUG_VERSION      = 8'h01;

    localparam logic [7:0] N2D_CMD_PING     = 8'h01;
    localparam logic [7:0] N2D_CMD_HALT     = 8'h02;
    localparam logic [7:0] N2D_CMD_RESUME   = 8'h03;
    localparam logic [7:0] N2D_CMD_RESET_PC = 8'h04;
    localparam logic [7:0] N2D_CMD_CAPS     = 8'h05;
    localparam logic [7:0] N2D_CMD_PEEK     = 8'h10;
    localparam logic [7:0] N2D_CMD_POKE     = 8'h11;
    localparam logic [7:0] N2D_CMD_LOAD     = 8'h12;
    localparam logic [7:0] N2D_CMD_INPUT    = 8'h20;
    localparam logic [31:0] N2D_DEBUG_INPUT_ADDRESS = 32'h2000_200c;

    localparam logic [7:0] N2D_ST_OK          = 8'h00;
    localparam logic [7:0] N2D_ST_VERSION     = 8'h01;
    localparam logic [7:0] N2D_ST_COMMAND     = 8'h02;
    localparam logic [7:0] N2D_ST_CHECKSUM    = 8'h03;
    localparam logic [7:0] N2D_ST_ALIGNMENT   = 8'h04;
    localparam logic [7:0] N2D_ST_RANGE       = 8'h05;
    localparam logic [7:0] N2D_ST_LENGTH      = 8'h06;
    localparam logic [7:0] N2D_ST_NOT_HALTED  = 8'h07;
    localparam logic [7:0] N2D_ST_TIMEOUT     = 8'h08;
    localparam logic [7:0] N2D_ST_MEMORY      = 8'h09;
    localparam logic [7:0] N2D_ST_FRAMING     = 8'h0a;
    localparam logic [7:0] N2D_ST_GRANT       = 8'h0b;
    localparam logic [7:0] N2D_ST_BUSY        = 8'h0c;
    /* verilator lint_on UNUSEDPARAM */

    // CRC-8, polynomial 0x07, MSB first, one byte per call.
    function automatic logic [7:0] n2d_crc8_byte(
        input logic [7:0] crc, input logic [7:0] data
    );
        logic [7:0] value;
        value = crc ^ data;
        for (int i = 0; i < 8; i++)
            value = value[7] ? (value << 1) ^ 8'h07 : value << 1;
        return value;
    endfunction

    // Reflected CRC-32 (0xEDB88320), one byte per call; caller inverts.
    function automatic logic [31:0] n2d_crc32_byte(
        input logic [31:0] crc, input logic [7:0] data
    );
        logic [31:0] value;
        value = crc ^ {24'b0, data};
        for (int i = 0; i < 8; i++)
            value = value[0] ? (value >> 1) ^ 32'hedb8_8320 : value >> 1;
        return value;
    endfunction

    // Bulk transfer window: SDRAM for both directions, framebuffer+palette
    // shadow window for READ only.
    function automatic logic n2d_bulk_range_valid(
        input logic [31:0] address, input logic [15:0] length,
        input logic is_read
    );
        logic [32:0] end_address;
        end_address = {1'b0, address} + {17'b0, length};
        return ((address < 32'h0400_0000) &&
                (end_address <= 33'h00400_0000)) ||
               (is_read && (address >= 32'h1000_0000) &&
                (end_address <= 33'h01001_0000));
    endfunction

    // Word-burst window for PEEK/POKE/LOAD: SDRAM, framebuffer or palette.
    function automatic logic n2d_debug_address_range_valid(
        input logic [31:0] address,
        input logic [7:0] count
    );
        logic [32:0] last_address;
        logic in_sdram;
        logic in_framebuffer;
        logic in_palette;
        last_address = {1'b0, address} + ({25'b0, count} << 2) - 1'b1;
        in_sdram = (address <= 32'h03ff_fffc) &&
                   (last_address <= 33'h0_03ff_ffff);
        in_framebuffer = (address >= 32'h1000_0000) &&
                         (last_address <= 33'h0_1000_ffff);
        in_palette = (address >= 32'h1001_0000) &&
                     (last_address <= 33'h0_1001_0fff);
        return (count != 0) && (in_sdram || in_framebuffer || in_palette);
    endfunction

    function automatic logic n2d_debug_command_supported(input logic [7:0] command);
        return (command == N2D_CMD_PING) || (command == N2D_CMD_HALT) ||
               (command == N2D_CMD_RESUME) || (command == N2D_CMD_RESET_PC) ||
               (command == N2D_CMD_PEEK) || (command == N2D_CMD_POKE) ||
               (command == N2D_CMD_LOAD) || (command == N2D_CMD_INPUT) ||
               (command == N2D_CMD_CAPS);
    endfunction

    function automatic logic n2d_debug_command_has_payload(input logic [7:0] command);
        return (command == N2D_CMD_POKE) || (command == N2D_CMD_LOAD) ||
               (command == N2D_CMD_INPUT);
    endfunction

    // ---------------------------------------------------------------------
    // Streaming image-load session (wiki/src/nand2doom/rtl/io/mas.md "Streaming load session").
    // Compositions without n2d_uart_stream (the bulk-router fixture) use none
    // of these values, so they share the unused-parameter exemption above.
    // ---------------------------------------------------------------------
    /* verilator lint_off UNUSEDPARAM */
    localparam logic [7:0]  N2D_STREAM_REQUEST_SOF  = 8'ha7;
    localparam logic [7:0]  N2D_STREAM_RESPONSE_SOF = 8'h7a;
    localparam logic [7:0]  N2D_STREAM_HELLO        = 8'h48;
    localparam logic [7:0]  N2D_STREAM_WRITE        = 8'h57;
    localparam logic [7:0]  N2D_STREAM_DIGEST       = 8'h56;
    localparam logic [7:0]  N2D_STREAM_END          = 8'h45;
    localparam logic [31:0] N2D_STREAM_IDENTITY     = 32'h3153_324e;
    localparam logic [31:0] N2D_STREAM_MAX_PAYLOAD  = 32'd1024;
    localparam logic [31:0] N2D_SDRAM_BYTES         = 32'h0400_0000;
    // Debug UART link rates (wiki/src/nand2doom/rtl/io/mas.md "Link rate"). The bit period is
    // N2D_SYSTEM_CLOCK_HZ / baud clocks. The default link of the simulation
    // fixtures is 1 Mbaud (25 clocks); both board tops run the factory board
    // rate, 2 Mbaud on every board and project (12 1/2 clocks through the
    // PHY's fractional accumulator).
    localparam int unsigned N2D_SYSTEM_CLOCK_HZ     = 25_000_000;
    localparam int unsigned N2D_UART_BAUD           = 1_000_000;
    localparam int unsigned N2D_UART_BOARD_BAUD     = 2_000_000;
    /* verilator lint_on UNUSEDPARAM */

    // Stream write/digest window: SDRAM only, 33-bit end arithmetic.
    function automatic logic n2d_stream_range_valid(
        input logic [31:0] address, input logic [31:0] length
    );
        logic [32:0] end_address;
        end_address = {1'b0, address} + {1'b0, length};
        return (address < N2D_SDRAM_BYTES) && (end_address <= {1'b0, N2D_SDRAM_BYTES});
    endfunction

    // Reflected CRC-32 continued over the low `count` (1..4) little-endian
    // bytes of `word`; caller owns the initial value and final inversion.
    function automatic logic [31:0] n2d_crc32_word(
        input logic [31:0] crc, input logic [31:0] word, input logic [2:0] count
    );
        logic [31:0] value;
        value = crc;
        for (int lane = 0; lane < 4; lane++)
            if (3'(lane) < count) value = n2d_crc32_byte(value, word[8*lane +: 8]);
        return value;
    endfunction

endpackage
