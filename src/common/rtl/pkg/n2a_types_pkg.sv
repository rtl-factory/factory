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
endpackage
