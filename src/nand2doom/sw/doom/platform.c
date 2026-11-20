/* nand2doom platform adapter for GPL-2.0-or-later doomgeneric. */
#include <stdint.h>
#include <stddef.h>
#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>

#include "doomgeneric.h"
#ifdef N2D_VERIFY_SINGLETICS
#include "d_loop.h"
#endif
#include "doomkeys.h"
#include "clock.h"
#include "capture.h"
#include "keymap.h"

enum {
    FRAMEBUFFER_ADDRESS = 0x10000000u,
    FRONT_FRAMEBUFFER_ADDRESS = 0x10020000u,
    FRAMEBUFFER_WORDS = (320u * 200u) / 4u,
    PALETTE_ADDRESS = 0x10010000u,
    SYSTEM_COMPLETION_ADDRESS = 0x20000004u,
    VIDEO_SWAP_ADDRESS = 0x20001008u,
    VIDEO_SWAP_PENDING = 2u,
    VIDEO_SWAP_REQUEST = 1u,
    VIDEO_SWAP_COMMIT_PALETTE = 2u,
    PS2_DATA_ADDRESS = 0x20002000u,
    PS2_STATUS_ADDRESS = 0x20002004u,
    DOOM_BOOT_MARKER = 0xd00d3701u,
    DOOM_FRAME_MARKER = 0xd00d3702u,
    DOOM_SCENE_MARKER = 0xd00d3703u,
    DOOM_RUNTIME_FAILURE = 0xd00d37eeu,
};

/* Doom draws into the back bank through DG_ScreenBuffer; the front view is
   the read-only image being scanned out. */
static volatile uint32_t *const framebuffer =
    (volatile uint32_t *)FRAMEBUFFER_ADDRESS;
static const volatile uint32_t *const front_framebuffer =
    (const volatile uint32_t *)FRONT_FRAMEBUFFER_ADDRESS;
static volatile uint32_t *const video_swap =
    (volatile uint32_t *)VIDEO_SWAP_ADDRESS;
static volatile uint32_t *const palette = (volatile uint32_t *)PALETTE_ADDRESS;
static volatile uint32_t *const completion =
    (volatile uint32_t *)SYSTEM_COMPLETION_ADDRESS;
static volatile uint32_t *const ps2_data =
    (volatile uint32_t *)PS2_DATA_ADDRESS;
static volatile uint32_t *const ps2_status =
    (volatile uint32_t *)PS2_STATUS_ADDRESS;

static uint32_t pending_palette[256];
static int palette_dirty;
static uint32_t player_angle;
static int player_state_valid;
static int swap_outstanding;
static int palette_commit_deferred;

void DG_Init(void) {
    N2D_ClockInit();
    N2D_CaptureInit();
    *completion = DOOM_BOOT_MARKER;
}

#ifdef N2D_DETERMINISTIC_TIME
/* Deterministic-time verification build: make every VGA raster that follows a
   frame or scene marker show exactly that frame, independent of CPU speed.
   The video status word holds the active palette bank (bit 0), a pending
   commit (bit 1) and the frame-start sequence (bits 31:2). Both waits run with
   game time paused; their timeouts use real time and only bound a hang. */
static uint32_t video_reference;
static int video_reference_valid;

static void video_wait(int (*done)(uint32_t video)) {
    N2D_ClockPause();
    uint32_t start = N2D_ClockRealMs();
    while (!done(N2D_CaptureVideo()) &&
           (uint32_t)(N2D_ClockRealMs() - start) < 100u) {
    }
    N2D_ClockResume();
}

/* The palette commit and the framebuffer swap requested for a frame take
   effect at the same vertical blank. */
static int frame_applied(uint32_t video) {
    return (video & 2u) == 0u && (*video_swap & VIDEO_SWAP_PENDING) == 0u;
}

static int swap_done(uint32_t video) {
    (void)video;
    return (*video_swap & VIDEO_SWAP_PENDING) == 0u;
}

static int raster_shown(uint32_t video) {
    return (((video >> 2) - (video_reference >> 2)) & 0x3fffffffu) >= 2u;
}

/* Called at the end of each frame and scene seam: the raster that starts
   next must complete before the framebuffer is written again. */
static void video_mark(void) {
    video_reference = N2D_CaptureVideo();
    video_reference_valid = 1;
}
#endif

/* Called before the next frame's first screen access. Waits for the
   requested swap, then copies rows [first_row, 200) of the newly displayed
   bank into the back bank, so those rows start as the displayed image: Doom
   redraws only what changed (status bar, borders, wipe columns) and reads the
   screen back. D_Display passes viewheight when a full-width level view will
   redraw every row above it, else 0. */
void N2D_FramebufferAcquireFrom(int first_row) {
    if (!swap_outstanding) return;
#ifdef N2D_DETERMINISTIC_TIME
    /* A hardware wait: game time does not advance while polling. */
    video_wait(swap_done);
#else
    while ((*video_swap & VIDEO_SWAP_PENDING) != 0u) {
    }
#endif
    if (first_row < 0 || first_row > 200) first_row = 0;
    /* A row is 80 words, so every start stays a four-word group. */
    for (size_t word = (size_t)first_row * 80u; word < FRAMEBUFFER_WORDS;
         word += 4u) {
        uint32_t a = front_framebuffer[word];
        uint32_t b = front_framebuffer[word + 1u];
        uint32_t c = front_framebuffer[word + 2u];
        uint32_t d = front_framebuffer[word + 3u];
        framebuffer[word] = a;
        framebuffer[word + 1u] = b;
        framebuffer[word + 2u] = c;
        framebuffer[word + 3u] = d;
    }
    swap_outstanding = 0;
}

