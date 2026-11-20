    .section .text.start,"ax",@progbits
    .globl _start
    .type _start, @function
_start:
    la sp, __stack_top
    la gp, __global_pointer$
    la tp, __tls_start

    la a0, __tbss_start
    la a1, __bss_end
1:
    bgeu a0, a1, 2f
    sb zero, 0(a0)
    addi a0, a0, 1
    j 1b
2:
    call main
3:
    j 3b
    .size _start, . - _start
