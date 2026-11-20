/* nand2doom firmware clock; GPL-2.0-or-later. */
#include "clock.h"

#if defined(N2D_DETERMINISTIC_TIME) && defined(N2D_CLOCK_TRACE)
#error "N2D_DETERMINISTIC_TIME and N2D_CLOCK_TRACE are exclusive"
#endif

#ifdef N2D_CLOCK_TRACE
/* Test-only software timestamp source; product always reads the existing MMIO. */
extern uint32_t n2d_clock_read_tick(void);
#elif defined(N2D_DETERMINISTIC_TIME)
/* Verification-only deterministic time. The tick is the retired-instruction
   count (minstret) plus an offset, at the same nominal 25,000 ticks per
   millisecond as the 25 MHz cycle counter (IPC 1). Waits on video hardware run
   paused: their instructions are subtracted, so game time depends only on the
   architectural instruction stream, never on microarchitectural timing. */
static uint32_t tick_offset;
static uint32_t pause_depth;
static uint32_t pause_instret;

static uint32_t read_instret(void) {
    uint32_t value;
    __asm__ volatile(".option push\n.option arch, +zicsr\n"
                     "csrr %0, minstret\n.option pop" : "=r"(value));
    return value;
}

static uint32_t n2d_clock_read_tick(void) {
    return (pause_depth ? pause_instret : read_instret()) + tick_offset;
}

void N2D_ClockPause(void) {
    if (pause_depth++ == 0u) pause_instret = read_instret();
}

void N2D_ClockResume(void) {
    if (pause_depth != 0u && --pause_depth == 0u)
        tick_offset -= read_instret() - pause_instret;
}

static n2d_clock real_state;
static int real_started;

uint32_t N2D_ClockRealMs(void) {
    uint32_t cycles = *(volatile uint32_t *)0x20000008u;
    if (!real_started) {
        n2d_clock_init(&real_state, cycles);
        real_started = 1;
    }
    return n2d_clock_sample(&real_state, cycles);
}
#else
static uint32_t n2d_clock_read_tick(void) {
    return *(volatile uint32_t *)0x20000008u;
}
#endif

static n2d_clock clock_state;

void N2D_ClockInit(void) {
    n2d_clock_init(&clock_state, n2d_clock_read_tick());
}

uint32_t DG_GetTicksMs(void) {
    return n2d_clock_sample(&clock_state, n2d_clock_read_tick());
}

#ifdef N2D_DETERMINISTIC_TIME
void DG_SleepMs(uint32_t milliseconds) {
    /* Advance deterministic time directly instead of spinning. */
    do {
        uint32_t chunk = milliseconds > 85899u ? 85899u : milliseconds;
        tick_offset += chunk * 25000u;
        n2d_clock_sample(&clock_state, n2d_clock_read_tick());
        milliseconds -= chunk;
    } while (milliseconds != 0u);
}
#else
void DG_SleepMs(uint32_t milliseconds) {
    /* Keep cycle-based deadlines (no millisecond rounding). Bound each signed
       deadline interval below half the hardware counter period. */
    do {
        uint32_t chunk = milliseconds > 85899u ? 85899u : milliseconds;
        uint32_t tick = n2d_clock_read_tick();
        uint32_t deadline = tick + chunk * 25000u;
        n2d_clock_sample(&clock_state, tick);
        while ((int32_t)(tick - deadline) < 0) {
            tick = n2d_clock_read_tick();
            n2d_clock_sample(&clock_state, tick);
        }
        milliseconds -= chunk;
    } while (milliseconds != 0u);
}
#endif
