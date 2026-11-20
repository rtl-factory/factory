    .section .text.start,"ax",@progbits
    .globl _start
    .type _start, @function
_start:
    la sp, __stack_top

    la t0, data_copy_guard
    li t1, 0xa7
    sb t1, 0(t0)
    la t0, zeroed_odd_guard
    li t1, 0x5a
    sb t1, 0(t0)

    la a0, __data_load_start
    la a1, __data_start
    la a2, __data_end
1:
    bgeu a1, a2, 2f
    lbu t0, 0(a0)
    sb t0, 0(a1)
    addi a0, a0, 1
    addi a1, a1, 1
    j 1b

2:
    la a0, __bss_start
    la a1, __bss_end
3:
    bgeu a0, a1, 4f
    sb zero, 0(a0)
    addi a0, a0, 1
    j 3b

4:
    call main
5:
    j 5b
    .size _start, . - _start
