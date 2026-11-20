"""Independent Python model of the nand2mario Game Boy serial port.

Written from the serial HAS, not from the RTL: SB and SC registers, an
internal-clock transfer that shifts SB left one bit on each pulse of a
free-running 8192 Hz shift clock (every 128 machine-cycle ticks, the falling
edge of divider bit 6) shifting in 1 (no partner), the transmitted byte
collected most significant bit first, completion after the eighth shift
clearing SC bit 7 with the interrupt request, external-clock mode that never
shifts, writes winning over a same-edge shift and an SC write restarting the
bit count but not the free-running clock.
"""

SB, SC = 0xFF01, 0xFF02
REGISTERS = (SB, SC)
BIT_PERIOD = 128          # machine cycles per bit at 8192 Hz
BYTE_PERIOD = 8 * BIT_PERIOD
SC_UNUSED = 0x7E


class SerialModel:
    def __init__(self):
        self.reset()

    def reset(self):
        self.sb = 0
        self.enable = False
        self.clock = False
        self.prescale = 0
        self.bits = 0
        self.tx_shift = 0
        self.tx_data = 0

    @property
    def active(self):
        return self.enable and self.clock

    def read(self, address):
        if address == SB:
            return self.sb
        if address == SC:
            return (0x80 if self.enable else 0) | SC_UNUSED | (1 if self.clock else 0)
        raise KeyError(address)

    @property
    def shift_clock(self):
        """Whether the next tick carries a shift clock pulse."""
        return self.prescale == BIT_PERIOD - 1

    def tick(self, write=None):
        return self.edge(True, write)

    def edge(self, tick=False, write=None, clock=None):
        """One system edge. ``write`` is an optional (address, value) applied
        on this edge; ``tick`` marks a machine-cycle enable. Returns the
        transmitted byte when the eighth shift completed on this edge (the
        interrupt and observation pulse of the following cycle), else None.
        ``clock``, when given, is the shift clock pulse itself (the system
        oracle derives it from the timer divider) in place of the model's
        own free-running count."""
        address, value = write if write else (None, None)
        completed = None
        if clock is None:
            clock = tick and self.prescale == BIT_PERIOD - 1
        if tick:
            self.prescale = (self.prescale + 1) % BIT_PERIOD
        if self.active and clock:
            self.tx_shift = ((self.tx_shift << 1) | (self.sb >> 7)) & 0xFF
            self.sb = ((self.sb << 1) | 1) & 0xFF
            self.bits += 1
            if self.bits == 8:
                self.bits = 0
                self.enable = False
                self.tx_data = self.tx_shift
                completed = self.tx_data
        if address == SB:
            self.sb = value & 0xFF
        elif address == SC:
            self.enable = bool(value & 0x80)
            self.clock = bool(value & 0x01)
            self.bits = 0
        return completed
