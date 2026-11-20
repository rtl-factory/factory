#include "runtime.h"

enum {
    FRAMEBUFFER_ADDRESS = 0x10000000u,
    FRAMEBUFFER_WIDTH = 320,
    FRAMEBUFFER_HEIGHT = 200,
    FRAMEBUFFER_WORDS = (320 * 200) / 4,
    FRONT_FRAMEBUFFER_ADDRESS = 0x10020000u,
    PALETTE_ADDRESS = 0x10010000u,
    VIDEO_COMMIT_ADDRESS = 0x20001000u,
    VIDEO_SWAP_ADDRESS = 0x20001008u,
    PS2_DATA_ADDRESS = 0x20002000u,
    PS2_STATUS_ADDRESS = 0x20002004u,
    SYSTEM_COMPLETION_ADDRESS = 0x20000004u,
    SYSTEM_IDENTITY_ADDRESS = 0x20000000u,
    SYSTEM_TICK_ADDRESS = 0x20000008u,
    SIGNATURE_ADDRESS = 0x00004000u,
    COMPLETION_MARKER = 0xd00d0035u,
};

static volatile uint32_t initialized_global = 7u;
static volatile uint8_t initialized_odd[3]
    __attribute__((section(".n2d_odd_data"))) = {0x11u, 0x22u, 0x33u};
volatile uint8_t data_copy_guard __attribute__((section(".n2d_data_guard")));
static volatile uint32_t zeroed_global;
static volatile uint8_t zeroed_odd[3]
    __attribute__((section(".n2d_odd_bss")));
volatile uint8_t zeroed_odd_guard __attribute__((section(".n2d_bss_guard")));

struct validation_signature {
    uint32_t completion;
    uint32_t call_stack;
    uint32_t multiply;
    uint32_t divide;
    uint32_t remainder;
    uint32_t initialized_data;
    uint32_t zeroed_bss;
    uint32_t heap_memory;
    uint32_t input_event;
    uint32_t platform_registers;
};

__attribute__((noinline)) static uint32_t stack_call(uint32_t input) {
    volatile uint32_t local[4];
    local[0] = input + 3u;
    local[1] = local[0] ^ 0x13579bdfu;
    local[2] = local[1] + initialized_global;
    local[3] = local[2] ^ local[0];
    return local[3];
}

static uint32_t read_input_event(volatile uint32_t *data,
                                 volatile uint32_t *status) {
    while ((*status & 1u) == 0u) {
        __asm__ volatile("nop");
    }
    // DATA bit 31 is the valid flag. Both physical PS/2 and UART injection
    // expose the same {extended, break, scan_code} event in bits 9:0.
    return *data & 0x3ffu;
}

