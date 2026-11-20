OUTPUT_ARCH(riscv)
ENTRY(_start)
PHDRS { text PT_LOAD FLAGS(5); data PT_LOAD FLAGS(6); }
SECTIONS {
    . = 0;
    .text : {
        KEEP(*(.text.start))
        *(.text .text.*)
    } :text
    .rodata : { *(.rodata .rodata.*) } :text
    .data : { *(.data .data.*) } :data
    .bss (NOLOAD) : { *(.bss .bss.*) *(COMMON) } :data
    ASSERT(. < 0x8000, "clock probe overlaps results")
    /DISCARD/ : { *(.comment) *(.note*) *(.riscv.attributes) }
}
