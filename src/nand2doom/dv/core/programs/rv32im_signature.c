#include <stdint.h>

enum { SIGNATURE_ADDRESS = 0x80 };

__attribute__((noreturn)) void c_entry(void) {
    volatile uint32_t n = 5;
    uint32_t product = 1;

    while (n != 0) {
        product *= n;
        n -= 1;
    }
    *(volatile uint32_t *)SIGNATURE_ADDRESS = product + 7;

    __asm__ volatile("ebreak");
    for (;;) {
    }
}