__attribute__((noreturn)) int main(void) {
    volatile uint32_t lhs = 12345u;
    volatile uint32_t rhs = 37u;
    uint32_t *heap_words = n2d_alloc(8u * sizeof(uint32_t));
    volatile uint32_t *framebuffer = (volatile uint32_t *)FRAMEBUFFER_ADDRESS;
    volatile uint32_t *palette = (volatile uint32_t *)PALETTE_ADDRESS;
    volatile uint32_t *video_commit =
        (volatile uint32_t *)VIDEO_COMMIT_ADDRESS;
    volatile uint32_t *video_swap = (volatile uint32_t *)VIDEO_SWAP_ADDRESS;
    const volatile uint32_t *front_framebuffer =
        (const volatile uint32_t *)FRONT_FRAMEBUFFER_ADDRESS;
    volatile uint32_t *ps2_data = (volatile uint32_t *)PS2_DATA_ADDRESS;
    volatile uint32_t *ps2_status = (volatile uint32_t *)PS2_STATUS_ADDRESS;
    volatile uint32_t *system_completion =
        (volatile uint32_t *)SYSTEM_COMPLETION_ADDRESS;
    volatile uint32_t *system_identity =
        (volatile uint32_t *)SYSTEM_IDENTITY_ADDRESS;
    volatile uint32_t *system_tick = (volatile uint32_t *)SYSTEM_TICK_ADDRESS;
    volatile struct validation_signature *signature =
        (volatile struct validation_signature *)SIGNATURE_ADDRESS;
    uint32_t bss_initial = zeroed_global;

    memset(heap_words, 0x5a, 8u * sizeof(uint32_t));
    heap_words[2] = stack_call(0x2468ace0u);
    memmove(&heap_words[4], &heap_words[2], 2u * sizeof(uint32_t));

    for (uint32_t index = 0; index < 256u; ++index) {
        uint32_t red = (index >> 4) & 0xfu;
        uint32_t green = index & 0xfu;
        uint32_t blue = red ^ green;
        palette[index] = (red << 8) | (green << 4) | blue;
    }
    *video_commit = 1u;

    for (uint32_t y = 0; y < FRAMEBUFFER_HEIGHT; ++y) {
        for (uint32_t x = 0; x < FRAMEBUFFER_WIDTH; x += 4u) {
            uint32_t index = (x * 3u + y * 5u) & 0xffu;
            framebuffer[(y * FRAMEBUFFER_WIDTH + x) / 4u] =
                index |
                (((index + 3u) & 0xffu) << 8) |
                (((index + 6u) & 0xffu) << 16) |
                (((index + 9u) & 0xffu) << 24);
        }
    }

    // The integrated gate supplies the same make/release pair first through
    // real PS/2 pins and then through UART. One translator consumes both.
    uint32_t input_event = 0x811c9dc5u;
    uint32_t first_input_event = 0u;
    for (uint32_t index = 0; index < 4u; ++index) {
        uint32_t event = read_input_event(ps2_data, ps2_status);
        if (index == 0u) {
            first_input_event = event;
        }
        input_event = input_event * 33u ^ event;
    }
    framebuffer[0] = (framebuffer[0] & 0xffffff00u) |
                     (first_input_event & 0xffu);
    // Show the drawn back bank, then copy the displayed bank through the
    // read-only front view so the back bank again equals the display.
    *video_swap = 1u;
    while ((*video_swap & 2u) != 0u) {
    }
    for (uint32_t word = 0; word < FRAMEBUFFER_WORDS; ++word) {
        framebuffer[word] = front_framebuffer[word];
    }
    uint32_t tick_before = *system_tick;
    uint32_t tick_after = *system_tick;
    uint32_t platform_registers =
        (*system_identity == 0x3144324eu && tick_after > tick_before)
            ? 0x51a70001u
            : 0u;

    zeroed_global = 0x600dbeefu;
    signature->call_stack = heap_words[2];
    signature->multiply = lhs * rhs;
    signature->divide = lhs / rhs;
    signature->remainder = lhs % rhs;
    signature->initialized_data =
        (uint32_t)initialized_odd[0] |
        ((uint32_t)initialized_odd[1] << 8) |
        ((uint32_t)initialized_odd[2] << 16) |
        ((uint32_t)data_copy_guard << 24);
    signature->zeroed_bss =
        (bss_initial == 0u && zeroed_odd[0] == 0u &&
         zeroed_odd[1] == 0u && zeroed_odd[2] == 0u)
            ? ((uint32_t)zeroed_odd_guard << 24)
            : 0xffffffffu;
    signature->heap_memory = heap_words[4] ^ heap_words[5];
    signature->input_event = input_event;
    signature->platform_registers = platform_registers;
    signature->completion = COMPLETION_MARKER;
    *system_completion = COMPLETION_MARKER;

    // Keep the terminal loop within one cache line. This lets the host inject
    // a backing-memory maintenance fault without manufacturing an unrelated
    // instruction-fill fault before the serialized Halt command arrives.
    __asm__ volatile("1: j 1b");
    __builtin_unreachable();
}
