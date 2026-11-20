#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <sys/stat.h>
#include <sys/types.h>

extern unsigned char __heap_start[];
extern unsigned char __heap_end[];
static unsigned char *heap = __heap_start;
volatile unsigned int n2d_error_log_write;
volatile unsigned char n2d_error_log[1024];

void *_sbrk(ptrdiff_t increment) {
    unsigned char *previous = heap;
    if (increment < 0 || (uintptr_t)heap + (uintptr_t)increment >
                             (uintptr_t)__heap_end) {
        errno = ENOMEM;
        return (void *)-1;
    }
    heap += increment;
    return previous;
}

int _write(int file, const void *buffer, size_t count) {
    const unsigned char *bytes = buffer;
    if (file == 1 || file == 2) {
        for (size_t index = 0; index < count; ++index) {
            n2d_error_log[n2d_error_log_write++ & 1023u] = bytes[index];
        }
    }
    return (int)count;
}
int write(int file, const void *buffer, size_t count) {
    return _write(file, buffer, count);
}
int _read(int file, void *buffer, size_t count) {
    (void)file; (void)buffer; (void)count; return 0;
}
int read(int file, void *buffer, size_t count) {
    return _read(file, buffer, count);
}
int _close(int file) { (void)file; return -1; }
int close(int file) { return _close(file); }
off_t _lseek(int file, off_t offset, int whence) {
    (void)file; (void)offset; (void)whence; return (off_t)-1;
}
off_t lseek(int file, off_t offset, int whence) {
    return _lseek(file, offset, whence);
}
int _fstat(int file, struct stat *status) {
    (void)file; status->st_mode = S_IFCHR; return 0;
}
int _isatty(int file) { (void)file; return 1; }
int open(const char *path, int flags, ...) {
    (void)path; (void)flags; errno = ENOENT; return -1;
}
int unlink(const char *path) { (void)path; errno = EROFS; return -1; }
int rename(const char *old_path, const char *new_path) {
    (void)old_path; (void)new_path; errno = EROFS; return -1;
}
int mkdir(const char *path, mode_t mode) {
    (void)path; (void)mode; errno = EROFS; return -1;
}
void _exit(int status) {
    *(volatile uint32_t *)0x20000004u = 0xd00de000u | ((uint32_t)status & 0xfffu);
    for (;;) { __asm__ volatile("nop"); }
}
