// n2a_jtag_keyboard wire constants and state records. The command and status
// words are in wiki/src/common/scripts/jtag/spec.md "Wire format"; the host
// script src/common/scripts/jtag/n2a_jtag_keyboard.py holds the same wire
// constants, and its unit test fails when the two differ.
package n2a_jtag_keyboard_pkg;
    // Identity bytes (bits 63:56) of an IR 1 command and an IR 2 status.
    localparam logic [7:0] COMMAND_MAGIC = 8'hA3;
    localparam logic [7:0] STATUS_MAGIC = 8'h5A;
    // Scan codes carried by one command.
    localparam int unsigned MAX_BATCH_CODES = 4;

    // Status result code (bits 53:51) of the last command.
    typedef enum logic [2:0] {
        RESULT_RESET     = 3'd0,  // no command completed since reset
        RESULT_ACCEPTED  = 3'd1,  // every code accepted
        RESULT_PARTIAL   = 3'd2,  // a prefix accepted, the rest refused
        RESULT_MALFORMED = 3'd3,  // bad identity, count or reserved bits: fault
        RESULT_SEQUENCE  = 3'd4   // unexpected sequence number: fault
    } t_result;

    // TCK-domain scan state.
    typedef struct packed {
        logic [63:0] shift_data, command, response;
        logic [6:0]  bits_shifted;
        logic [1:0]  instruction;
        logic        scan_open, request, busy, error_seen, bypass;
    } t_scan;

    // System-domain command phase.
    typedef enum logic [2:0] {
        KBD_IDLE,
        KBD_DECODE,
        KBD_OFFER,
        KBD_ACCOUNT,
        KBD_FINISH
    } t_phase;

    // System-domain command state.
    typedef struct packed {
        t_phase      phase;
        logic [63:0] command, last_command, response;
        logic        ack, have_last, fault, valid, take;
        logic [7:0]  expected_seq, byte_data;
        logic [2:0]  accepted;
        t_result     code;
        logic [31:0] total;
    } t_system;
endpackage
