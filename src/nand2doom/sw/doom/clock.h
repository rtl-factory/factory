/* nand2doom firmware clock; GPL-2.0-or-later. */
#ifndef N2D_DOOM_CLOCK_H
#define N2D_DOOM_CLOCK_H

#include <stdint.h>

typedef struct {
    uint32_t last_tick;
    uint32_t milliseconds;
    uint32_t remainder;
} n2d_clock;

static inline void n2d_clock_init(n2d_clock *clock, uint32_t tick) {
    clock->last_tick = tick;
    clock->milliseconds = 0;
    clock->remainder = 0;
}

/* Samples must be less than 2^32 cycles apart. Milliseconds wrap modulo 2^32. */
static inline uint32_t n2d_clock_sample(n2d_clock *clock, uint32_t tick) {
    uint32_t delta = tick - clock->last_tick;
    clock->last_tick = tick;
    /* Split before adding: delta may be UINT32_MAX, remainder is < 25000. */
    uint32_t fraction = clock->remainder + delta % 25000u;
    clock->milliseconds += delta / 25000u;
    if (fraction >= 25000u) {
        ++clock->milliseconds;
        fraction -= 25000u;
    }
    clock->remainder = fraction;
    return clock->milliseconds;
}

void N2D_ClockInit(void);
uint32_t DG_GetTicksMs(void);
void DG_SleepMs(uint32_t milliseconds);

#ifdef N2D_DETERMINISTIC_TIME
/* Deterministic-time verification build only: hardware waits run paused
   (nestable), and their timeouts use real milliseconds from the cycle counter. */
void N2D_ClockPause(void);
void N2D_ClockResume(void);
uint32_t N2D_ClockRealMs(void);
#define N2D_WAIT_BEGIN() N2D_ClockPause()
#define N2D_WAIT_END() N2D_ClockResume()
#endif

#endif
