#ifndef N2D_PMON_H
#define N2D_PMON_H

/* Bare-metal accessor for the nand2doom machine-mode performance monitor.
 * The CSR layout and event IDs are owned by wiki/src/nand2doom/rtl/core/spec.md
 * ("Performance monitor"). Counters 3..6 are programmable; mcycle and
 * minstret are the fixed CY and IR counters. */

#include <stdint.h>

enum n2d_pmon_event {
    N2D_PMON_NONE = 0,
    N2D_PMON_CYCLES = 1,
    N2D_PMON_INSTRET = 2,
    N2D_PMON_IFETCH_WAIT = 3,
    N2D_PMON_DATA_HOLD = 4,
    N2D_PMON_LOAD_USE_HOLD = 5,
    N2D_PMON_MULDIV_HOLD = 6,
    N2D_PMON_DATA_ADMISSION_HOLD = 8,
    N2D_PMON_BRANCH_REDIRECT = 9,
    N2D_PMON_TRAP_ENTRY = 10,
    N2D_PMON_ICACHE_DEMAND_ACCESS = 12,
    N2D_PMON_ICACHE_DEMAND_MISS = 13,
    N2D_PMON_ICACHE_REFILL = 14,
    N2D_PMON_DCACHE_READ_ACCESS = 15,
    N2D_PMON_DCACHE_READ_MISS = 16,
    N2D_PMON_DCACHE_WRITE_MISS = 17,
    N2D_PMON_DCACHE_REFILL = 18,
    N2D_PMON_DCACHE_WRITEBACK = 19,
    N2D_PMON_MEM_FETCH_READ = 29,
    N2D_PMON_MEM_DATA_READ = 30,
    N2D_PMON_MEM_WRITE = 32,
    N2D_PMON_MEM_BACKPRESSURE = 33,
    N2D_PMON_MEM_READ_SERVICE = 34,
    N2D_PMON_DCACHE_WRITE_ACCESS = 64,
    N2D_PMON_UNCACHED_FB_STORE = 65,
    N2D_PMON_UNCACHED_MMIO_ACCESS = 66,
    N2D_PMON_UNCACHED_WAIT = 67,
    N2D_PMON_SYSTEM_SERIALIZE_HOLD = 68,
    N2D_PMON_MUL_USE_HOLD = 69,
    N2D_PMON_DEBUG_HALTED = 76,
    N2D_PMON_MEM_MASTER_WAIT = 77,
    N2D_PMON_BRANCH_PREDICT_TAKEN = 78,
    N2D_PMON_BRANCH_MISPREDICT = 79
};

/* mcountinhibit bits: CY = 0, IR = 2, HPM3..6 = 3..6. */
#define N2D_PMON_INHIBIT_CY 0x01u
#define N2D_PMON_INHIBIT_IR 0x04u
#define N2D_PMON_INHIBIT_HPM(counter) (1u << (counter))
#define N2D_PMON_INHIBIT_ALL 0x7du

/* Software builds with -march=rv32im; enable Zicsr for these instructions
 * only, since the core implements them as specified in core/HAS.md. */
#define N2D_CSR_READ(csr) ({ uint32_t value_; \
    __asm__ volatile (".option push\n.option arch, +zicsr\n" \
                      "csrr %0, " #csr "\n.option pop" : "=r"(value_)); value_; })
#define N2D_CSR_WRITE(csr, value) \
    __asm__ volatile (".option push\n.option arch, +zicsr\n" \
                      "csrw " #csr ", %0\n.option pop" :: "r"((uint32_t)(value)))

/* Stopped-counter pair reads need no high/low retry loop; running reads
 * retry until the high half is stable. */
#define N2D_PMON_READ64(lo, hi) ({ uint32_t hi_, lo_; \
    do { hi_ = N2D_CSR_READ(hi); lo_ = N2D_CSR_READ(lo); } \
    while (hi_ != N2D_CSR_READ(hi)); ((uint64_t)hi_ << 32) | lo_; })

static inline uint32_t n2d_pmon_inhibit(uint32_t mask)
{
    uint32_t previous = N2D_CSR_READ(0x320);
    N2D_CSR_WRITE(0x320, mask);
    return previous;
}

/* Select an event for counter 3..6 and clear it. The counter is stopped
 * while its selector and both halves are written, so no event can land
 * between the half writes; the previous inhibit mask is then restored, so
 * the counter resumes only if it was running and other counters are not
 * affected. */
static inline void n2d_pmon_configure(unsigned counter, enum n2d_pmon_event event)
{
    if (counter < 3 || counter > 6)
        return;
    uint32_t previous = N2D_CSR_READ(0x320);
    N2D_CSR_WRITE(0x320, previous | N2D_PMON_INHIBIT_HPM(counter));
    switch (counter) {
    case 3: N2D_CSR_WRITE(0x323, event); N2D_CSR_WRITE(0xB03, 0); N2D_CSR_WRITE(0xB83, 0); break;
    case 4: N2D_CSR_WRITE(0x324, event); N2D_CSR_WRITE(0xB04, 0); N2D_CSR_WRITE(0xB84, 0); break;
    case 5: N2D_CSR_WRITE(0x325, event); N2D_CSR_WRITE(0xB05, 0); N2D_CSR_WRITE(0xB85, 0); break;
    default: N2D_CSR_WRITE(0x326, event); N2D_CSR_WRITE(0xB06, 0); N2D_CSR_WRITE(0xB86, 0); break;
    }
    N2D_CSR_WRITE(0x320, previous);
}

static inline uint64_t n2d_pmon_read(unsigned counter)
{
    switch (counter) {
    case 3: return N2D_PMON_READ64(0xB03, 0xB83);
    case 4: return N2D_PMON_READ64(0xB04, 0xB84);
    case 5: return N2D_PMON_READ64(0xB05, 0xB85);
    case 6: return N2D_PMON_READ64(0xB06, 0xB86);
    default: return 0;
    }
}

static inline uint64_t n2d_pmon_cycles(void) { return N2D_PMON_READ64(0xB00, 0xB80); }
static inline uint64_t n2d_pmon_instret(void) { return N2D_PMON_READ64(0xB02, 0xB82); }

/* Coherent snapshot of all counters: stop them, read, restore the mask. */
struct n2d_pmon_snapshot {
    uint64_t cycles;
    uint64_t instret;
    uint64_t hpm[4];
};

static inline void n2d_pmon_snapshot(struct n2d_pmon_snapshot *snapshot)
{
    uint32_t previous = n2d_pmon_inhibit(N2D_PMON_INHIBIT_ALL);
    snapshot->cycles = n2d_pmon_cycles();
    snapshot->instret = n2d_pmon_instret();
    for (unsigned counter = 3; counter <= 6; ++counter)
        snapshot->hpm[counter - 3] = n2d_pmon_read(counter);
    N2D_CSR_WRITE(0x320, previous);
}

#endif
