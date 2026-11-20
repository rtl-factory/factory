"""Hardware-free contract of the common JTAG keyboard host: wire protocol, persistent Tcl
process lifecycle, native-worker selection, scan-code translation and typing."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from src.common.tools.jtag import n2a_jtag_keyboard as jtag

ROOT = Path(__file__).resolve().parents[5]


def temporary_directory():
    output = ROOT / 'workdir'
    output.mkdir(exist_ok=True)
    return tempfile.TemporaryDirectory(dir=output, prefix='jtag-test-')


def wire(code=0, accepted=0, seq=0, total=0, busy=False):
    return (0x5a << 56) | (int(busy) << 55) | (code << 51) | (accepted << 48) | (seq << 32) | total


class Device:
    """Independent minimal wire peer: records the actual submitted byte prefix."""
    def __init__(self, *, initial=0x5a00000000000000, limit=4, defect=None):
        self.value, self.limit, self.defect = initial, limit, defect
        self.injected, self.commands = bytearray(), []
        self.closed = False
        self.entered, self.resume = threading.Event(), threading.Event()

    def scan(self, ir, value=0, *, deadline=None):
        if ir == 2:
            return self.value
        self.commands.append(value)
        self.entered.set()
        if self.defect == 'stall':
            if not self.resume.wait(max(0, deadline-time.monotonic())):
                raise jtag.TransferError('bounded stalled scan')
        count, seq = (value >> 48) & 255, (value >> 40) & 255
        assert value >> 56 == 0xa3 and not (value >> 32) & 255 and 1 <= count <= 4
        accepted = min(count, self.limit)
        self.injected += (value & 0xffffffff).to_bytes(4, 'little')[:accepted]
        total = ((self.value & 0xffffffff) + accepted) & 0xffffffff
        self.value = wire(1 if accepted == count else 2, accepted, seq, total)
        if self.defect == 'lost':
            raise jtag.TransferError('lost reply after submit')
        if self.defect == 'reset': self.value = wire()
        if self.defect == 'sequence': self.value ^= 1 << 32
        if self.defect == 'total': self.value ^= 1
        if self.defect == 'error': self.value |= 1 << 54
        if self.defect == 'prefix': self.value = wire(1, 0, seq, total)
        return wire()

    def close(self):
        self.closed = True
        return 'unconfirmed' if self.defect == 'close' else 'closed'


def wait_for(predicate, seconds=2):
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError('bounded predicate timeout')
        time.sleep(0.002)


class ProtocolTests(unittest.TestCase):
    def test_attach_and_wrap_exact_prefix(self):
        d = Device(initial=wire(1, 3, 255, 0xfffffffe), limit=2)
        s = jtag.Session(d)
        self.assertEqual(s.send(b'\x11\x22\x33\x44'), 2)
        self.assertEqual((s.seq, s.total), (1, 0))
        self.assertEqual(s.send(b'\x33\x44'), 2)
        self.assertEqual(d.injected, b'\x11\x22\x33\x44')
        self.assertEqual(d.commands, [0xa304000044332211, 0xa302010000004433])

    def test_unknown_results_poison_without_replay(self):
        for defect in ('lost', 'reset', 'sequence', 'total', 'error', 'prefix'):
            with self.subTest(defect=defect):
                d = Device(defect=defect)
                s = jtag.Session(d)
                with self.assertRaises(jtag.TransferError): s.send(b'\x1c\xf0\x1c')
                with self.assertRaises(jtag.TransferError): s.send(b'\x1c\xf0\x1c')
                self.assertEqual(len(d.commands), 1)
                self.assertEqual(d.injected, b'\x1c\xf0\x1c')

    def test_discontinuity_before_send_never_submits(self):
        d = Device(initial=wire(1, 1, 9, 20))
        s = jtag.Session(d)
        d.value = wire()
        with self.assertRaises(jtag.TransferError): s.send(b'a')
        self.assertFalse(d.commands)

    def test_bad_attach_and_busy_bound(self):
        for value in (0, wire(3), wire() | 1 << 54, wire() | 1 << 40, wire(1), wire(0, total=1), wire(busy=True)):
            with self.subTest(value=value):
                with self.assertRaises(jtag.TransferError): jtag.Session(Device(initial=value), timeout=0.01)

    def test_settings_have_no_process_or_device_side_effect(self):
        with patch.object(subprocess, 'Popen', side_effect=AssertionError('hardware access')):
            self.assertEqual(jtag.settings({})['jtag_cable'], '')
            for data in ({'jtag_cable': 'only'}, {'jtag_tck_hz': '10000001'}, {'jtag_instance': '1'}):
                with self.assertRaises(ValueError): jtag.settings(data)
            with self.assertRaisesRegex(jtag.TransferError, 'unavailable'): jtag.open_transport({})

    def test_windows_quartus_uses_only_native_worker_from_wsl(self):
        with temporary_directory() as directory:
            binary = Path(directory)
            (binary/'quartus_stp.exe').touch()
            python = binary/'python.exe'
            python.touch()
            data = dict(jtag_cable='USB-Blaster [USB-0]', jtag_device='exact device',
                        quartus_bin=str(binary), windows_python=str(python))
            with patch.object(jtag, 'ProcessTransport') as process:
                jtag.open_transport(data)
            argv = process.call_args.args[0]
            self.assertEqual(argv[0], str(python))
            self.assertEqual(argv[1:3], ['-I', '-u'])
            self.assertEqual(argv[4], '--native-worker')
            self.assertTrue(process.call_args.kwargs['native_proxy'])
            self.assertEqual(process.call_args.kwargs['startup_timeout'], 12)


class TypingTests(unittest.TestCase):
    def test_translation_covers_modifiers_and_extended_keys(self):
        codes, unsupported = jtag.keys_to_scancodes(b'A\x1b[A\n\x01\xff')
        self.assertEqual(codes.hex(), '121cf01cf012' 'e075e0f075' '5af05a' '141cf01cf014')
        self.assertEqual(unsupported, b'\xff')
        self.assertEqual(jtag.split_trailing_escape(b'ab\x1b['), (b'ab', b'\x1b['))
        self.assertEqual(jtag.split_trailing_escape(b'\x1b'), (b'', b'\x1b'))

    def test_key_strokes_split_make_and_break_per_key(self):
        self.assertEqual(jtag.key_strokes(b'Aa\x1b[D\n'), [
            (bytes.fromhex('121c'), bytes.fromhex('f01cf012')),
            (bytes.fromhex('1c'), bytes.fromhex('f01c')),
            (bytes.fromhex('e06b'), bytes.fromhex('e0f06b')),
            (bytes.fromhex('5a'), bytes.fromhex('f05a'))])
        with self.assertRaisesRegex(ValueError, 'no scan code'):
            jtag.key_strokes(b'a\xff')

    def test_send_all_resubmits_only_the_refused_suffix(self):
        d = Device(limit=3)
        s = jtag.Session(d)
        self.assertEqual(jtag.send_all(s, b'abcdefg', deadline=time.monotonic() + 5, sleep=lambda _: None), 7)
        self.assertEqual(d.injected, b'abcdefg')
        self.assertEqual(len(d.commands), 3)
        d = Device(limit=0)
        with self.assertRaisesRegex(jtag.TransferError, 'no replay'):
            jtag.send_all(jtag.Session(d), b'z', deadline=time.monotonic() + 0.05, sleep=lambda _: None)
        self.assertEqual(d.injected, b'')

    def test_type_text_holds_each_key_between_make_and_break(self):
        d = Device()
        s = jtag.Session(d)
        pauses = []
        self.assertEqual(jtag.type_text(s, 'Hi', hold=0.07, sleep=pauses.append), 2)
        self.assertEqual(d.injected.hex(), '1233' 'f033f012' '43' 'f043')
        self.assertEqual(pauses, [0.07, 0.07])
        self.assertEqual([c >> 48 & 255 for c in d.commands], [2, 4, 1, 2])
        with self.assertRaises(ValueError):
            jtag.type_text(s, b'\xff')


@unittest.skipUnless(shutil.which('tclsh'), 'Linux tclsh required')
class TclProtocolTests(unittest.TestCase):
    def script(self, fault=''):
        temporary = temporary_directory()
        self.addCleanup(temporary.cleanup)
        script = Path(temporary.name) / 'peer.tcl'
        script.write_text('''package provide ::quartus::jtag 1.0
set quartus(args) [list {USB-Blaster [USB-0]} {exact device} 6000000 0 fixed-original-6mhz {}]
proc open_device {args} {}
proc device_lock {args} {}
proc device_unlock {} {}
proc close_device {} {}
rename exec real_exec
proc exec {args} {return 10M}
proc device_virtual_ir_shift {args} {global ir; set ir [lindex $args 3]}
set status 5a00000000000000
proc device_virtual_dr_shift {args} {
    global ir status
    if {$ir == 1} {set status 5a0b000000000003}
    return $status
}
''' + fault + '\nsource {' + str(jtag.Path(jtag.__file__).with_suffix('.tcl')) + '}\n')
        return script

    def test_real_tcl_stderr_protocol_persistent_process_and_close(self):
        t = jtag.ProcessTransport(['tclsh', str(self.script())])
        self.addCleanup(t.close)
        s = jtag.Session(t)
        pid = t.process.pid
        self.assertEqual(s.send(b'\x1c\xf0\x1c'), 3)
        self.assertEqual(t.process.pid, pid)
        self.assertEqual(t.close(), 'closed')
        self.assertFalse(t.reader.is_alive())

    def test_fixed_clock_never_queries_and_adjustable_verifies_exact_identity(self):
        t = jtag.ProcessTransport(['tclsh', str(self.script('proc exec {args} {error {fixed cable must not query}}'))])
        self.assertEqual(t.close(), 'closed')
        for defect in ('', 'index', 'clock', 'unsupported'):
            fault = """set quartus(args) [list {USB-BlasterII [USB-0]} {exact device} 6000000 0 adjustable 2]
