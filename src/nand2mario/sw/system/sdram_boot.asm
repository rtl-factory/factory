; Original bounded program for the n2m_system SDRAM boot test. The direct
; 32 KiB image this assembles to is expanded by src/nand2mario/scripts/sw_build/mbc1_image.py
; into a 128 KiB (8 bank) MBC1+RAM+BATTERY image with 32 KiB of cartridge RAM
; that the UART loader writes into the SDRAM backing; bank 1 ($4000-$7FFF
; here) is the template of every switched bank, each copy carrying its bank
; number at $7ffe and a per-bank data pattern at $7f00-$7ffd. The program
; leaves an exact WRAM signature at $c000 and four cartridge RAM banks with
; two defined bytes each:
;   $c000  RAM bank 3 byte $a000 read in mode 1 with BANK2=3 ($53)
;   $c001  low window in mode 1 with BANK2=1: 32 & 7 = bank 0, its unused
;          byte $3f80 ($ff)
;   $c002  low window bank 0 byte $3ffe, unused ($ff)
;   $c003  RAM bank 0 byte $bfff read in mode 0 ($60)
;   $c004  disabled RAM reads open bus ($ff)
;   $c005.. per switched bank 1..7 through the high window: number, pattern
;          byte $7f80 (14 bytes)
;   $c013  BANK1=9 on an 8-bank image: 9 & 7 = bank 1 ($01)
;   $c014  bank count visited (7)
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
; RAM bank 0 in mode 0, then disable RAM and read open bus.
    LD A, [$bfff]
    LD [HL+], A
    XOR A, A
    LD [RAMG], A
    LD A, [$a000]
    LD [HL+], A
; Every switched bank through the high window in mode 0: the routine at
; $4000 of the selected bank stores its own number and its pattern byte.
    LD C, 1
bank_loop:
    LD A, C
    LD [BANK1], A
    CALL bank_entry
    INC C
    LD A, C
    CP A, 8
    JR NZ, bank_loop
; BANK1 above the bank count masks to the image: 9 & 7 = 1.
    LD A, 9
    LD [BANK1], A
    LD A, [bank_id]
    LD [HL+], A
    LD A, 7
    LD [HL+], A
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
    LD A, 3
    LD [BANK2], A
    LD A, [$a000]
    LD [HL+], A
    LD A, 1
    LD [BANK2], A
    LD A, [$3f80]
    LD [HL+], A
    LD A, [$3ffe]
    LD [HL+], A
    XOR A, A
    LD [MODE], A
    LD [BANK2], A
    RET
wram_routine_end:
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
