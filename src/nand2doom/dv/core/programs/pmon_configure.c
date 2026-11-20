/* Behavioral check of the n2d_pmon.h configure/clear sequence. The test
 * drives ICACHE_DEMAND_ACCESS on every instruction fetch inside
 * pmon_configure_under_test before its final mcountinhibit restore, so any
 * event that lands between the selector and half writes would remain. */
#include <stdint.h>

#include "n2d_pmon.h"

__attribute__((noinline)) void pmon_configure_under_test(void)
{
    n2d_pmon_configure(3, N2D_PMON_ICACHE_DEMAND_ACCESS);
}

void c_entry(void)
{
    volatile uint32_t *out = (volatile uint32_t *)0x80;

    /* Leave a nonzero count in counter 3 so clearing is observable. */
    N2D_CSR_WRITE(0x323, N2D_PMON_CYCLES);
    out[6] = N2D_CSR_READ(0xB03);
    pmon_configure_under_test();
    out[0] = N2D_CSR_READ(0xB03);
    out[1] = N2D_CSR_READ(0xB83);
    out[2] = N2D_CSR_READ(0x323);
    out[3] = N2D_CSR_READ(0x320);

    /* A counter the caller stopped stays stopped; others are untouched. */
    N2D_CSR_WRITE(0x320, N2D_PMON_INHIBIT_HPM(4));
    n2d_pmon_configure(4, N2D_PMON_CYCLES);
    out[4] = N2D_CSR_READ(0x320);
    out[5] = N2D_CSR_READ(0xB04);
}