proc exec {args} {
    if {[llength $args] == 1} {return {2) USB-BlasterII [USB-0]}}
    if {[lindex $args 1] eq "--getparam"} {return 6M}
    if {[lindex $args 2] ne "2" || [lindex $args 4] ne "6.0M"} {error {wrong setparam arguments}}
    return {}
}
"""
            if defect == 'index': fault = fault.replace('2) USB-', '1) USB-')
            if defect == 'clock': fault = fault.replace('return 6M', 'return 24M')
            if defect == 'unsupported': fault = fault.replace('return 6M', 'error {No parameter named JtagClock}')
            with self.subTest(defect=defect):
                if defect:
                    with self.assertRaises(jtag.TransferError):
                        jtag.ProcessTransport(['tclsh', str(self.script(fault))])
                else:
                    t = jtag.ProcessTransport(['tclsh', str(self.script(fault))])
                    self.assertEqual(t.close(), 'closed')

    def test_open_and_close_errors_and_eof_are_fail_closed(self):
        for fault in ('proc device_lock {args} {error refused}', 'proc open_device {args} {error unplugged}'):
            with self.subTest(fault=fault), self.assertRaises(jtag.TransferError):
                jtag.ProcessTransport(['tclsh', str(self.script(fault))], timeout=0.1)
        t = jtag.ProcessTransport(['tclsh', str(self.script('proc device_unlock {} {error failed}'))])
        self.assertNotEqual(t.close(), 'closed')
        t = jtag.ProcessTransport(['tclsh', str(self.script('proc device_virtual_dr_shift {args} {exit 1}'))])
        with self.assertRaises(jtag.TransferError): t.scan(2)
        self.assertNotEqual(t.close(), 'closed')

    def test_separate_cold_start_deadline_and_operation_timeout(self):
        t = jtag.ProcessTransport(['tclsh', str(self.script('after 120'))], timeout=0.02, startup_timeout=1)
        self.assertEqual(t.scan(2), wire())
        self.assertEqual(t.close(), 'closed')
        t = jtag.ProcessTransport(['tclsh', str(self.script('proc device_virtual_dr_shift {args} {after 1000; return 0}'))], timeout=0.02)
        started = time.monotonic()
        with self.assertRaises(jtag.TransferError): t.scan(1, jtag.packet(0, b'a'))
        self.assertLess(time.monotonic()-started, 1)
        self.assertNotEqual(t.close(), 'closed')


if __name__ == '__main__':
    unittest.main()
