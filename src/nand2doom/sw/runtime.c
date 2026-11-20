#include "runtime.h"

extern unsigned char __heap_start[];
extern unsigned char __heap_end[];

static uintptr_t heap_next = (uintptr_t)__heap_start;

void *memcpy(void *destination, const void *source, size_t count) {
    unsigned char *dst = destination;
    const unsigned char *src = source;
    for (size_t index = 0; index < count; ++index) {
        dst[index] = src[index];
    }
    return destination;
}

void *memmove(void *destination, const void *source, size_t count) {
    unsigned char *dst = destination;
    const unsigned char *src = source;
    uintptr_t dst_address = (uintptr_t)destination;
    uintptr_t src_address = (uintptr_t)source;
    if (dst_address <= src_address || dst_address - src_address >= count) {
        return memcpy(destination, source, count);
    }
    for (size_t index = count; index != 0; --index) {
        dst[index - 1] = src[index - 1];
    }
    return destination;
}

void *memset(void *destination, int value, size_t count) {
    unsigned char *dst = destination;
    for (size_t index = 0; index < count; ++index) {
        dst[index] = (unsigned char)value;
    }
    return destination;
}

int memcmp(const void *left, const void *right, size_t count) {
    const unsigned char *lhs = left;
    const unsigned char *rhs = right;
    for (size_t index = 0; index < count; ++index) {
        if (lhs[index] != rhs[index]) {
            return (int)lhs[index] - (int)rhs[index];
        }
    }
    return 0;
}

void *n2d_alloc(size_t count) {
    uintptr_t start = (heap_next + 15u) & ~(uintptr_t)15u;
    uintptr_t end = start + count;
    if (end < start || end > (uintptr_t)__heap_end) {
        return (void *)0;
    }
    heap_next = end;
    return (void *)start;
}
