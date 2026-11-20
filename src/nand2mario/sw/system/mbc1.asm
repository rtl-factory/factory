; Original bounded MBC1 program for the n2m_system cartridge test. The direct
; 32 KiB image this assembles to is expanded by src/nand2mario/tools/mbc1_image.py
; into a 1 MiB (64 bank) MBC1+RAM+BATTERY image with 32 KiB of cartridge RAM:
; bank 1 ($4000-$7FFF here) is the template of every switched bank, each copy
; carrying its bank number at $7ffe and a per-bank data pattern at $7f00-$7ffd.
; The program leaves an exact WRAM signature at $c000 and four cartridge RAM
; banks with two defined bytes each:
;   $c000  RAM bank 2 byte $a000 read in mode 1 with BANK2=2 ($52)
;   $c001  bank 32 pattern byte $3f80 through the mode-1 low window (BANK2=1)
;   $c002  bank 32 number through the low window ($20)
;   $c003  bank 33 number through the high window ($21)
;   $c004  low window with BANK2=2 in mode 1: 64 & 63 = bank 0, byte $0101 ($c3)
;   $c005  high window with BANK2=2, BANK1=1: 65 & 63 = bank 1, its number ($01)
;   $c006  RAM bank 0 byte $a000 read in mode 0 with BANK2=2 ($50)
;   $c007  RAM bank 0 byte $bfff in mode 0 ($60)
;   $c008  disabled RAM reads open bus ($ff)
;   $c009  RAM re-enabled with $1a: the dropped write did not land ($50)
;   $c00a  RAM disabled with $0b: open bus ($ff)
;   $c00b.. per table entry: bank number then pattern byte $7f80 of the bank
;          reached through the high window: 1,2,1(zero),31,33,63,33,5,1,35
;   $c01f  table entry count (10)
RAMG EQU $0000
BANK1 EQU $2000
BANK2 EQU $4000
MODE EQU $6000
SECTION "code", ROM
start:
    LD SP, $dffe
; Copy the position-independent mode-1 routine into WRAM: in mode 1 with
; BANK2 set the low ROM window (and this code) is remapped, so the work that
; needs mode 1 runs from $c800.
    LD DE, wram_routine
    LD HL, $c800
    LD BC, wram_routine_end - wram_routine
copy_loop:
    LD A, [DE]
    LD [HL+], A
    INC DE
    DEC BC
    LD A, B
    OR A, C
    JR NZ, copy_loop
    LD HL, $c000
    LD A, $0a
    LD [RAMG], A
    CALL $c800
; RAM enable nibble: disable, dropped write, re-enable with another $xA
; value, disable with a non-$A nibble.
    XOR A, A
    LD [RAMG], A
    LD A, [$a000]
    LD [HL+], A
    LD A, $77
    LD [$a000], A
    LD A, $1a
    LD [RAMG], A
    LD A, [$a000]
    LD [HL+], A
    LD A, $0b
    LD [RAMG], A
    LD A, [$a000]
    LD [HL+], A
    LD A, $fa
    LD [RAMG], A
; ROM banks through the high window in mode 0: for every (BANK1, BANK2)
; pair the routine at $4000 of the selected bank stores its own number and
; its pattern byte.
    LD DE, bank_table
    LD C, 0
bank_loop:
    LD A, [DE]
    CP A, $ff
    JR Z, bank_done
    LD [BANK1], A
    INC DE
    LD A, [DE]
    LD [BANK2], A
    INC DE
    INC C
    CALL bank_entry
    JR bank_loop
bank_done:
    LD A, C
    LD [$c01f], A
    HALT
; Mode-1 work, executed from WRAM (relative jumps only, returns through the
; stack to bank-0 code once mode 0 is restored).
wram_routine:
    LD A, $01
    LD [MODE], A
    LD B, 0
ram_fill:
    LD A, B
    LD [BANK2], A
    LD A, $50
    ADD A, B
    LD [$a000], A
    LD A, $60
    ADD A, B
    LD [$bfff], A
    INC B
    LD A, B
    CP A, 4
    JR NZ, ram_fill
    LD A, 2
    LD [BANK2], A
    LD A, [$a000]
    LD [HL+], A
    LD A, 1
    LD [BANK2], A
    LD A, [$3f80]
    LD [HL+], A
    LD A, [$3ffe]
    LD [HL+], A
    LD A, [$7ffe]
    LD [HL+], A
    LD A, 2
    LD [BANK2], A
    LD A, [$0101]
    LD [HL+], A
    LD A, [$7ffe]
    LD [HL+], A
    XOR A, A
    LD [MODE], A
    LD A, [$a000]
    LD [HL+], A
    LD A, [$bfff]
    LD [HL+], A
    RET
wram_routine_end:
; (BANK1, BANK2) pairs; $ff ends the table. Expected high-window banks for a
; 64-bank image: 1, 2, 1 (zero selects one), 31, 33, 63, 33 (zero check before
; BANK2), 5 (69 & 63), 1 ($21 keeps five bits), 35 (99 & 63).
bank_table:
    DB 1, 0
    DB 2, 0
    DB 0, 0
    DB 31, 0
    DB 1, 1
    DB 31, 1
    DB 0, 1
    DB 5, 2
    DB $21, 0
    DB 3, 3
    DB $ff
SECTION "bank_code", ROM
; Template of every switched bank at $4000: store this bank's number and
; its pattern byte at $7f80.
bank_entry:
    LD A, [bank_id]
    LD [HL+], A
    LD A, [bank_data]
    LD [HL+], A
    RET
bank_data EQU $7f80
SECTION "bank_id", ROM
bank_id:
    DB 1
