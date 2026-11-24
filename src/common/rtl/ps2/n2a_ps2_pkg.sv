// Common PS/2 keyboard package: the Set-2 event the decoder produces, the
// receiver's discard reasons, and the prefix bytes
// (wiki/src/common/rtl/ps2/mas.md). No state, no timing.
package n2a_ps2_pkg;
    // Ref: PS/2 scan code set 2: E0 marks the next code as extended and F0
    // marks it as a release (break).
    localparam logic [7:0] PS2_PREFIX_EXTENDED = 8'hE0;
    localparam logic [7:0] PS2_PREFIX_BREAK    = 8'hF0;

    // One key event, packed as {extended, key_break, code}: bit 9 extended,
    // bit 8 break, bits 7:0 the scan code, the layout nand2doom software reads.
    typedef struct packed {
        logic       extended;
        logic       key_break;
        logic [7:0] code;
    } t_ps2_event;

    // One-cycle discard reasons of n2a_ps2_receiver: parity and frame may
    // both mark one completed frame; frame also marks a bad start bit.
    typedef struct packed {
        logic parity;
        logic frame;
        logic timeout;
    } t_ps2_rx_errors;

    function automatic logic ps2_is_prefix(input logic [7:0] value);
        return (value == PS2_PREFIX_EXTENDED) || (value == PS2_PREFIX_BREAK);
    endfunction
endpackage
