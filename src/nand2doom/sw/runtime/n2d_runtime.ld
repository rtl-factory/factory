OUTPUT_ARCH(riscv)
ENTRY(_start)

MEMORY
{
    SDRAM (rwx) : ORIGIN = 0x00000000, LENGTH = 64M
}

SECTIONS
{
    . = ORIGIN(SDRAM);
    .text :
    {
        KEEP(*(.text.start))
        *(.text .text.*)
    } > SDRAM

    .rodata :
    {
        *(.rodata .rodata.*)
    } > SDRAM

    . = ALIGN(4);
    __data_load_start = .;
    .data 0x00008000 : AT(__data_load_start)
    {
        __data_start = .;
        *(.sdata .sdata.*)
        *(.data .data.*)
        *(.n2d_odd_data)
        __data_end = .;
    } > SDRAM

    .data_copy_guard (NOLOAD) :
    {
        KEEP(*(.n2d_data_guard))
    } > SDRAM

    .bss (NOLOAD) :
    {
        __bss_start = .;
        *(.sbss .sbss.*)
        *(.bss .bss.*)
        *(COMMON)
        *(.n2d_odd_bss)
        __bss_end = .;
    } > SDRAM

    .bss_zero_guard (NOLOAD) :
    {
        KEEP(*(.n2d_bss_guard))
    } > SDRAM

    __heap_start = ALIGN(16);
    __heap_end = 0x000f0000;
    __stack_top = 0x00100000;

    ASSERT(__bss_end <= __heap_end, "runtime image overlaps heap")
    ASSERT(__heap_end < __stack_top, "heap overlaps stack")

    /DISCARD/ :
    {
        *(.comment)
        *(.note*)
        *(.riscv.attributes)
    }
}
