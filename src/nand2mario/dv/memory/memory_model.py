"""Independent Python model of the nand2mario Game Boy memory map.

Derived from the memory HAS, not from the RTL: region bounds, echo aliasing,
the cartridge windows forwarded to a cartridge model, open-bus defaults for
the unusable window, the peripheral port and the IE register. Byte indexing
lets the CPU oracle use it as its memory, so composed system tests share one
reference.
"""
from ..cartridge.mbc1_model import Cartridge

OPEN_BUS = 0xFF
ROM_BYTES, VRAM_BYTES, WRAM_BYTES, OAM_BYTES, HRAM_BYTES = 32768, 8192, 8192, 160, 127
CARTRIDGE = {'rom', 'cart_ram'}

REGIONS = (
    (0x0000, 0x7FFF, 'rom'),
    (0x8000, 0x9FFF, 'vram'),
    (0xA000, 0xBFFF, 'cart_ram'),
    (0xC000, 0xDFFF, 'wram'),
    (0xE000, 0xFDFF, 'echo'),
    (0xFE00, 0xFE9F, 'oam'),
    (0xFEA0, 0xFEFF, 'unusable'),
    (0xFF00, 0xFF7F, 'io'),
    (0xFF80, 0xFFFE, 'hram'),
    (0xFFFF, 0xFFFF, 'ie'),
)
STORED = {'rom', 'vram', 'wram', 'oam', 'hram', 'ie'}


def destination(address):
    """Return (destination, offset) for a 16-bit address."""
    address &= 0xFFFF
    for first, last, name in REGIONS:
        if first <= address <= last:
            if name == 'echo':
                return 'wram', address - 0xE000
            return name, address - first
    raise AssertionError('unreachable: regions tile the address space')


def boundaries():
    """Every first and last address of every region, for directed probes."""
    return sorted({first for first, _, _ in REGIONS} | {last for _, last, _ in REGIONS})


class UndefinedRead(AssertionError):
    """A store byte was read before anything defined it."""


class GameBoyMemory:
    """Memory map model with a pluggable peripheral port responder and a
    cartridge model behind the ROM and cartridge RAM windows.

    Store bytes have no power-up value (memory HAS): a byte is *defined* only
    after a load (ROM) or a write (RAM) has established it. Reading an
    undefined store byte raises in strict mode; a lenient caller checks
    ``is_defined`` first and compares nothing for undefined bytes. The
    cartridge object answers ``read``, ``write``, ``is_defined`` and
    ``reset``; the default is the independent MBC1 model.
    """

    def __init__(self, rom=b'', io_read=None, io_write=None, strict=True, cartridge=None):
        self.cartridge = Cartridge() if cartridge is None else cartridge
        self.vram = bytearray(VRAM_BYTES)
        self.wram = bytearray(WRAM_BYTES)
        self.oam = bytearray(OAM_BYTES)
        self.hram = bytearray(HRAM_BYTES)
        self.defined = {name: set() for name in ('vram', 'wram', 'oam', 'hram')}
        self.load_rom(rom)
        self.ie = 0
        self.strict = strict
        self.io_read = io_read or (lambda address: OPEN_BUS)
        self.io_write = io_write or (lambda address, value: None)

    def store(self, name):
        return getattr(self, name)

    def load_rom(self, image, start=0):
        """Define cartridge ROM bytes the way the load port does, one byte each."""
        self.cartridge.load_image(image, start)

    def is_defined(self, address):
        name, offset = destination(address)
        if name in CARTRIDGE:
            return self.cartridge.is_defined(address & 0xFFFF)
        return name not in self.defined or offset in self.defined[name]

    def defined_bytes(self, name):
        """Sorted (offset, value) pairs of the defined bytes of one store."""
        return [(offset, self.store(name)[offset]) for offset in sorted(self.defined[name])]

    def __getitem__(self, address):
        name, offset = destination(address)
        if name in CARTRIDGE:
            if self.strict and not self.cartridge.is_defined(address & 0xFFFF):
                raise UndefinedRead(f'cartridge byte at {address & 0xFFFF:04x} read before it was defined')
            return self.cartridge.read(address & 0xFFFF)
        if name == 'unusable':
            return OPEN_BUS
        if name == 'io':
            return self.io_read(address & 0xFFFF) & 0xFF
        if name == 'ie':
            return self.ie
        if self.strict and offset not in self.defined[name]:
            raise UndefinedRead(f'{name} byte {offset:04x} read before it was defined')
        return self.store(name)[offset]

    def __setitem__(self, address, value):
        value &= 0xFF
        name, offset = destination(address)
        if name in CARTRIDGE:
            self.cartridge.write(address & 0xFFFF, value)
            return
        if name == 'unusable':
            return
        if name == 'io':
            self.io_write(address & 0xFFFF, value)
        elif name == 'ie':
            self.ie = value
        else:
            self.store(name)[offset] = value
            self.defined[name].add(offset)

    def interrupt_enable(self):
        """The five IE bits the CPU consumes."""
        return self.ie & 0x1F
