OUTPUT_ARCH(riscv)
ENTRY(_start)

MEMORY
{
    SDRAM (rwx) : ORIGIN = 0x00000000, LENGTH = 32M
}

SECTIONS
{
    . = ORIGIN(SDRAM);
    .text : { KEEP(*(.text.start)) *(.text .text.*) } > SDRAM
    .rodata : { *(.rodata .rodata.*) } > SDRAM
    .data :
    {
        __global_pointer$ = . + 0x800;
        __data_start = .;
        *(.sdata .sdata.*) *(.data .data.*)
        __data_end = .;
    } > SDRAM
    .tdata : ALIGN(16)
    {
        __tls_start = .;
        __tdata_start = .;
        *(.tdata .tdata.*)
        __tdata_end = .;
    } > SDRAM
    .tbss (NOLOAD) : ALIGN(16)
    {
        __tbss_start = .;
        *(.tbss .tbss.*)
        __tls_end = .;
    } > SDRAM
    .bss ALIGN(ADDR(.tbss) + SIZEOF(.tbss), 16) (NOLOAD) :
    {
        __bss_start = .;
        *(.sbss .sbss.*) *(.bss .bss.*) *(COMMON)
        __bss_end = .;
    } > SDRAM

    __heap_start = ALIGN(16);
    __heap_end = 0x00700000;
    __zone_start = 0x00800000;
    __zone_end = 0x01800000;
    __stack_bottom = 0x01e00000;
    __stack_top = 0x01ff0000;
    __wad_start = 0x02000000;
    __wad_end = 0x03b760c4;

    ASSERT(__bss_end <= __heap_end, "Doom image overlaps heap")
    ASSERT(n2d_capture >= __bss_start && n2d_capture + 1120 <= __bss_end,
           "Capture mailbox must be wholly inside cleared BSS")
    ASSERT((n2d_capture & 31) == 0, "Capture mailbox alignment")
    ASSERT(__tls_end <= __bss_start, "Doom TLS overlaps BSS")
    ASSERT(__heap_end <= __zone_start, "Doom heap overlaps zone")
    ASSERT(__zone_end <= __stack_bottom, "Doom zone overlaps stack")
    ASSERT(__stack_top <= __wad_start, "Doom stack overlaps WAD")
    ASSERT(__wad_end <= 0x04000000, "Freedoom WAD exceeds SDRAM")

    /DISCARD/ : { *(.comment) *(.note*) *(.riscv.attributes) }
}
