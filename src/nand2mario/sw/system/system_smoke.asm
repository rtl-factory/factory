; Original bounded system program: touches every Game Boy memory-map region
; class through the CPU bus, exercises the interrupt flags and the timer, waits
; in HALT for one timer interrupt, reads the joypad rows while the bench
; presses buttons at the labelled points, wakes from HALT on a joypad press,
; prints "N2M" and the pressed-row byte over the serial port one interrupt per
; byte, and leaves an exact WRAM/HRAM signature.
;   $c000  ROM data byte read back after an ignored ROM write
;   $c001  written through its echo alias $e001
;   $c002  echo-written byte read back through the base address
;   $c003  base-written $d123 read back through its echo $f123
;   $c004  absent cartridge RAM reads open bus
;   $c005  unusable window reads open bus
;   $c006  unclaimed peripheral address $ff7f reads open bus after an ignored write
;   $c007  VRAM first byte round trip
;   $c008  VRAM last byte round trip
;   $c009  OAM first byte round trip
;   $c00a  OAM last byte round trip
;   $c00b  IF after a software write of $1f: all five flags, upper bits high
;   $c00c  IF after a software write of $04: only the timer flag
;   $c00d  IF after a software write of $00
;   $c00e  TAC readback of $03: unused bits high, timer disabled
;   $c00f  TIMA written while the timer is disabled
;   $c010  TMA readback
;   $c011  DIV read a few machine cycles after a DIV write
;   $c012  DIV read about 90 machine cycles after that write
;   $c013  TIMA after the timer interrupt: the reloaded TMA value
;   $c014  IF after the interrupt: the timer flag acknowledged
;   $c015  IE readback of all eight written bits
;   $c016  written by the called helper
;   $c017  P1 with no row selected
;   $c018  P1 direction row with Right, Up, A and Start pressed
;   $c019  P1 action row with the same buttons
;   $c01a  P1 both rows with the same buttons
;   $c01b  IF after the press fell on the direction row and the action-row
;          select exposed A and Start (joypad flag, IE zero)
;   $c01c  P1 with no row selected while the buttons stay pressed
;   $c01d  IF after the release and a direction-row select (nothing raised)
;   $c01e  P1 direction row after the joypad interrupt woke HALT (Down pressed)
;   $c01f  IF after that interrupt (joypad flag acknowledged)
;   $c020  IF after selecting the action row with Down held (no fall)
;   $c021  SB after the last serial byte completed (ones shifted in)
;   $c022  SC after that completion (enable cleared, internal clock kept)
;   $c023  SC during the external-clock wait (enable still set)
;   $c024  SB during the external-clock wait (unchanged)
;   $c025  SC after software cleared the external-clock transfer
;   $c026  IF after the external-clock wait (no serial flag)
;   $c0f0  timer interrupt count
;   $c0f1  TIMA read inside the handler (reloaded TMA value)
;   $c0f2  IF read inside the handler (timer flag already acknowledged)
;   $c0f3  joypad interrupt count
;   $c0f4  P1 read inside the joypad handler (Down pressed, direction row)
;   $c0f5  IF read inside the joypad handler (joypad flag already acknowledged)
;   $c0f6  serial interrupt count
;   $c0f7  SB read inside the serial handler (ones shifted in)
;   $c0f8  SC read inside the serial handler (enable cleared, internal clock kept)
;   HRAM   every byte $ff80-$fffe holds (address low byte) XOR $5a
SECTION "code", ROM
start:
    LD SP, $dffe
    ; The core starts in the DMG post-boot state with the LCD on. Turn it
    ; off first so VRAM and OAM are free (in simulation only; on hardware
    ; LCD off belongs in VBlank, this program's oracle does not model LY).
    XOR A, A
    LDH [$ff40], A
    LD HL, $c000
    LD A, $55
    LD [rom_byte], A
    LD A, [rom_byte]
    LD [HL+], A
    LD A, $3c
    LD [$e001], A
    LD A, [$c001]
    INC HL
    LD [HL+], A
    LD A, $c3
    LD [$d123], A
    LD A, [$f123]
    LD [HL+], A
    LD A, $12
    LD [$a000], A
    LD A, [$a000]
    LD [HL+], A
    LD A, $34
    LD [$fea0], A
    LD A, [$feff]
    LD [HL+], A
    LD A, $56
    LDH [$ff7f], A
    LDH A, [$ff7f]
    LD [HL+], A
    LD A, $81
    LD [$8000], A
    LD A, $18
    LD [$9fff], A
    LD A, [$8000]
    LD [HL+], A
    LD A, [$9fff]
    LD [HL+], A
    LD A, $0a
    LD [$fe00], A
    LD A, $9f
    LD [$fe9f], A
    LD A, [$fe00]
    LD [HL+], A
    LD A, [$fe9f]
    LD [HL+], A
    LD C, $80
hram_fill:
    LD A, C
    XOR A, $5a
    LDH [C], A
    INC C
    LD A, C
    CP A, $ff
    JR NZ, hram_fill
; Interrupt flags: software set and clear while IE is zero, so nothing is
; dispatched; the upper three bits always read high.
    LD A, $1f
    LDH [$ff0f], A
    LDH A, [$ff0f]
    LD [HL+], A
    LD A, $04
    LDH [$ff0f], A
    LDH A, [$ff0f]
    LD [HL+], A
    XOR A, A
    LDH [$ff0f], A
    LDH A, [$ff0f]
    LD [HL+], A
; Timer registers while the timer is disabled.
    LD A, $a5
    LDH [$ff06], A
    LD A, $12
    LDH [$ff05], A
    LD A, $03
    LDH [$ff07], A
    LDH A, [$ff07]
    LD [HL+], A
    LDH A, [$ff05]
    LD [HL+], A
    LDH A, [$ff06]
    LD [HL+], A
; DIV: a write zeroes it; it counts up every 64 machine cycles.
    LDH [$ff04], A
    LDH A, [$ff04]
    LD [HL+], A
    LD B, 20
div_wait:
    DEC B
    JR NZ, div_wait
    LDH A, [$ff04]
    LD [HL+], A
; Timer interrupt: TIMA=$fc at 16384 Hz overflows after four increments; the
; handler runs once while the main program sleeps in HALT. TIMA and IF are
; read before the timer is disabled: disabling TAC while the selected divider
; bit is high counts one more falling edge, which is not part of the signature.
    XOR A, A
    LD [timer_count], A
    LD A, $fc
    LDH [$ff05], A
    LD A, $07
    LDH [$ff07], A
    LD A, $04
    LDH [$ffff], A
    EI
    HALT
    DI
    LDH A, [$ff05]
    LD [HL+], A
    LDH A, [$ff0f]
    LD [HL+], A
    XOR A, A
    LDH [$ff07], A
    LD A, $e5
    LDH [$ffff], A
    LDH A, [$ffff]
    LD [HL+], A
    CALL finish
; Joypad: P1 rows and select bits while the bench presses buttons at the
; labelled points (the test changes buttons_i when the instruction before a
; label retires), the flag a select write raises by exposing pressed buttons,
; and the joypad interrupt waking HALT on a selected press.
    XOR A, A
    LD [joypad_count], A
    LD [serial_count], A
    LD A, $30
    LDH [$ff00], A
    LDH A, [$ff00]
    LD [HL+], A
    LD A, $20
    LDH [$ff00], A
joypad_press_1:
    LDH A, [$ff00]
    LD [HL+], A
    LD A, $10
    LDH [$ff00], A
    LDH A, [$ff00]
    LD [HL+], A
    XOR A, A
    LDH [$ff00], A
    LDH A, [$ff00]
    LD [HL+], A
    LDH A, [$ff0f]
    LD [HL+], A
    LD A, $30
    LDH [$ff00], A
    LDH A, [$ff00]
    LD [HL+], A
    XOR A, A
    LDH [$ff0f], A
joypad_release:
    LD A, $20
    LDH [$ff00], A
    LDH A, [$ff0f]
    LD [HL+], A
    LD A, $10
    LDH [$ffff], A
    EI
    HALT
joypad_woken:
    DI
    LDH A, [$ff00]
    LD [HL+], A
    LDH A, [$ff0f]
    LD [HL+], A
    LD A, $10
    LDH [$ff00], A
    LDH A, [$ff0f]
    LD [HL+], A
; Serial: "N2M" then the P1 byte the joypad handler read, one byte per
; internal-clock transfer of 1024 machine cycles, each completion waking HALT
; through the serial interrupt; then an external-clock transfer that waits.
    LD A, $08
    LDH [$ffff], A
    LD DE, serial_text
    LD B, 3
serial_loop:
    LD A, [DE]
    INC DE
    LDH [$ff01], A
    LD A, $81
    LDH [$ff02], A
    EI
    HALT
    DI
    DEC B
    JR NZ, serial_loop
    LD A, [joypad_p1]
    LDH [$ff01], A
    LD A, $81
    LDH [$ff02], A
    EI
    HALT
    DI
    LDH A, [$ff01]
    LD [HL+], A
    LDH A, [$ff02]
    LD [HL+], A
    LD A, $5a
    LDH [$ff01], A
    LD A, $80
    LDH [$ff02], A
    LD B, 20
serial_wait:
    DEC B
    JR NZ, serial_wait
    LDH A, [$ff02]
    LD [HL+], A
    LDH A, [$ff01]
    LD [HL+], A
    XOR A, A
    LDH [$ff02], A
    LDH A, [$ff02]
    LD [HL+], A
    LDH A, [$ff0f]
    LD [HL+], A
    HALT
finish:
    LD A, $6e
    LD [HL+], A
    RET
timer_handler:
    PUSH AF
    LD A, [timer_count]
    INC A
    LD [timer_count], A
    LDH A, [$ff05]
    LD [timer_tima], A
    LDH A, [$ff0f]
    LD [timer_if], A
    POP AF
    RETI
joypad_handler:
    PUSH AF
    LD A, [joypad_count]
    INC A
    LD [joypad_count], A
    LDH A, [$ff00]
    LD [joypad_p1], A
    LDH A, [$ff0f]
    LD [joypad_if], A
    POP AF
    RETI
serial_handler:
    PUSH AF
    LD A, [serial_count]
    INC A
    LD [serial_count], A
    LDH A, [$ff01]
    LD [serial_sb], A
    LDH A, [$ff02]
    LD [serial_sc], A
    POP AF
    RETI
rom_byte:
    DB $a7
serial_text:
    DB $4e
    DB $32
    DB $4d
timer_count EQU $c0f0
timer_tima EQU $c0f1
timer_if EQU $c0f2
joypad_count EQU $c0f3
joypad_p1 EQU $c0f4
joypad_if EQU $c0f5
serial_count EQU $c0f6
serial_sb EQU $c0f7
serial_sc EQU $c0f8
SECTION "timer_vector", ROM
    JP timer_handler
SECTION "serial_vector", ROM
    JP serial_handler
SECTION "joypad_vector", ROM
    JP joypad_handler
