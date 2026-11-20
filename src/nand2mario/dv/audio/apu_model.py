"""Python model of the silent DMG APU register block (audio HAS).

Reference behaviour: Pan Docs and SameBoy Core/apu.c (MIT); restated, not copied.
"""

REGISTER_START, NR52, WAVE_START, WAVE_END = 0xFF10, 0xFF26, 0xFF30, 0xFF3F
# Read-back masks; read = mask | stored byte. SameBoy read_mask (MIT).
MASKS = {
    0xFF10: 0x80, 0xFF11: 0x3F, 0xFF12: 0x00, 0xFF13: 0xFF, 0xFF14: 0xBF,
    0xFF16: 0x3F, 0xFF17: 0x00, 0xFF18: 0xFF, 0xFF19: 0xBF,
    0xFF1A: 0x7F, 0xFF1B: 0xFF, 0xFF1C: 0x9F, 0xFF1D: 0xFF, 0xFF1E: 0xBF,
    0xFF20: 0xFF, 0xFF21: 0x00, 0xFF22: 0x00, 0xFF23: 0xBF,
    0xFF24: 0x00, 0xFF25: 0x00, 0xFF26: 0x70,
}
NR10, NR11, NR12, NR13, NR14 = range(5)
NR21, NR22, NR24, NR30, NR31, NR34, NR41, NR42, NR44 = 6, 7, 9, 10, 11, 14, 16, 17, 19
FULL = (64, 64, 256, 64)


def sweep_calc(shadow, nr10):
    delta = shadow >> (nr10 & 7)
    return shadow - delta if nr10 & 8 else shadow + delta


def sweep_period(nr10):
    return ((nr10 >> 4) & 7) or 8


class ApuModel:
    def __init__(self):
        self.reset()

    def reset(self):
        self.wave = [0] * 16
        # DMG post-boot state: power on, channel 1 active (Pan Docs).
        self.regs = [0] * 22
        self.regs[NR11], self.regs[NR12], self.regs[20], self.regs[21] = 0xBF, 0xF3, 0x77, 0xF3
        self.power = 1
        self.active = [1, 0, 0, 0]
        self.step = 0
        self.length = [0] * 4
        self.shadow = 0
        self.sweep_timer = 0
        self.sweep_enable = 0

    @staticmethod
    def selected(address):
        return REGISTER_START <= address <= WAVE_END

    def dac(self, ch):
        r = self.regs
        return [r[NR12] >> 3 != 0, r[NR22] >> 3 != 0, r[NR30] >> 7, r[NR42] >> 3 != 0][ch]

    def read(self, address):
        if WAVE_START <= address <= WAVE_END:
            return self.wave[address & 0xF]
        if address == NR52:
            return (self.power << 7) | 0x70 | sum(a << i for i, a in enumerate(self.active))
        if REGISTER_START <= address < NR52:
            return MASKS.get(address, 0xFF) | self.regs[address - REGISTER_START]
        return 0xFF

    def sequencer_tick(self):
        """One frame-sequencer step (DIV bit 4 falling edge)."""
        if not self.power:
            return
        r = self.regs
        if self.step % 2 == 0:
            for i in range(4):
                if r[NR14 + 5 * i] & 0x40 and self.length[i]:
                    self.length[i] -= 1
                    if not self.length[i]:
                        self.active[i] = 0
        if self.step in (2, 6):
            timer = self.sweep_timer
            if timer:
                self.sweep_timer = timer - 1
            if timer == 1:
                self.sweep_timer = sweep_period(r[NR10])
                if self.sweep_enable and (r[NR10] >> 4) & 7:
                    new = sweep_calc(self.shadow, r[NR10])
                    if new > 2047:
                        self.active[0] = 0
                    elif r[NR10] & 7:
                        self.shadow = new
                        r[NR13] = new & 0xFF
                        r[NR14] = (r[NR14] & 0xF8) | (new >> 8)
                        if sweep_calc(new, r[NR10]) > 2047:
                            self.active[0] = 0
        self.step = (self.step + 1) & 7

    def write(self, address, data):
        """A write on the same edge as (after) any sequencer step."""
        data &= 0xFF
        if WAVE_START <= address <= WAVE_END:
            self.wave[address & 0xF] = data
            return
        if address == NR52:
            if self.power and not data & 0x80:
                self.regs = [0] * 22
                self.active = [0] * 4
                self.shadow = self.sweep_timer = self.sweep_enable = 0
            if not self.power and data & 0x80:
                self.step = 0
            self.power = data >> 7
            return
        if not REGISTER_START <= address < NR52:
            return
        index = address - REGISTER_START
        old = self.regs[index]
        if index in (NR11, NR21, NR41):
            self.length[index // 5] = 64 - (data & 0x3F)
        elif index == NR31:
            self.length[2] = 256 - data
        if not self.power:
            return
        self.regs[index] = data
        first_half = self.step % 2 == 1  # next step does not clock length
        if index in (NR14, NR24, NR34, NR44):
            ch = index // 5
            trigger = data & 0x80
            if data & 0x40 and not old & 0x40 and first_half and self.length[ch]:
                self.length[ch] -= 1
                if not self.length[ch] and not trigger:
                    self.active[ch] = 0
            if trigger:
                if self.dac(ch):
                    self.active[ch] = 1
                if not self.length[ch]:
                    self.length[ch] = FULL[ch] - (1 if data & 0x40 and first_half else 0)
                if ch == 0:
                    nr10 = self.regs[NR10]
                    freq = ((data & 7) << 8) | self.regs[NR13]
                    self.shadow = freq
                    self.sweep_timer = sweep_period(nr10)
                    self.sweep_enable = int(bool(nr10 & 0x70 or nr10 & 7))
                    if nr10 & 7 and sweep_calc(freq, nr10) > 2047:
                        self.active[0] = 0
        elif index in (NR12, NR22, NR42) and data >> 3 == 0:
            self.active[index // 5] = 0
        elif index == NR30 and not data & 0x80:
            self.active[2] = 0
