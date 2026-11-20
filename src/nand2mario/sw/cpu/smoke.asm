; Original bounded CPU program: sum 1..10, call/return, memory and CB flags.
SECTION "code", ROM
start:
    LD SP, $dffe
    LD HL, $c000
    LD A, 0
    LD B, 10
sum_loop:
    ADD A, B
    DEC B
    JR NZ, sum_loop
    CALL transform
    LD [HL], A
    BIT 7, [HL]
    LD A, $a5
    LD [$c001], A
    HALT
transform:
    PUSH BC
    LD B, A
    RLC B
    LD A, B
    POP BC
    RET
