"""Independent Python model of the nand2mario Game Boy timer.

Written from the timer HAS, not from the RTL: a machine-cycle divider, DIV as
its top byte, TIMA counting falling edges of the TAC-selected divider bit
(including edges produced by DIV writes, TAC writes and the STOP divider
reset), the one-machine-cycle $00 window after an overflow, the reload from
TMA with its interrupt, and the TIMA/TMA write rules around the reload.

The model advances one system edge at a time (``edge``) so the focused test
can place register writes anywhere relative to the machine-cycle ticks; the
system oracle advances it tick by tick (``tick``).
"""

DIV, TIMA, TMA, TAC = 0xFF04, 0xFF05, 0xFF06, 0xFF07
REGISTERS = (DIV, TIMA, TMA, TAC)
# DMG ABC boot-ROM handoff: internal counter $ABCC T-cycles, DIV $AB.
DIVIDER_RESET = 0x2AF3
DIVIDER_BITS = 14
# TAC[1:0] -> divider bit whose falling edge increments TIMA, and its period
# in machine cycles (4096, 262144, 65536 and 16384 Hz at 1,048,576 Hz).
SELECT_BIT = {0: 7, 1: 1, 2: 3, 3: 5}
PERIOD = {select: 2 << bit for select, bit in SELECT_BIT.items()}

IDLE, PENDING, HOLD = 'idle', 'pending', 'hold'


class TimerModel:
    def __init__(self):
        self.reset()

    def reset(self):
        self.divider = DIVIDER_RESET
        self.tima = 0
        self.tma = 0
        self.tac = 0
        self.reload = IDLE

    @staticmethod
    def selected(divider, tac):
        return bool(tac & 4) and bool((divider >> SELECT_BIT[tac & 3]) & 1)

    def read(self, address):
        if address == DIV:
            return (self.divider >> (DIVIDER_BITS - 8)) & 0xFF
        if address == TIMA:
            return self.tima
        if address == TMA:
            return self.tma
        if address == TAC:
            return 0xF8 | (self.tac & 7)
        raise KeyError(address)

    def tick(self, write=None, divider_reset=False):
        return self.edge(True, write, divider_reset)

    def edge(self, tick=False, write=None, divider_reset=False):
        """One system edge. ``write`` is an optional (address, value) applied
        on this edge; ``tick`` marks a machine-cycle enable. Returns True when
        TIMA was reloaded from TMA on this edge (the interrupt request)."""
        address, value = write if write else (None, None)
        # Divider: advance, then DIV write or STOP reset; TAC and TMA update.
        advanced = (self.divider + (1 if tick else 0)) % (1 << DIVIDER_BITS)
        divider = 0 if (address == DIV or divider_reset) else advanced
        tac = (value & 7) if address == TAC else self.tac
        tma = value if address == TMA else self.tma
        old = self.selected(self.divider, self.tac)
        mid = self.selected(advanced, self.tac)
        new = self.selected(divider, tac)
        falling = (old and not mid) or (mid and not new)

        reload_now = self.reload == PENDING and tick
        tima_write = address == TIMA and self.reload != HOLD and not reload_now
        tima, reload, interrupt = self.tima, self.reload, False
        if reload_now:
            tima, reload, interrupt = tma, HOLD, True
        elif self.reload == HOLD:
            if tick:
                reload = IDLE
            base = value if address == TMA else self.tima
            tima = base
            if falling:
                tima = (base + 1) & 0xFF
                if base == 0xFF:
                    reload = PENDING
        elif self.reload == PENDING:
            if tima_write:
                tima, reload = value, IDLE
        else:
            if tima_write:
                tima = value
            elif falling:
                tima = (self.tima + 1) & 0xFF
                if self.tima == 0xFF:
                    reload = PENDING
        self.divider, self.tac, self.tma, self.tima, self.reload = divider, tac, tma, tima, reload
        return interrupt
