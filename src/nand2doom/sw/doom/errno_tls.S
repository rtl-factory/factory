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

/* Why: errno is the only emulated-TLS object defined here, so any other
   thread-local fails the link on its missing control object, never here. */
    .section .text.__emutls_get_address,"ax",@progbits
    .globl __emutls_get_address
    .type __emutls_get_address, @function
__emutls_get_address:
    lui a0, %tprel_hi(errno)
    add a0, a0, tp, %tprel_add(errno)
    addi a0, a0, %tprel_lo(errno)
    ret
    .size __emutls_get_address, . - __emutls_get_address
