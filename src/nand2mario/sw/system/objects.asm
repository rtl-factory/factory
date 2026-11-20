; Original bounded system program: draws a background, a window and sprites
; whose OAM arrives through OAM DMA from a VBlank handler, then halts with
; the LCD on so the system test can capture one frame.
;   VRAM $8000-$807f  tiles 0-7 (unsigned tile addressing, LCDC.4 = 1):
;                     0 blank, 1 checker (colors 1/3), 2 stripes (color 2),
;                     3-6 sprite shapes with transparent holes, 7 diagonals
;   VRAM $9800-$9bff  background map: blank where (row + column) & 3 = 0,
;                     checker elsewhere
;   VRAM $9c00-$9fff  window map: tiles 2 and 7 alternating by row and column
;   WRAM $c100-$c19f  OAM shadow (40 entries) copied from the oam_table
;   HRAM $ff80-$ff87  the DMA routine copied from dma_routine
;   SCX 3, SCY 12, BGP $e4, OBP0 $d8, OBP1 $6c, WY 80, WX 100
;   LCDC $f3 (LCD on, window map $9c00, window on, $8000 tiles, map $9800,
;             8x8 objects, objects on, background on)
;   $c000-$c008  LCDC, SCY, SCX, BGP, OBP0, OBP1, WY, WX and DMA ($00)
;                read back through the I/O port before the interrupt
;   $c009  VBlank interrupt count after one HALT wake (1)
;   $c00a  LY read inside the VBlank handler before the DMA (144)
;   $c00b  DMA register read back after the transfer ($c1)
;   $c00c  IF read inside the handler: VBlank already acknowledged ($e0)
; Every VRAM write happens with the LCD off, so no write is blocked. The
; handler at $0040 counts the entry, records LY, calls the HRAM routine
; (which writes DMA and waits 160 machine cycles inside HRAM), records the
; DMA readback and IF, and returns; the main program then disables IE, stores
; the evidence and HALTs for good with the LCD on.
SECTION "code", ROM
start:
    LD SP, $dffe
    XOR A, A
    LDH [$ff40], A
    LD HL, $8000
    LD DE, tiles
    LD B, 128
copy_tiles:
    LD A, [DE]
    LD [HL+], A
    INC DE
    DEC B
    JR NZ, copy_tiles
    LD HL, $9800
    LD C, 0
bg_row:
    LD B, 0
bg_column:
    LD A, B
    ADD A, C
    AND A, 3
    JR NZ, bg_checker
    XOR A, A
    JR bg_store
bg_checker:
    LD A, 1
bg_store:
    LD [HL+], A
    INC B
    LD A, B
    CP A, 32
    JR NZ, bg_column
    INC C
    LD A, C
    CP A, 32
    JR NZ, bg_row
    LD HL, $9c00
    LD C, 0
win_row:
    LD B, 0
win_column:
    LD A, B
    ADD A, C
    AND A, 1
    JR Z, win_stripes
    LD A, 7
    JR win_store
win_stripes:
    LD A, 2
win_store:
    LD [HL+], A
    INC B
    LD A, B
    CP A, 32
    JR NZ, win_column
    INC C
    LD A, C
    CP A, 32
    JR NZ, win_row
    LD HL, $c100
    LD DE, oam_table
    LD B, 160
copy_oam:
    LD A, [DE]
    LD [HL+], A
    INC DE
    DEC B
    JR NZ, copy_oam
    LD HL, $ff80
    LD DE, dma_routine
    LD B, 8
copy_dma:
    LD A, [DE]
    LD [HL+], A
    INC DE
    DEC B
    JR NZ, copy_dma
    LD A, 12
    LDH [$ff42], A
    LD A, 3
    LDH [$ff43], A
    LD A, $e4
    LDH [$ff47], A
    LD A, $d8
    LDH [$ff48], A
    LD A, $6c
    LDH [$ff49], A
    LD A, 80
    LDH [$ff4a], A
    LD A, 100
    LDH [$ff4b], A
    LD A, $f3
    LDH [$ff40], A
    LD HL, $c000
    LDH A, [$ff40]
    LD [HL+], A
    LDH A, [$ff42]
    LD [HL+], A
    LDH A, [$ff43]
    LD [HL+], A
    LDH A, [$ff47]
    LD [HL+], A
    LDH A, [$ff48]
    LD [HL+], A
    LDH A, [$ff49]
    LD [HL+], A
    LDH A, [$ff4a]
    LD [HL+], A
    LDH A, [$ff4b]
    LD [HL+], A
    LDH A, [$ff46]
    LD [HL+], A
    XOR A, A
    LD [vblank_count], A
    LDH [$ff0f], A
    LD A, $01
    LDH [$ffff], A
    EI
    HALT
    DI
    XOR A, A
    LDH [$ffff], A
    LD A, [vblank_count]
    LD [HL+], A
    LD A, [vblank_ly]
    LD [HL+], A
    LD A, [vblank_dma]
    LD [HL+], A
    LD A, [vblank_if]
    LD [HL+], A
    HALT
