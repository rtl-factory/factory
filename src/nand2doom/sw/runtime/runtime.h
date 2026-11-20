#ifndef N2D_RUNTIME_H
#define N2D_RUNTIME_H

#include <stddef.h>
#include <stdint.h>

void *memcpy(void *destination, const void *source, size_t count);
void *memmove(void *destination, const void *source, size_t count);
void *memset(void *destination, int value, size_t count);
int memcmp(const void *left, const void *right, size_t count);
void *n2d_alloc(size_t count);

#endif
