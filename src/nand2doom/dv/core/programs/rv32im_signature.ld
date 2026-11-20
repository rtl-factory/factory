OUTPUT_ARCH(riscv)
ENTRY(_start)

SECTIONS {
    . = 0;
    .text : {
        KEEP(*(.text.start))
        *(.text .text.*)
        *(.rodata .rodata.*)
    }
    /DISCARD/ : {
        *(.comment)
        *(.note*)
        *(.riscv.attributes)
    }
}