vblank_handler:
    PUSH AF
    LD A, [vblank_count]
    INC A
    LD [vblank_count], A
    LDH A, [$ff44]
    LD [vblank_ly], A
    LD A, $c1
    CALL $ff80
    LDH A, [$ff46]
    LD [vblank_dma], A
    LDH A, [$ff0f]
    LD [vblank_if], A
    POP AF
    RETI
; Copied to HRAM: start the transfer from page A, then wait 40 x 4 machine
; cycles inside HRAM before returning through the stack.
dma_routine:
    LDH [$ff46], A
    LD A, 40
dma_wait:
    DEC A
    JR NZ, dma_wait
    RET
vblank_count EQU $c0f0
vblank_ly EQU $c0f1
vblank_dma EQU $c0f2
vblank_if EQU $c0f3
tiles:
    DB $00, $00, $00, $00, $00, $00, $00, $00, $00, $00, $00, $00, $00, $00, $00, $00
    DB $ff, $33, $ff, $33, $ff, $cc, $ff, $cc, $ff, $33, $ff, $33, $ff, $cc, $ff, $cc
    DB $00, $ff, $00, $ff, $00, $00, $00, $00, $00, $ff, $00, $ff, $00, $00, $00, $00
    DB $ff, $c0, $e0, $c0, $d0, $d0, $c0, $c8, $c0, $c4, $c2, $c0, $c0, $c0, $00, $00
    DB $00, $ff, $00, $e1, $00, $e1, $00, $e1, $00, $e1, $00, $e1, $00, $e1, $00, $ff
    DB $80, $00, $80, $00, $80, $00, $80, $00, $80, $00, $80, $00, $80, $00, $ff, $00
    DB $0f, $0f, $0f, $0f, $0f, $0f, $0f, $0f, $ff, $ff, $ff, $ff, $ff, $ff, $ff, $ff
    DB $80, $01, $40, $02, $20, $04, $10, $08, $08, $10, $04, $20, $02, $40, $01, $80
; OAM shadow: Y, X, tile, attributes per entry.
;   0      hidden at X 0 on the overflow line (counts toward the ten)
;   1-12   twelve overlapping objects on screen lines 50-57 with descending
;          X, so the smallest X wins the overlaps and the last three are
;          dropped by the limit
;   13-14  same X on screen line 100: the lower index wins, its holes show 14
;   15     priority over the background (hidden except on blank cells)
;   16     OBP1; 17 Y flip; 18 both flips
;   19-20  over the window, priority (visible on the color-0 stripes) and OBP1
;   21     X 168, never visible; 22 top edge; 23 left edge; 24 bottom edge;
;          25 right edge
;   26-39  Y 0, never on a line
oam_table:
    DB 66, 0, 6, $00
    DB 66, 100, 3, $00
    DB 66, 95, 4, $20
    DB 66, 90, 3, $00
    DB 66, 85, 4, $20
    DB 66, 80, 3, $00
    DB 66, 75, 4, $20
    DB 66, 70, 3, $00
    DB 66, 65, 4, $20
    DB 66, 60, 3, $00
    DB 66, 55, 4, $20
    DB 66, 50, 3, $00
    DB 66, 45, 4, $20
    DB 116, 60, 5, $00
    DB 116, 60, 6, $00
    DB 40, 40, 6, $80
    DB 40, 56, 4, $10
    DB 40, 72, 3, $40
    DB 40, 88, 3, $60
    DB 112, 130, 6, $80
    DB 112, 146, 4, $90
    DB 96, 168, 6, $00
    DB 10, 8, 6, $00
    DB 140, 4, 6, $00
    DB 156, 150, 6, $00
    DB 116, 165, 6, $00
    DB 0, 0, 0, $00
    DB 0, 0, 0, $00
    DB 0, 0, 0, $00
    DB 0, 0, 0, $00
    DB 0, 0, 0, $00
    DB 0, 0, 0, $00
    DB 0, 0, 0, $00
    DB 0, 0, 0, $00
    DB 0, 0, 0, $00
    DB 0, 0, 0, $00
    DB 0, 0, 0, $00
    DB 0, 0, 0, $00
    DB 0, 0, 0, $00
    DB 0, 0, 0, $00
SECTION "vblank_vector", ROM
    JP vblank_handler
