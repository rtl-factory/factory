; Original bounded system program: draws a known scrolled background and
; halts with the LCD on so the system test can capture one frame.
;   VRAM $9000-$902f  tiles 0, 1, 2 (signed tile addressing, LCDC.4 = 0)
;   VRAM $8ff0-$8fff  tile $ff (signed id -1)
;   VRAM $9c00-$9fff  32x32 map: id = (row + column) & 3, where 3 becomes $ff
;   SCX $65 (fine 5, wraps past column 255), SCY $73 (wraps past row 255)
;   BGP $1b (color 0 -> shade 3, 1 -> 2, 2 -> 1, 3 -> 0)
;   LCDC $89 (LCD on, map $9c00, signed tile data, background on)
;   $c000-$c003  LCDC, SCY, SCX and BGP read back through the I/O port
;   $c004  VBlank interrupt count after one HALT wake (1)
;   $c005  LY read inside the VBlank handler (144)
;   $c006  IF read inside the handler: VBlank already acknowledged ($e0)
; Every VRAM write happens with the LCD off, so no write is blocked. After the
; registers are read back the program clears IF, enables IE=VBlank and HALTs;
; the first VBlank wakes it into the handler at $0040, which counts the entry
; and records LY and IF. It then disables IE and HALTs for good with the LCD
; on so the system test can capture one frame.
SECTION "code", ROM
start:
    LD SP, $dffe
    XOR A, A
    LDH [$ff40], A
    LD HL, $9000
    LD DE, tiles
    LD B, 48
copy_tiles:
    LD A, [DE]
    LD [HL+], A
    INC DE
    DEC B
    JR NZ, copy_tiles
    LD HL, $8ff0
    LD B, 16
copy_tile_ff:
    LD A, [DE]
    LD [HL+], A
    INC DE
    DEC B
    JR NZ, copy_tile_ff
    LD HL, $9c00
    LD C, 0
map_row:
    LD B, 0
map_column:
    LD A, B
    ADD A, C
    AND A, 3
    CP A, 3
    JR NZ, map_store
    LD A, $ff
map_store:
    LD [HL+], A
    INC B
    LD A, B
    CP A, 32
    JR NZ, map_column
    INC C
    LD A, C
    CP A, 32
    JR NZ, map_row
    LD A, $73
    LDH [$ff42], A
    LD A, $65
    LDH [$ff43], A
    LD A, $1b
    LDH [$ff47], A
    LD A, $89
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
    LDH A, [$ff0f]
    LD [vblank_if], A
    POP AF
    RETI
vblank_count EQU $c0f0
vblank_ly EQU $c0f1
vblank_if EQU $c0f2
tiles:
    DB $00, $00, $ff, $00, $00, $ff, $ff, $ff, $00, $00, $ff, $00, $00, $ff, $ff, $ff
    DB $55, $33, $55, $33, $55, $33, $55, $33, $55, $33, $55, $33, $55, $33, $55, $33
    DB $55, $33, $aa, $66, $55, $cc, $aa, $99, $55, $33, $aa, $66, $55, $cc, $aa, $99
    DB $33, $0f, $33, $0f, $cc, $3c, $cc, $3c, $33, $f0, $33, $f0, $cc, $c3, $cc, $c3
SECTION "vblank_vector", ROM
    JP vblank_handler
