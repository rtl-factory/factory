"""Independent Python model of the nand2mario cartridge mapper.

Written from the cartridge HAS and Pan Docs (MBC1), not from the RTL. The
model owns the header bytes it has seen on the load port, the MBC1 registers,
the physical ROM and RAM images and which physical bytes are defined. Two
views are offered: ``translate`` gives the backing-level expectation (which
store and physical address a CPU access reaches, or that it is answered
locally), and ``read``/``write`` give the byte-level behavior the memory map
model and the CPU oracle consume.
"""

ROM_ONLY, MBC1, MBC1_RAM, MBC1_RAM_BATTERY = 0x00, 0x01, 0x02, 0x03
ROM_BANK_BYTES, RAM_BANK_BYTES = 16384, 8192
MAX_ROM_BYTES, MAX_RAM_BYTES = 1 << 21, 1 << 15
HEADER_TYPE, HEADER_ROM_SIZE, HEADER_RAM_SIZE = 0x147, 0x148, 0x149
RAMG, BANK1, BANK2, MODE = 'ramg', 'bank1', 'bank2', 'mode'
OPEN_BUS = 0xFF
BANK1_RESET = 1


class UndefinedHeader(AssertionError):
    """A cartridge access needs header bytes nothing has loaded."""


class UndefinedByte(AssertionError):
    """A physical ROM/RAM byte was read before anything defined it."""


def rom_bank_mask(code):
    """Header $0148: 2 << code banks up to 2 MiB; larger codes keep all seven bits."""
    return (2 << code) - 1 if code <= 6 else 0x7F


def ram_bank_mask(code):
    """Header $0149: $03 and the larger codes bank the RAM in four 8 KiB banks."""
    return 3 if code in (3, 4, 5) else 0


def has_ram_capacity(code):
    """Header $0149: $00 (and the unused $01) declare no RAM at all."""
    return code in (2, 3, 4, 5)


def is_mbc1(cartridge_type):
    return cartridge_type in (MBC1, MBC1_RAM, MBC1_RAM_BATTERY)


def has_ram(cartridge_type):
    return cartridge_type in (MBC1_RAM, MBC1_RAM_BATTERY)


def register_of(address):
    """MBC1 register selected by a write inside the ROM window."""
    return (RAMG, BANK1, BANK2, MODE)[(address >> 13) & 3]


class Cartridge:
    def __init__(self, image=b''):
        self.rom = bytearray(MAX_ROM_BYTES)
        self.rom_known = bytearray(MAX_ROM_BYTES)
        self.ram = bytearray(MAX_RAM_BYTES)
        self.ram_known = bytearray(MAX_RAM_BYTES)
        self.header = {HEADER_TYPE: None, HEADER_ROM_SIZE: None, HEADER_RAM_SIZE: None}
        self.registers = {}
        self.reset()
        self.load_image(image)

    def load(self, address, byte):
        """One load-port write: the physical ROM byte and, for $0147-$0149,
        the header register the mapper captures."""
        address &= MAX_ROM_BYTES - 1
        self.rom[address] = byte & 0xFF
        self.rom_known[address] = 1
        if address in self.header:
            self.header[address] = byte & 0xFF

    def load_image(self, image, start=0):
        for offset, byte in enumerate(image, start):
            self.load(offset, byte)

    def reset(self):
        """Console reset: MBC1 registers return to their reset values; the
        header and both stores are the inserted cartridge and stay."""
        self.registers = {BANK1: BANK1_RESET, BANK2: 0, MODE: 0, RAMG: False}

    def header_byte(self, offset):
        value = self.header[offset]
        if value is None:
            raise UndefinedHeader(f'header byte {offset:04x} was never loaded')
        return value

    @property
    def cartridge_type(self):
        return self.header_byte(HEADER_TYPE)

    @property
    def mbc1(self):
        return is_mbc1(self.cartridge_type)

    @property
    def ram_present(self):
        """RAM exists only when the type has RAM and the size declares capacity."""
        return has_ram(self.cartridge_type) and has_ram_capacity(self.header_byte(HEADER_RAM_SIZE))

    @property
    def rom_mask(self):
        return rom_bank_mask(self.header_byte(HEADER_ROM_SIZE))

    @property
    def ram_mask(self):
        return ram_bank_mask(self.header_byte(HEADER_RAM_SIZE))

    def rom_bank(self, address):
        """Effective ROM bank of a CPU address in $0000-$7FFF. A ROM-only or
        unsupported type is the identity whatever the (possibly stale) MBC1
        registers hold."""
        if not self.mbc1:
            return (1 if address & 0x4000 else 0) & self.rom_mask
        bank1 = self.registers[BANK1] or 1
        bank2 = self.registers[BANK2]
        if address & 0x4000:
            bank = (bank2 << 5) | bank1
        else:
            bank = (bank2 << 5) if self.registers[MODE] else 0
        return bank & self.rom_mask

    def ram_bank(self):
        if not self.mbc1:
            return 0
        return (self.registers[BANK2] if self.registers[MODE] else 0) & self.ram_mask

    def translate(self, address, write):
        """('rom'|'ram', physical address) for an access the backing serves,
        ('local', None) for one the mapper answers itself (register writes,
        ROM-only writes, disabled or absent RAM)."""
        address &= 0xFFFF
        if address <= 0x7FFF:
            if write:
                return 'local', None
            return 'rom', (self.rom_bank(address) << 14) | (address & 0x3FFF)
        assert 0xA000 <= address <= 0xBFFF, f'{address:04x} is not a cartridge address'
        if not (self.ram_present and self.registers[RAMG]):
            return 'local', None
        return 'ram', (self.ram_bank() << 13) | (address & 0x1FFF)

    def write(self, address, value):
        value &= 0xFF
        store, physical = self.translate(address, write=True)
        if store == 'ram':
            self.ram[physical] = value
            self.ram_known[physical] = 1
        elif address <= 0x7FFF and self.mbc1:
            register = register_of(address)
            if register == RAMG:
                self.registers[RAMG] = (value & 0x0F) == 0x0A
            elif register == BANK1:
                self.registers[BANK1] = value & 0x1F
            elif register == BANK2:
                self.registers[BANK2] = value & 0x03
            else:
                self.registers[MODE] = value & 0x01

    def is_defined(self, address):
        store, physical = self.translate(address, write=False)
        if store == 'rom':
            return bool(self.rom_known[physical])
        if store == 'ram':
            return bool(self.ram_known[physical])
        return True

    def read(self, address):
        store, physical = self.translate(address, write=False)
        if store == 'local':
            return OPEN_BUS
        known, data = (self.rom_known, self.rom) if store == 'rom' else (self.ram_known, self.ram)
        if not known[physical]:
            raise UndefinedByte(f'{store} byte {physical:06x} read before it was defined')
        return data[physical]

    def defined_ram_bytes(self):
        """Sorted (physical offset, value) pairs of every written RAM byte."""
        return [(offset, self.ram[offset]) for offset in range(MAX_RAM_BYTES) if self.ram_known[offset]]
