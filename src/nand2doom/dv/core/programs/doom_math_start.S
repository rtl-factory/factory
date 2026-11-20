    .section .text.start,"ax",@progbits
    .globl _start
_start:
    li sp, 0x2000
    call c_entry
    ebreak
1:
    j 1b
