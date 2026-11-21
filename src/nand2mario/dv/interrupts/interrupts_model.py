"""Independent Python model of the nand2mario interrupt-flag register (IF).

From the interrupts HAS: five flags set by one-edge source requests, written
by the CPU in bits [4:0], cleared per bit by the CPU acknowledge; on one edge
acknowledge beats write beats source. Reads return bits [7:5] high.
"""

IF = 0xFF0F
VBLANK, STAT, TIMER, SERIAL, JOYPAD = 1, 2, 4, 8, 16
MASK = 0x1F


class InterruptsModel:
    def __init__(self):
        self.reset()

    def reset(self):
        # Ref: Pan Docs "Power Up Sequence": the DMG boot-ROM handoff leaves VBlank pending (IF reads $E1).
        self.flags = 0x01

    def read(self):
        return 0xE0 | self.flags

    def edge(self, sources=0, write=None, ack=0):
        """One system edge: ``sources`` is the set-request mask, ``write`` an
        optional CPU byte, ``ack`` the one-hot acknowledge mask."""
        assert ack & (ack - 1) == 0, 'acknowledge is one-hot or zero'
        flags = self.flags | (sources & MASK)
        if write is not None:
            flags = write & MASK
        self.flags = flags & ~ack & MASK
        return self.flags
