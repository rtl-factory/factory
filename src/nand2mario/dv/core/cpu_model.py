"""Independent architectural LR35902 oracle, organized by ISA x/y/z fields.

No RTL, imported opcode dictionary or donor test implementation is consulted.
Cycles are LR35902 machine cycles, excluding initial opcode pipeline fill.
"""
from dataclasses import dataclass, field

ILLEGAL = {0xD3, 0xDB, 0xDD, 0xE3, 0xE4, 0xEB, 0xEC, 0xED, 0xF4, 0xFC, 0xFD}


@dataclass
class CPU:
    mem: bytearray = field(default_factory=lambda: bytearray(65536))
    # DMG ABC post-boot registers: B C D E H L (HL) A, F.
    r: list = field(default_factory=lambda: [0x00, 0x13, 0x00, 0xD8, 0x01, 0x4D, 0, 0x01])
    f: int = 0xB0
    sp: int = 0xFFFE
    pc: int = 0x100
    ime: bool = False
    delayed: bool = False
    halted: bool = False
    stopped: bool = False
    bug: bool = False
    writes: list = field(default_factory=list)

    def pair(self, p, stack=False):
        return ((self.r[7] << 8) | self.f) if stack and p == 3 else self.sp if p == 3 else (self.r[p*2] << 8) | self.r[p*2+1]

    def putpair(self, p, value, stack=False):
        value &= 65535
        if p == 3:
            if stack:
                self.r[7], self.f = value >> 8, value & 0xF0
            else:
                self.sp = value
        else:
            self.r[p*2:p*2+2] = [value >> 8, value & 255]

    def read(self, index):
        return self.mem[self.pair(2)] if index == 6 else self.r[index]

    def write(self, address, value):
        address &= 65535
        self.mem[address] = value & 255
        self.writes.append((address, value & 255))

    def put(self, index, value):
        if index == 6:
            self.write(self.pair(2), value)
        else:
            self.r[index] = value & 255

    def fetch(self):
        value = self.mem[self.pc]
        self.pc = (self.pc + 1) & 65535
        self.bytes.append(value)
        return value

    def word(self):
        lo = self.fetch()
        return lo | self.fetch() << 8

    def push(self, value):
        self.sp = (self.sp - 1) & 65535
        self.write(self.sp, value >> 8)
        self.sp = (self.sp - 1) & 65535
        self.write(self.sp, value)

    def pop(self):
        value = self.mem[self.sp] | self.mem[(self.sp + 1) & 65535] << 8
        self.sp = (self.sp + 2) & 65535
        return value

    def condition(self, y):
        return [not self.f & 128, bool(self.f & 128), not self.f & 16, bool(self.f & 16)][y]

    def alu(self, operation, value):
        a = self.r[7]
        carry = int(bool(self.f & 16)) if operation in (1, 3) else 0
        if operation in (0, 1, 2, 3, 7):
            sub = operation in (2, 3, 7)
            result = a - value - carry if sub else a + value + carry
            half = (a & 15) < (value & 15) + carry if sub else (a & 15) + (value & 15) + carry > 15
            self.f = (128 if result & 255 == 0 else 0) | (64 if sub else 0) | (32 if half else 0) | (16 if result < 0 or result > 255 else 0)
            if operation != 7:
                self.r[7] = result & 255
        else:
            result = a & value if operation == 4 else a ^ value if operation == 5 else a | value
            self.r[7] = result
            self.f = (128 if result == 0 else 0) | (32 if operation == 4 else 0)

    def rotate(self, operation, value):
        carry = int(bool(self.f & 16))
        if operation == 0: result, out = ((value << 1) | (value >> 7)) & 255, value >> 7
        elif operation == 1: result, out = (value >> 1) | ((value & 1) << 7), value & 1
        elif operation == 2: result, out = ((value << 1) | carry) & 255, value >> 7
        elif operation == 3: result, out = (value >> 1) | (carry << 7), value & 1
        elif operation == 4: result, out = (value << 1) & 255, value >> 7
        elif operation == 5: result, out = (value >> 1) | (value & 128), value & 1
        elif operation == 6: result, out = ((value << 4) | (value >> 4)) & 255, 0
        else: result, out = value >> 1, value & 1
        self.f = (128 if result == 0 else 0) | out << 4
        return result

    def step(self, pending=0, selected=False):
        before = self.pc
        self.bytes = []
        self.writes = []
        op = self.fetch()
        if self.bug:
            self.pc = before
            self.bug = False
        if op in ILLEGAL:
            raise ValueError(f'illegal opcode {op:02x}')
        x, y, z = op >> 6, (op >> 3) & 7, op & 7
        p, q = y >> 1, y & 1
        cycles = 1
        old_delay = self.delayed
        self.delayed = False
        if x == 0:
            if z == 0:
                if y == 1:
                    address = self.word(); self.write(address, self.sp); self.write(address+1, self.sp >> 8); cycles = 5
                elif y == 2:
                    if not pending: self.fetch()
                    self.halted = selected and not pending
                    self.stopped = not selected
                elif y >= 3:
                    offset = self.fetch(); offset -= 256 if offset & 128 else 0
                    taken = y == 3 or self.condition(y-4)
                    if taken: self.pc = (self.pc + offset) & 65535
                    cycles = 3 if taken else 2
            elif z == 1:
                if not q: self.putpair(p, self.word()); cycles = 3
                else:
                    a, b = self.pair(2), self.pair(p)
                    self.putpair(2, a+b)
                    self.f = (self.f & 128) | (32 if (a & 4095)+(b & 4095)>4095 else 0) | (16 if a+b>65535 else 0)
                    cycles = 2
            elif z == 2:
                address = self.pair(p if p < 2 else 2)
                if q: self.r[7] = self.mem[address]
                else: self.write(address, self.r[7])
                if p >= 2: self.putpair(2, address + (1 if p == 2 else -1))
                cycles = 2
            elif z == 3: self.putpair(p, self.pair(p) + (-1 if q else 1)); cycles = 2
            elif z in (4, 5):
                value = self.read(y); result = (value + (1 if z == 4 else -1)) & 255
                self.put(y, result)
                self.f = (self.f & 16) | (128 if result == 0 else 0) | (64 if z == 5 else 0) | (32 if (value & 15) == (15 if z == 4 else 0) else 0)
                cycles = 3 if y == 6 else 1
            elif z == 6: self.put(y, self.fetch()); cycles = 3 if y == 6 else 2
            elif y < 4: self.r[7] = self.rotate(y, self.r[7]); self.f &= 0x70
            elif y == 4:
                a = self.r[7]; correction = 0; carry = bool(self.f & 16)
                if self.f & 64:
                    if self.f & 32: correction += 6
                    if carry: correction += 0x60
                    a -= correction
                else:
                    if (self.f & 32) or (a & 15) > 9: correction += 6
                    if carry or a > 0x99: correction += 0x60; carry = True
                    a += correction
                self.r[7] = a & 255
                self.f = (self.f & 64) | (128 if self.r[7] == 0 else 0) | (16 if carry else 0)
            elif y == 5: self.r[7] ^= 255; self.f |= 0x60
            elif y == 6: self.f = (self.f & 128) | 16
            else: self.f = (self.f & 128) | ((self.f ^ 16) & 16)
        elif x == 1:
            if op == 0x76:
                self.halted = not pending
                self.bug = bool(pending and not (self.ime or old_delay))
                if pending and (self.ime or old_delay): self.pc = before
            else: self.put(y, self.read(z)); cycles = 2 if 6 in (y, z) else 1
        elif x == 2: self.alu(y, self.read(z)); cycles = 2 if z == 6 else 1
        else:
            if z == 0:
                if y < 4:
                    taken = self.condition(y)
                    if taken: self.pc = self.pop()
                    cycles = 5 if taken else 2
                elif y in (4, 6):
                    address = 0xFF00 + self.fetch()
                    if y == 4: self.write(address, self.r[7])
                    else: self.r[7] = self.mem[address]
                    cycles = 3
                else:
                    offset = self.fetch(); sp = self.sp
                    self.f = (32 if (sp & 15)+(offset & 15)>15 else 0) | (16 if (sp & 255)+offset>255 else 0)
                    value = sp + (offset-256 if offset & 128 else offset)
                    if y == 5: self.sp = value & 65535; cycles = 4
                    else: self.putpair(2, value); cycles = 3
            elif z == 1:
                if not q: self.putpair(p, self.pop(), True); cycles = 3
                elif p < 2:
                    self.pc = self.pop(); cycles = 4
                    if p == 1: self.ime = True
                elif p == 2: self.pc = self.pair(2)
                else: self.sp = self.pair(2); cycles = 2
            elif z == 2:
                if y < 4:
                    address = self.word(); taken = self.condition(y)
                    if taken: self.pc = address
                    cycles = 4 if taken else 3
                else:
                    address = 0xFF00+self.r[1] if y in (4, 6) else self.word()
                    if y < 6: self.write(address, self.r[7])
                    else: self.r[7] = self.mem[address]
                    cycles = 2 if y in (4, 6) else 4
            elif z == 3:
                if y == 0: self.pc = self.word(); cycles = 4
                elif y == 1:
                    cb = self.fetch(); bx, by, bz = cb >> 6, (cb >> 3) & 7, cb & 7
                    value = self.read(bz)
                    if bx == 0: self.put(bz, self.rotate(by, value))
                    elif bx == 1: self.f = (self.f & 16) | 32 | (128 if not value & (1 << by) else 0)
                    elif bx == 2: self.put(bz, value & ~(1 << by))
                    else: self.put(bz, value | (1 << by))
                    cycles = (3 if bx == 1 else 4) if bz == 6 else 2
                elif y == 6: self.ime = False; old_delay = False
                elif y == 7: self.delayed = not self.ime
            elif z == 4:
                address = self.word(); taken = self.condition(y)
                if taken: self.push(self.pc); self.pc = address
                cycles = 6 if taken else 3
            elif z == 5:
                if not q: self.push(self.pair(p, True)); cycles = 4
                else: address = self.word(); self.push(self.pc); self.pc = address; cycles = 6
            elif z == 6: self.alu(y, self.fetch()); cycles = 2
            else: self.push(self.pc); self.pc = y*8; cycles = 4
        if old_delay: self.ime = True; self.delayed = False
        return {'pc_before': before, 'pc_after': self.pc,
                'opcode': sum(b << (8*i) for i, b in enumerate(self.bytes)),
                'opcode_length': len(self.bytes), 'a': self.r[7], 'f': self.f,
                **dict(zip('bcdehl', self.r[:6])), 'sp': self.sp,
                'ime': int(self.ime), 'halted': int(self.halted),
                'stopped': int(self.stopped), 'is_interrupt': 0}, cycles, list(self.writes)
