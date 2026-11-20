// Common UART memory endpoint package: wire constants, pure CRC helpers and
// the endpoint's state record (wiki/src/common/uart_mem/HAS.md). No state,
// no timing.
package n2a_uart_mem_pkg;
    // Framing (HAS "Framing").
    localparam logic [7:0] UART_MEM_REQUEST_SOF  = 8'ha8;
    localparam logic [7:0] UART_MEM_RESPONSE_SOF = 8'h8a;
    localparam logic [7:0] UART_MEM_VERSION      = 8'h01;
    // Identify word 0: the bytes "N2AM" on the wire.
    localparam logic [31:0] UART_MEM_MAGIC       = 32'h4d41_324e;
    // Identify words before the window descriptors.
    localparam int unsigned UART_MEM_IDENT_FIXED_WORDS = 5;
    // Bits per window descriptor: {attributes, size, base}, 32 bits each.
    localparam int unsigned UART_MEM_WINDOW_BITS = 96;

    // Commands.
    localparam logic [7:0] UART_MEM_CMD_IDENTIFY = 8'h01;
    localparam logic [7:0] UART_MEM_CMD_HOLD     = 8'h02;
    localparam logic [7:0] UART_MEM_CMD_RELEASE  = 8'h03;
    localparam logic [7:0] UART_MEM_CMD_ENTRY    = 8'h04;
    localparam logic [7:0] UART_MEM_CMD_WRITE    = 8'h10;
    localparam logic [7:0] UART_MEM_CMD_READ     = 8'h11;

    // Statuses.
    localparam logic [7:0] UART_MEM_ST_OK        = 8'h00;
    localparam logic [7:0] UART_MEM_ST_VERSION   = 8'h01;
    localparam logic [7:0] UART_MEM_ST_COMMAND   = 8'h02;
    localparam logic [7:0] UART_MEM_ST_CHECKSUM  = 8'h03;
    localparam logic [7:0] UART_MEM_ST_ALIGNMENT = 8'h04;
    localparam logic [7:0] UART_MEM_ST_RANGE     = 8'h05;
    localparam logic [7:0] UART_MEM_ST_LENGTH    = 8'h06;
    localparam logic [7:0] UART_MEM_ST_NOT_HELD  = 8'h07;
    localparam logic [7:0] UART_MEM_ST_TIMEOUT   = 8'h08;
    localparam logic [7:0] UART_MEM_ST_MEMORY    = 8'h09;
    localparam logic [7:0] UART_MEM_ST_OVERRUN   = 8'h0a;

    // Window attribute bits.
    localparam logic [31:0] UART_MEM_ATTR_WRITE     = 32'h1;
    localparam logic [31:0] UART_MEM_ATTR_READ_HELD = 32'h2;
    localparam logic [31:0] UART_MEM_ATTR_READ_LIVE = 32'h4;

    // CRC-8/ATM step: polynomial 0x07, no reflection; the caller starts at 0.
    function automatic logic [7:0] uart_mem_crc8(input logic [7:0] crc,
                                                 input logic [7:0] data);
        logic [7:0] value;
        value = crc ^ data;
        for (int bit_index = 0; bit_index < 8; bit_index++)
            value = value[7] ? ((value << 1) ^ 8'h07) : (value << 1);
        return value;
    endfunction

    // Reflected CRC-32 (IEEE 0xedb88320) step with no inversion; the caller
    // starts at 0xffffffff and inverts the result, as zlib.crc32 does.
    function automatic logic [31:0] uart_mem_crc32(input logic [31:0] crc,
                                                   input logic [7:0] data);
        logic [31:0] value;
        value = crc ^ {24'h0, data};
        for (int bit_index = 0; bit_index < 8; bit_index++)
            value = value[0] ? ((value >> 1) ^ 32'hedb8_8320) : (value >> 1);
        return value;
    endfunction

    // Endpoint link phase.
    typedef enum logic [2:0] {
        UART_MEM_IDLE,     // not owning the link: bytes pass to the project
        UART_MEM_HEADER,   // request bytes 1..10
        UART_MEM_PAYLOAD,  // write payload bytes
        UART_MEM_PCRC,     // four payload CRC-32 bytes
        UART_MEM_DISCARD,  // owning; consume until the line is quiet
        UART_MEM_EXEC,     // command execution
        UART_MEM_RESPOND   // response bytes
    } uart_mem_phase_t;

    // Endpoint state. Timers are separate registers in the module.
    typedef struct packed {
        uart_mem_phase_t phase;
        logic [15:0] index;        // header, payload or response byte index
        logic [7:0]  version;
        logic [7:0]  command;
        logic [7:0]  seq;
        logic [31:0] address;
        logic [15:0] length;
        logic [7:0]  crc8;
        logic [31:0] crc32;        // running payload (RX) or response (TX) CRC
        logic [31:0] rx_crc;       // received payload CRC-32
        logic [7:0]  status;
        logic        reject;       // header rejected: no memory effect
        logic [15:0] rsp_length;
        logic [31:0] word;         // write payload word assembly
        logic        hold;
        logic        release_pulse;
        logic        entry_pulse;
        logic [31:0] entry_addr;
        logic        req_valid;
        logic        req_write;
        logic [31:0] req_addr;
        logic [31:0] req_wdata;
        logic        outstanding;  // accepted request awaiting its response
        logic        out_write;
        logic        orphan;       // the outstanding request has timed out
        logic        mem_failed;   // memory error or timeout in this frame
        logic [15:0] issued;       // read bytes requested
        logic [31:0] cur_word;     // read word being transmitted
        logic        cur_valid;
        logic [31:0] nxt_word;     // prefetched read word
        logic        nxt_valid;
    } uart_mem_regs_t;
endpackage
