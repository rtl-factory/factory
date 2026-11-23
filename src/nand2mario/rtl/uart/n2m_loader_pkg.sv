`default_nettype none

// nand2mario UART cartridge loader protocol facts: frame layout, command and
// status encodings and the CRC-16 helper shared by the loader RTL, its
// verification model and the host tool (wiki/src/nand2mario/scripts/board/spec.md#loader-protocol).
// The package holds facts and pure helpers only; n2m_uart_loader owns the
// behavior.
package n2m_loader_pkg;
    /* verilator lint_off UNUSEDPARAM */
    // Every frame starts with the sync byte; the CRC-16/CCITT-FALSE (poly
    // $1021, init $FFFF, no reflection, no final XOR) covers every byte after
    // it and precedes nothing: the frame ends with the CRC low then high byte.
    localparam logic [7:0]  SYNC_BYTE = 8'hA5;
    localparam logic [15:0] CRC_INIT = 16'hFFFF;
    localparam logic [15:0] CRC_POLYNOMIAL = 16'h1021;

    // Request: SYNC, command, address[7:0], address[15:8], address[23:16],
    // length-1, payload (WRITE only, length bytes), CRC lo, CRC hi.
    // Response: SYNC, status, command, address (3), length-1, payload (READ
    // with status OK only), CRC lo, CRC hi; the CRC covers status onward.
    localparam logic [7:0] CMD_PING    = 8'h01;
    localparam logic [7:0] CMD_HOLD    = 8'h02;
    localparam logic [7:0] CMD_RELEASE = 8'h03;
    localparam logic [7:0] CMD_WRITE   = 8'h04;
    localparam logic [7:0] CMD_READ    = 8'h05;
    // Frame snapshot (display HAS): pin the displayed frame, release it, read
    // its record; none needs the hold.
    localparam logic [7:0] CMD_SNAP_PIN     = 8'h06;
    localparam logic [7:0] CMD_SNAP_RELEASE = 8'h07;
    localparam logic [7:0] CMD_SNAP_READ    = 8'h08;

    // Address field: bits [20:0] the byte offset, bit 21 selects cartridge RAM
    // (offset below 32 KiB) instead of ROM, bits [23:22] must be zero.
    localparam int unsigned ADDRESS_RAM_BIT = 21;
    localparam int unsigned MAX_PAYLOAD_BYTES = 256;

    // Common UART memory endpoint map (n2m_uart_link): the same address field
    // as the loader's frames, so ROM at 0 and cartridge RAM at bit 21. Both
    // windows are written and read while held. N2M_MEM_PROJECT_ID is ASCII
    // "n2m ", first character in the low byte; entry is the cartridge entry
    // point the boot ROM jumps to.
    localparam logic [31:0] N2M_MEM_PROJECT_ID        = 32'h206d_326e;
    localparam logic [31:0] N2M_MEM_ROM_BASE          = 32'h0000_0000;
    localparam logic [31:0] N2M_MEM_RAM_BASE          = 32'h0020_0000;
    localparam logic [31:0] N2M_MEM_WINDOW_ATTRIBUTES = 32'h3;
    localparam logic [31:0] N2M_MEM_ENTRY             = 32'h0000_0100;

    // Status byte: {held, backing initialized, framing seen, timeout seen,
    // overrun seen, code[2:0]}. The three sticky flags report events since the
    // previous response and clear when a response carrying them is sent.
    localparam int unsigned STATUS_HELD_BIT = 7;
    localparam int unsigned STATUS_INITIALIZED_BIT = 6;
    localparam int unsigned STATUS_FRAMING_BIT = 5;
    localparam int unsigned STATUS_TIMEOUT_BIT = 4;
    localparam int unsigned STATUS_OVERRUN_BIT = 3;
    localparam logic [2:0] STATUS_OK           = 3'd0;
    localparam logic [2:0] STATUS_BAD_CRC      = 3'd1;
    localparam logic [2:0] STATUS_BAD_COMMAND  = 3'd2;
    localparam logic [2:0] STATUS_BAD_ARGUMENT = 3'd3;
    localparam logic [2:0] STATUS_NOT_HELD     = 3'd4;
    localparam logic [2:0] STATUS_BAD_ADDRESS  = 3'd5;
    localparam logic [2:0] STATUS_NO_FRAME     = 3'd6;
    localparam logic [2:0] STATUS_NOT_PINNED   = 3'd7;
    /* verilator lint_on UNUSEDPARAM */

    // Loader frame/commit/response sequencer states (wiki/src/nand2mario/scripts/board/spec.md#loader-protocol).
    typedef enum logic [4:0] {
        IDLE, CMD, ADDR0, ADDR1, ADDR2, LEN, PAYLOAD, CRC_LO, CRC_HI, DECIDE,
        COMMIT_READ, COMMIT_REQUEST, COMMIT_WAIT,
        RESPOND_HEADER, RESPOND_READ_REQUEST, RESPOND_READ_WAIT, RESPOND_READ_BYTE,
        RESPOND_CRC_LO, RESPOND_CRC_HI
    } t_loader_state;

    // One byte through CRC-16/CCITT-FALSE, most significant bit first.
    function automatic logic [15:0] crc16_byte(input logic [15:0] crc, input logic [7:0] data);
        logic [15:0] value;
        value = crc ^ {data, 8'h00};
        for (int i = 0; i < 8; i++) begin
            value = value[15] ? ({value[14:0], 1'b0} ^ CRC_POLYNOMIAL) : {value[14:0], 1'b0};
        end
        return value;
    endfunction

    function automatic logic is_known_command(input logic [7:0] command);
        return (command == CMD_PING) || (command == CMD_HOLD) || (command == CMD_RELEASE) ||
               (command == CMD_WRITE) || (command == CMD_READ) || (command == CMD_SNAP_PIN) ||
               (command == CMD_SNAP_RELEASE) || (command == CMD_SNAP_READ);
    endfunction

    function automatic logic has_payload(input logic [7:0] command);
        return command == CMD_WRITE;
    endfunction

    function automatic logic needs_address(input logic [7:0] command);
        return (command == CMD_WRITE) || (command == CMD_READ) || (command == CMD_SNAP_READ);
    endfunction

    // Commands on the cartridge backing, which need the core held.
    function automatic logic needs_hold(input logic [7:0] command);
        return (command == CMD_WRITE) || (command == CMD_READ);
    endfunction

    // Commands whose OK response carries `length` payload bytes.
    function automatic logic reads_payload(input logic [7:0] command);
        return (command == CMD_READ) || (command == CMD_SNAP_READ);
    endfunction

    // The whole range must lie inside the selected space: reserved address
    // bits clear, and offset + length within 2 MiB of ROM or 32 KiB of RAM.
    function automatic logic address_valid(input logic [23:0] address, input logic [7:0] length_minus_one);
        logic [22:0] end_offset;
        logic        ram;
        ram = address[ADDRESS_RAM_BIT];
        end_offset = {2'b00, address[20:0]} + 23'(length_minus_one) + 23'd1;
        if (address[23:22] != 2'b00) return 1'b0;
        if (ram) return end_offset <= 23'd32768;
        return end_offset <= 23'd2097152;
    endfunction
endpackage
