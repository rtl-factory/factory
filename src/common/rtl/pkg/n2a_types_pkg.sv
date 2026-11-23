// Type-only package of the common blocks' module-private state records
// (RTL BKM "Types and shared constants live in packages"). It holds no
// constants or functions, so every closure that compiles one of these blocks
// lists it without unused-parameter lint. Fields whose widths follow a block
// parameter stay registers of their own beside the packaged record.
package n2a_types_pkg;
    // n2a_uart_rx and n2a_uart_tx protocol phase.
    typedef enum logic [1:0] {
        UART_IDLE,
        UART_START,
        UART_DATA,
        UART_STOP
    } t_uart_state;

    // n2a_uart_rx and n2a_uart_tx protocol state. The parameter-sized bit
    // clock and fractional remainder are separate registers in each block.
    typedef struct packed {
        t_uart_state state;
        logic [2:0]  bit_index;
        logic [7:0]  shift;
        logic        extra;
    } t_uart_protocol;

    // n2a_ps2_receiver frame state. The parameter-sized timeout counter is a
    // separate register in the receiver.
    typedef struct packed {
        logic       previous;
        logic       active;
        logic [3:0] index;
        logic [7:0] data;
        logic       parity;
    } t_ps2_rx_state;

    // n2a_jtag_keyboard TCK-domain scan state.
    typedef struct packed {
        logic [63:0] shift_data, command, response;
        logic [6:0]  bits_shifted;
        logic [1:0]  instruction;
        logic        scan_open, request, busy, error_seen, bypass;
    } t_jtag_keyboard_scan;

    // n2a_jtag_keyboard system-domain command phase.
    typedef enum logic [2:0] {
        KBD_IDLE,
        KBD_DECODE,
        KBD_OFFER,
        KBD_ACCOUNT,
        KBD_FINISH
    } t_jtag_keyboard_phase;

    // n2a_jtag_keyboard system-domain command state.
    typedef struct packed {
        t_jtag_keyboard_phase phase;
        logic [63:0] command, last_command, response;
        logic        ack, have_last, fault, valid, take;
        logic [7:0]  expected_seq, byte_data;
        logic [2:0]  accepted, code;
        logic [31:0] total;
    } t_jtag_keyboard_system;
endpackage
