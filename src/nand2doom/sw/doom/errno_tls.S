/* Why: the pinned GCC is built without native TLS, so it reaches errno through
   libgcc's emulated TLS (__emutls_v.errno, __emutls_get_address), while the
   pinned Picolibc keeps errno in real TLS at tp. Both names resolve here to
   libc's own errno, so the platform and Picolibc share one variable. Unused
   under a compiler with native TLS, where --gc-sections drops it. */
    .section .sdata.__emutls_v.errno,"aw",@progbits
    .globl __emutls_v.errno
    .type __emutls_v.errno, @object
    .balign 4
__emutls_v.errno:
    .word 4, 4, 0, 0
    .size __emutls_v.errno, . - __emutls_v.errno

/* Why: GCC emits a control object for every __thread a C file defines, so a
   second thread-local would link and silently alias errno; any other object
   traps at its first access, and the Doom build also rejects it from the
   symbol table. */
    .section .text.__emutls_get_address,"ax",@progbits
    .globl __emutls_get_address
    .type __emutls_get_address, @function
__emutls_get_address:
    la t0, __emutls_v.errno
    bne a0, t0, 1f
    lui a0, %tprel_hi(errno)
    add a0, a0, tp, %tprel_add(errno)
    addi a0, a0, %tprel_lo(errno)
    ret
1:
    unimp
    .size __emutls_get_address, . - __emutls_get_address