void N2D_FramebufferAcquire(void) { N2D_FramebufferAcquireFrom(0); }

/* The frame is complete in the back bank: commit its palette and request the
   swap; both take effect at the same vertical blank. Rendering continues with
   game logic and waits only in N2D_FramebufferAcquireFrom. */
void DG_DrawFrame(void) {
#ifdef N2D_DETERMINISTIC_TIME
    /* Two frame starts after the last marker: one whole raster showed the
       previous frame before this swap replaces it. */
    if (video_reference_valid) video_wait(raster_shown);
#endif
    if (palette_dirty) {
        if (N2D_CaptureUpload(pending_palette)) palette_dirty = 0;
    }
    /* One write requests the swap and commits an uploaded palette, so the
       image and its palette flip at the same vertical blank. */
    *video_swap = VIDEO_SWAP_REQUEST |
        (palette_commit_deferred ? VIDEO_SWAP_COMMIT_PALETTE : 0u);
    palette_commit_deferred = 0;
    swap_outstanding = 1;
#ifdef N2D_DETERMINISTIC_TIME
    /* A request in vertical blank applies only at the next one; wait so the
       next raster always shows this frame with its palette. */
    video_wait(frame_applied);
#endif
    N2D_CaptureFrame();
    *completion = DOOM_FRAME_MARKER;
#ifdef N2D_DETERMINISTIC_TIME
    video_mark();
#endif
}

void N2D_SetPalette(const uint32_t *colors) {
    for (unsigned int index = 0; index < 256u; ++index) {
        uint32_t blue = (colors[index] >> 4) & 0xfu;
        uint32_t green = (colors[index] >> 12) & 0xfu;
        uint32_t red = (colors[index] >> 20) & 0xfu;
        pending_palette[index] = (red << 8) | (green << 4) | blue;
    }
    palette_dirty = 1;
}

void N2D_RecordSceneReady(int gameplay) {
    *completion = DOOM_SCENE_MARKER;
#ifdef N2D_DETERMINISTIC_TIME
    video_mark();
#endif
    N2D_CaptureScene(gameplay);
}

/* Called only after the capture helper accepts a request at a gameplay seam. */
void N2D_CaptureAcquire(void) { N2D_FramebufferAcquire(); }

uint32_t N2D_CaptureVideo(void) {
    return *(volatile uint32_t *)0x20001004u;
}
void N2D_CaptureColor(uint32_t index, uint32_t value) { palette[index] = value; }
/* The capture upload's commit rides on the frame's swap write. */
void N2D_CaptureCommit(void) { palette_commit_deferred = 1; }
uint32_t N2D_CaptureTime(void) { return DG_GetTicksMs(); }

int DG_GetKey(int *pressed, unsigned char *key) {
    uint32_t event = 0;
    if ((*ps2_status & 1u) != 0u) {
        event = *ps2_data;
    }
    if (event == 0u) return 0;
    *key = n2d_doom_key(event);
    if (*key == 0u) return 0;
    *pressed = ((event >> 8) & 1u) == 0u;
    *completion = 0xd00d3800u | ((uint32_t)(*pressed) << 8) | *key;
    return 1;
}

void DG_SetWindowTitle(const char *title) { (void)title; }

void N2D_RecordTiccmd(int angleturn, int forwardmove, int sidemove,
                      unsigned int buttons) {
    uint32_t activity = (angleturn != 0) | ((forwardmove != 0) << 1)
        | ((sidemove != 0) << 2) | ((buttons != 0u) << 3);
    if (activity != 0u) *completion = 0xd00d4000u | activity;
}

void N2D_RecordPlayerState(unsigned int angle, int x, int y) {
    (void)x;
    (void)y;
    if (player_state_valid && angle != player_angle) {
        *completion = 0xd00d5000u | ((angle >> 20) & 0xfffu);
    }
    player_angle = angle;
    player_state_valid = 1;
}

int main(void) {
    unsigned char *probe = malloc(16u);
    if (probe == NULL) {
        *completion = DOOM_RUNTIME_FAILURE;
        for (;;) { __asm__ volatile("nop"); }
    }
    for (unsigned int index = 0; index < 16u; ++index) probe[index] = 0xa5u;
    errno = 0;
    int descriptor = open("n2d-expected-missing", O_RDONLY);
    int runtime_ok = descriptor == -1 && errno == ENOENT;
    for (unsigned int index = 0; index < 16u; ++index) {
        if (probe[index] != 0xa5u) runtime_ok = 0;
    }
    free(probe);
    if (!runtime_ok) {
        *completion = DOOM_RUNTIME_FAILURE;
        for (;;) { __asm__ volatile("nop"); }
    }
    char *argv[] = {
        "doomgeneric", "-iwad", "freedoom1.wad", "-warp", "1", "1",
        "-skill", "1", "-nosound", "-nomusic", "-nomouse", 0
    };
#ifdef N2D_VERIFY_SINGLETICS
    /* Create also performs a startup Tick/display; enable the upstream mode
       before that display. Production and ordinary deterministic builds omit it. */
    singletics = true;
#endif
    doomgeneric_Create(11, argv);
    for (;;) doomgeneric_Tick();
}
