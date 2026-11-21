"""Peripheral-port oracle for the composed system: the timer, interrupt-flag,
joypad and serial models behind the memory model's $FF00-$FF7F hooks,
advanced in machine cycles by the system test so register reads and the
timer, serial and joypad interrupts are predicted at the exact tick of each
CPU access."""
from ..interrupts.interrupts_model import IF, JOYPAD, SERIAL, TIMER, InterruptsModel
from ..joypad.joypad_model import P1, JoypadModel
from ..serial.serial_model import REGISTERS as SERIAL_REGISTERS, SerialModel
from ..timer.timer_model import REGISTERS as TIMER_REGISTERS, TimerModel

OPEN_BUS = 0xFF
SERIAL_CLOCK_BIT = 6


class Peripherals:
    def __init__(self):
        self.timer = TimerModel()
        self.flags = InterruptsModel()
        self.joypad = JoypadModel()
        self.serial = SerialModel()
        self.ticks = 0
        self.reload_ticks = []
        self.completions = []
        self.joypad_event_ticks = []
        self.pending_presses = set()

    def advance(self, ticks):
        """Advance the tick-driven blocks to ``ticks`` machine cycles since
        reset release, raising the timer flag on every reload, the serial flag
        on every completed byte and the joypad flag at each scheduled press."""
        assert ticks >= self.ticks, 'the oracle never moves backwards'
        while self.ticks < ticks:
            self.ticks += 1
            sources = 0
            bit6 = self.timer.divider >> SERIAL_CLOCK_BIT & 1
            if self.timer.tick():
                self.reload_ticks.append(self.ticks)
                sources |= TIMER
            # Ref: Pan Docs, serial transfer: the internal clock shifts at 8192 Hz.
            # Why: divider bit 6 falls at that rate, so its falling edge is the shift clock.
            byte = self.serial.edge(clock=self.serial_clock(bit6))
            if byte is not None:
                self.completions.append((self.ticks, byte))
                sources |= SERIAL
            if self.ticks in self.pending_presses:
                self.pending_presses.remove(self.ticks)
                self.joypad_event_ticks.append(self.ticks)
                sources |= JOYPAD
            if sources:
                self.flags.edge(sources=sources)

    def serial_clock(self, before):
        return bool(before) and not (self.timer.divider >> SERIAL_CLOCK_BIT & 1)

    def press(self, buttons, tick):
        """The bench changes the buttons between two CPU accesses: the joypad
        sees them before the next access, and a selected line falling raises
        the joypad flag at ``tick``, the first machine cycle in which the CPU
        can see the flag the register set on the sampling edge."""
        assert tick > self.ticks, 'a press takes effect after the current access'
        if self.joypad.edge(buttons=buttons):
            self.pending_presses.add(tick)

    def read(self, address):
        if address in TIMER_REGISTERS:
            return self.timer.read(address)
        if address == IF:
            return self.flags.read()
        if address == P1:
            return self.joypad.read()
        if address in SERIAL_REGISTERS:
            return self.serial.read(address)
        return OPEN_BUS

    def write(self, address, value):
        if address in TIMER_REGISTERS:
            bit6 = self.timer.divider >> SERIAL_CLOCK_BIT & 1
            self.timer.edge(write=(address, value))
            if self.serial_clock(bit6):
                # Why: a DIV write that drops bit 6 is a shift clock edge too; a
                # completion here is not expected by the system programs.
                assert self.serial.edge(clock=True) is None
        elif address == IF:
            self.flags.edge(write=value)
        elif address == P1:
            # Why: a select write exposing a pressed button raises the flag on the
            # edge after the write, before the program's next access.
            if self.joypad.edge(write=value):
                self.flags.edge(sources=JOYPAD)
        elif address in SERIAL_REGISTERS:
            self.serial.edge(write=(address, value))

    def acknowledge(self, bit):
        self.flags.edge(ack=1 << bit)
