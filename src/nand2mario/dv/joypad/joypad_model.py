"""Independent Python model of the nand2mario Game Boy joypad register P1.

Written from the joypad HAS, not from the RTL: eight active-high buttons
sampled on every system edge, two active-low row selects in P1 bits [5:4],
the selected rows' lines read active-low in bits [3:0] (unselected rows and
bits [7:6] read 1), and a one-edge event whenever a selected line falls,
whether a button was pressed or a select write exposed a pressed button.
"""

P1 = 0xFF00
RIGHT, LEFT, UP, DOWN, A, B, SELECT, START = 1, 2, 4, 8, 16, 32, 64, 128
BUTTONS = {'right': RIGHT, 'left': LEFT, 'up': UP, 'down': DOWN,
           'a': A, 'b': B, 'select': SELECT, 'start': START}
SELECT_DIRECTIONS = 0b10   # Ref: Pan Docs "Joypad Input": P1 bit 4 low selects the direction row.
SELECT_ACTIONS = 0b01      # Ref: Pan Docs "Joypad Input": P1 bit 5 low selects the action row.
SELECT_BOTH = 0b00
SELECT_NONE = 0b11


def pressed_lines(buttons, select):
    """Active-high lines of the selected rows: directions in bits [3:0] of
    ``buttons``, actions in bits [7:4]; both rows OR together."""
    lines = 0
    if not select & 1:
        lines |= buttons & 0xF
    if not select & 2:
        lines |= (buttons >> 4) & 0xF
    return lines


class JoypadModel:
    def __init__(self):
        self.reset()

    def reset(self):
        self.buttons = 0
        self.select = SELECT_BOTH

    def lines(self):
        return pressed_lines(self.buttons, self.select)

    def read(self):
        return 0xC0 | (self.select << 4) | (~self.lines() & 0xF)

    def selected_active(self):
        return self.lines() != 0

    def edge(self, buttons=None, write=None):
        """One system edge: ``buttons`` is the sampled button byte (None keeps
        the previous sample), ``write`` an optional P1 byte from the CPU.
        Returns True when a selected line fell on this edge, which is the
        interrupt request and STOP wake pulse of the following cycle."""
        old = self.lines()
        if buttons is not None:
            self.buttons = buttons & 0xFF
        if write is not None:
            self.select = (write >> 4) & 3
        return bool(self.lines() & ~old)
