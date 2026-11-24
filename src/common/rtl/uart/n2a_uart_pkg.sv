// Common UART constants: the factory board rate and the common PHY's bit
// period for a clock (wiki/src/common/rtl/uart/mas.md). No state, no timing.
package n2a_uart_pkg;
    // The one UART rate of every project's board image on every board (#129).
    localparam int unsigned N2A_UART_BOARD_BAUD = 2_000_000;

    function automatic int unsigned n2a_uart_gcd(input int unsigned a, input int unsigned b);
        int unsigned x, y, t;
        x = a;
        y = b;
        // Why: a bounded for loop instead of a while keeps every tool's
        // constant-function loop limit; 64 steps cover any 32-bit pair.
        for (int step = 0; step < 64; step++) begin
            if (y != 0) begin
                t = x % y;
                x = y;
                y = t;
            end
        end
        return x;
    endfunction

    // The common PHY's bit period for baud at clock_hz: whole clocks, plus the
    // remainder as the reduced fraction FRAC_NUM / FRAC_DEN (0 / 1 when exact).
    function automatic int unsigned n2a_uart_clks_per_bit(input int unsigned clock_hz,
                                                          input int unsigned baud);
        return clock_hz / baud;
    endfunction

    function automatic int unsigned n2a_uart_frac_num(input int unsigned clock_hz,
                                                      input int unsigned baud);
        int unsigned remainder;
        remainder = clock_hz % baud;
        return (remainder == 0) ? 0 : remainder / n2a_uart_gcd(remainder, baud);
    endfunction

    function automatic int unsigned n2a_uart_frac_den(input int unsigned clock_hz,
                                                      input int unsigned baud);
        int unsigned remainder;
        remainder = clock_hz % baud;
        return (remainder == 0) ? 1 : baud / n2a_uart_gcd(remainder, baud);
    endfunction
endpackage
