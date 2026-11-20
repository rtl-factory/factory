/* Verification only: link the production helper and callbacks to timestamp data. */
#include "clock.h"
#include "vectors.h"

#ifdef N2D_CLOCK_HOST
uint32_t probe_results[RESULT_WORDS];
#else
#define probe_results ((volatile uint32_t *)0x8000u)
#endif

static uint32_t tick;
static uint32_t step;
static uint32_t reads;

uint32_t n2d_clock_read_tick(void) {
    uint32_t value = tick;
    tick += step;
    ++reads;
    return value;
}

void c_entry(void) {
    unsigned int output = 0;
    for (unsigned int i = 0; i < TRACE_COUNT; ++i) {
        n2d_clock clock;
        n2d_clock_init(&clock, traces[i].origin);
        clock.milliseconds = traces[i].milliseconds;
        clock.remainder = traces[i].remainder;
        for (unsigned int j = traces[i].begin; j < traces[i].end; ++j) {
            probe_results[output++] = n2d_clock_sample(&clock, samples[j]);
            probe_results[output++] = clock.remainder;
        }
    }
    for (unsigned int i = 0; i < RUNTIME_COUNT; ++i) {
        tick = runtime[i].origin;
        step = 0;
        N2D_ClockInit();
        probe_results[output++] = DG_GetTicksMs();
        tick += runtime[i].age;
        probe_results[output++] = DG_GetTicksMs();
        step = runtime[i].step;
        reads = 0;
        DG_SleepMs(runtime[i].duration);
        /* Reuse the last sleep timestamp so the query measures only sleep. */
        tick -= step;
        step = 0;
        probe_results[output++] = DG_GetTicksMs();
        probe_results[output++] = reads - 1u;
    }
}
