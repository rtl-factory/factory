; Original animated system program for the frame-snapshot test: draws the
; background program's tiles and diagonal map, turns the LCD on and then
; scrolls the background once per frame forever, so every displayed frame
; differs from its neighbours while the host pins and reads frames over the
; UART loader.
;   VRAM $9000-$902f  tiles 0, 1, 2 (signed tile addressing, LCDC.4 = 0)
;   VRAM $8ff0-$8fff  tile $ff (signed id -1)
;   VRAM $9c00-$9fff  32x32 map: id = (row + column) & 3, where 3 becomes $ff
;   SCX $65, SCY $73 at LCD on; the VBlank handler adds 3 to SCX and 1 to
;   SCY each frame (both wrap)
;   BGP $1b (color 0 -> shade 3, 1 -> 2, 2 -> 1, 3 -> 0)
;   LCDC $89 (LCD on, map $9c00, signed tile data, background on)
;   $c0f0  VBlank count (wraps)
; Every VRAM write happens with the LCD off; afterwards the main loop only
; HALTs and the handler writes the scroll registers inside VBlank, so every
; frame is drawn with one scroll pair.
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
    XOR A, A
    LD [vblank_count], A
    LDH [$ff0f], A
    LD A, $01
    LDH [$ffff], A
    LD A, $89
    LDH [$ff40], A
    EI
main_loop:
    HALT
    JR main_loop
vblank_handler:
    PUSH AF
    LDH A, [$ff43]
    ADD A, 3
    LDH [$ff43], A
    LDH A, [$ff42]
    INC A
    LDH [$ff42], A
    LD A, [vblank_count]
    INC A
    LD [vblank_count], A
    POP AF
    RETI
vblank_count EQU $c0f0
tiles:
    DB $00, $00, $ff, $00, $00, $ff, $ff, $ff, $00, $00, $ff, $00, $00, $ff, $ff, $ff
    DB $55, $33, $55, $33, $55, $33, $55, $33, $55, $33, $55, $33, $55, $33, $55, $33
    DB $55, $33, $aa, $66, $55, $cc, $aa, $99, $55, $33, $aa, $66, $55, $cc, $aa, $99
    DB $33, $0f, $33, $0f, $cc, $3c, $cc, $3c, $33, $f0, $33, $f0, $cc, $c3, $cc, $c3
SECTION "vblank_vector", ROM
    JP vblank_handler
