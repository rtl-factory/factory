"""Independent Python model of the nand2mario OAM DMA.

Written from the DMA HAS, not from the RTL: the $FF46 register stores the
source page; the transfer is armed at the first machine tick after the write
and starts at the next, copying one byte per machine cycle from page
$XX (pages $E0-$FF alias $XX - $20) offsets $00-$9F into OAM; the CPU
restriction (OAM $FE00-$FEFF and the bus the source sits on: VRAM
$8000-$9FFF for a $80-$9F source, else the external bus $0000-$7FFF and
$A000-$FDFF; blocked accesses read $FF and drop writes) lasts exactly the 160 transfer
cycles; a second write before the transfer starts restarts it after the
same delay. Nothing here reads RTL state.
"""

DMA = 0xFF46
TRANSFER_BYTES = 160
HRAM_FIRST, HRAM_LAST = 0xFF80, 0xFFFE


def source_page(page):
    return page - 0x20 if page >= 0xE0 else page


def restricted(address, page):
    """True when a CPU access to ``address`` is blocked during a transfer
    from source ``page`` (DMG external/VRAM bus split)."""
    if 0xFE00 <= address <= 0xFEFF:
        return True
    if address >= 0xFF00:
        return False
    vram = 0x8000 <= address <= 0x9FFF
    return vram == (0x80 <= source_page(page) <= 0x9F)


class DmaModel:
    """Tick-stepped engine. ``write`` records a $FF46 write between ticks;
    ``tick`` applies one machine-cycle boundary and returns the offset of
    the byte transferred in the machine cycle that begins at this tick, or
    None. ``active`` is the restriction level after the tick."""

    def __init__(self):
        self.page = 0
        self.pending = False
        self.armed = False
        self.active = False
        self.offset = 0

    def reset(self):
        self.__init__()

    def read(self):
        return self.page

    def write(self, value):
        self.page = value & 0xFF
        self.pending = True

    def tick(self):
        byte = None
        if self.armed:
            self.active, self.offset, byte = True, 0, 0
        elif self.active:
            if self.offset == TRANSFER_BYTES - 1:
                self.active = False
            else:
                self.offset += 1
                byte = self.offset
        self.armed, self.pending = self.pending, False
        return byte

    def source_address(self, offset):
        return (source_page(self.page) << 8) | offset


def transfer_image(memory, page):
    """The 160 bytes a transfer from ``page`` copies, read through the
    memory model (ROM, VRAM, WRAM or open bus for absent cartridge RAM)."""
    base = source_page(page) << 8
    return bytes(memory[base + offset] for offset in range(TRANSFER_BYTES))
