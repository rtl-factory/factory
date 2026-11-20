/* Firmware writer owns pixels and palette until READY, then parks until release. */
#include "capture.h"
#ifdef N2D_DETERMINISTIC_TIME
/* Verification-only deterministic time: waits run with game time paused. */
#include "clock.h"
#else
#define N2D_WAIT_BEGIN() ((void)0)
#define N2D_WAIT_END() ((void)0)
#endif

volatile n2d_capture_mailbox n2d_capture __attribute__((aligned(32)));
_Static_assert(sizeof(n2d_capture_mailbox) == 4u * N2D_CAPTURE_WORDS,
               "capture ABI must contain only aligned u32 words");
static uint32_t expected_bank;
static uint32_t fatal_error;

static void ordered(void) {
#ifdef __riscv
    __asm__ volatile("fence iorw,iorw" ::: "memory");
#else
    __asm__ volatile("" ::: "memory");
#endif
}

static uint32_t service(void) {
    uint32_t now = N2D_CaptureTime();
    /* Generation is evidence of a completed sample, never of a Resume command. */
    ++n2d_capture.generation;
#ifdef N2D_DETERMINISTIC_TIME
    /* Game time is paused during waits; their timeouts use real time. */
    now = N2D_ClockRealMs();
#endif
    return now;
}

static void fail(uint32_t error) {
    n2d_capture.ready = 0;
    n2d_capture.error = error;
    n2d_capture.state = CAP_ERROR;
}

static void idle(void) {
    n2d_capture.ready = 0;
    ordered();
    n2d_capture.error = fatal_error;
    n2d_capture.state = fatal_error ? CAP_ERROR : CAP_IDLE;
}

void N2D_CaptureInit(void) {
    n2d_capture.magic = 0;
    volatile uint32_t *words = (volatile uint32_t *)&n2d_capture;
    for (uint32_t i = 1; i < N2D_CAPTURE_WORDS; ++i) words[i] = 0;
    expected_bank = 0;
    fatal_error = 0;
    n2d_capture.abi = N2D_CAPTURE_ABI;
    n2d_capture.words = N2D_CAPTURE_WORDS;
    for (uint32_t i = 0; i < 8; ++i)
        n2d_capture.identity[i] = n2d_capture_identity[i];
    service();
    ordered();
    n2d_capture.magic = N2D_CAPTURE_MAGIC;
}

/* Never require pending=1: the commit may coincide with vertical blank. */
static int settle_wait(uint32_t sequence) {
    uint32_t start = service();
    for (;;) {
        if (sequence && n2d_capture.request != sequence) {
            if (!n2d_capture.request) idle(); else fail(CAP_SEQUENCE);
            return 0;
        }
        uint32_t video = N2D_CaptureVideo();
        if (!(video & 2u)) {
            if (n2d_capture.palette_known && (video & 1u) != expected_bank) {
                fatal_error = CAP_BANK;
                fail(fatal_error);
                return 0;
            }
            return 1;
        }
        if ((uint32_t)(service() - start) >= 100u) {
            fatal_error = CAP_TIMEOUT;
            fail(fatal_error);
            return 0;
        }
    }
}

/* Deterministic-time builds pause game time for the whole hardware wait. */
static int settle(uint32_t sequence) {
    N2D_WAIT_BEGIN();
    int settled = settle_wait(sequence);
    N2D_WAIT_END();
    return settled;
}

int N2D_CaptureUpload(const uint32_t *colors) {
    if (fatal_error || !settle(0)) return 0;
    uint32_t bank = N2D_CaptureVideo() & 1u;
    for (uint32_t i = 0; i < 256; ++i) {
        uint32_t value = colors[i] & 0xfffu;
        N2D_CaptureColor(i, value);
        n2d_capture.palette[i] = value;
    }
    expected_bank = bank ^ 1u;
    n2d_capture.palette_known = 1;
    ordered();
    N2D_CaptureCommit();
    return 1;
}

void N2D_CaptureFrame(void) { ++n2d_capture.frame; }

#ifdef N2D_DETERMINISTIC_TIME
static void capture_scene(uint32_t sequence);
#endif

void N2D_CaptureScene(int gameplay) {
    service();
    uint32_t sequence = n2d_capture.request;
    if (!sequence) { idle(); return; }
    if (fatal_error) { fail(fatal_error); return; }
    /* An error requires an observed zero release before any new request. */
    if (n2d_capture.state == CAP_ERROR) return;
    if (sequence <= n2d_capture.last) { fail(CAP_SEQUENCE); return; }
    /* The exact completed D_Display seam grants eligibility, never past history. */
    if (!gameplay) return;
#ifdef N2D_DETERMINISTIC_TIME
    /* PREPARING through release only waits on the host and video hardware;
       deterministic-time builds run it with game time paused. */
    N2D_ClockPause();
    capture_scene(sequence);
    N2D_ClockResume();
}

static void capture_scene(uint32_t sequence) {
#endif
    n2d_capture.last = sequence;
    n2d_capture.ready = 0;
    n2d_capture.state = CAP_PREPARING;
    if (!n2d_capture.palette_known) { fail(CAP_UNKNOWN_PALETTE); return; }
    /* Acquire after eligibility, using the same accepted request. A host can
       write request at any instruction boundary, including the scene seam. */
    N2D_CaptureAcquire();
    if (!settle(sequence)) return;
    uint32_t start = service();
    uint32_t first = N2D_CaptureVideo();
    for (;;) {
        uint32_t request = n2d_capture.request;
        if (!request) { idle(); return; }
        if (request != sequence) { fail(CAP_SEQUENCE); return; }
        uint32_t video = N2D_CaptureVideo();
        if ((video & 3u) != expected_bank) { fail(CAP_BANK); return; }
        if (((video >> 2) - (first >> 2)) % (1u << 30) >= 2u) {
            n2d_capture.video = video;
            break;
        }
        if ((uint32_t)(service() - start) >= 100u) { fail(CAP_TIMEOUT); return; }
    }
    service();
    if (n2d_capture.request != sequence) {
        if (!n2d_capture.request) idle(); else fail(CAP_SEQUENCE);
        return;
    }
    n2d_capture.state = CAP_READY;
    ordered();
    n2d_capture.ready = sequence;
    /* A cancellation between the last load and publication is harmless: the
       host requires request==ready, and this loop reloads before allowing writes. */
    while (n2d_capture.request == sequence) service();
    service();
    if (n2d_capture.request) fail(CAP_SEQUENCE); else idle();
}
