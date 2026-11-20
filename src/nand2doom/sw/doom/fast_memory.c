#include <stddef.h>
#include <stdint.h>

void *memset(void *destination, int value, size_t count) {
    uint8_t *bytes = destination;
    uint8_t fill = (uint8_t)value;
    while (count != 0 && ((uintptr_t)bytes & 3u) != 0u) {
        *bytes++ = fill;
        --count;
    }
    uint32_t word = (uint32_t)fill * 0x01010101u;
    uint32_t *words = (uint32_t *)(void *)bytes;
    while (count >= sizeof(*words)) {
        *words++ = word;
        count -= sizeof(*words);
    }
    bytes = (uint8_t *)(void *)words;
    while (count-- != 0) *bytes++ = fill;
    return destination;
}

void *memcpy(void *destination, const void *source, size_t count) {
    uint8_t *dst = destination;
    const uint8_t *src = source;
    if ((((uintptr_t)dst | (uintptr_t)src) & 3u) == 0u) {
        uint32_t *dst_words = (uint32_t *)(void *)dst;
        const uint32_t *src_words = (const uint32_t *)(const void *)src;
        while (count >= sizeof(*dst_words)) {
            *dst_words++ = *src_words++;
            count -= sizeof(*dst_words);
        }
        dst = (uint8_t *)(void *)dst_words;
        src = (const uint8_t *)(const void *)src_words;
    }
    while (count-- != 0) *dst++ = *src++;
    return destination;
}
