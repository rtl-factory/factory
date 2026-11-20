"""Host side of the common Virtual JTAG keyboard endpoint (n2a_jtag_keyboard).

Persistent quartus_stp transport with explicit configuration and no discovery,
the IR1/IR2 command/status protocol (wiki/src/common/tools/jtag/spec.md), and the
Set-2 scan-code translation shared by every consumer (nand2linux console and
headless `type`, the nand2tetris console). This file is self-contained: it also
runs under native Windows Python as the bounded Quartus owner, so it imports
nothing from the repository. Ported from nand2anything tools/jtag.
"""
from __future__ import annotations

from collections import deque
import math
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import sys
import threading
import time

MASK32 = 0xffffffff
OP_SECONDS = 2.0
BATCH_SECONDS = 3.0
JTAG_KEYS = ('jtag_cable', 'jtag_device', 'jtag_tck_hz', 'jtag_instance', 'jtag_clock_mode', 'jtag_cable_index')


class TransferError(RuntimeError):
    pass


def settings(data):
    """Validate declarations only. Empty cable/device means unavailable."""
    cable, device = (str(data.get(k, '')).strip() for k in JTAG_KEYS[:2])
    if bool(cable) != bool(device):
        raise ValueError('JTAG requires both exact cable and device names')
    if any(not x.isprintable() or len(x) > 256 for x in (cable, device) if x):
        raise ValueError('JTAG cable/device must be printable names up to256 characters')
    try:
        tck, instance = int(data.get('jtag_tck_hz', '6000000')), int(data.get('jtag_instance', '0'))
    except (TypeError, ValueError) as exc:
        raise ValueError('JTAG TCK and instance must be integers') from exc
    if not 1 <= tck <= 10000000 or instance != 0:
        raise ValueError('JTAG requires TCK1..10000000 Hz and instance0')
    mode = data.get('jtag_clock_mode', 'fixed-original-6mhz')
    index = str(data.get('jtag_cable_index', '')).strip()
    if mode not in ('fixed-original-6mhz', 'adjustable'):
        raise ValueError('JTAG clock mode: fixed-original-6mhz or adjustable')
    if mode == 'fixed-original-6mhz':
        if tck != 6000000 or (cable and not re.fullmatch(r'USB-Blaster \[[^\]\r\n]+\]', cable)):
            raise ValueError('fixed-original-6mhz requires exact original USB-Blaster name and6000000 Hz')
    elif not re.fullmatch(r'[1-9][0-9]*', index):
        raise ValueError('adjustable JTAG clock requires explicit cable index')
    return dict(data, jtag_clock_mode=mode, jtag_cable_index=index, jtag_cable=cable, jtag_device=device, jtag_tck_hz=str(tck), jtag_instance='0')


def packet(seq, data):
    if not 0 <= seq <= 255 or not 1 <= len(data) <= 4:
        raise ValueError('sequence8 and1..4 scan bytes required')
    return (0xa3 << 56) | (len(data) << 48) | (seq << 40) | int.from_bytes(data, 'little')


def status(word):
    if not 0 <= word < 1 << 64 or word >> 56 != 0x5a or (word >> 40) & 255:
        raise TransferError('invalid JTAG status identity/reserved bits')
    value = dict(busy=bool(word & (1 << 55)), error=bool(word & (1 << 54)),
                 code=(word >> 51) & 7, accepted=(word >> 48) & 7,
                 seq=(word >> 32) & 255, total=word & MASK32)
    if value['error'] or value['code'] > 2 or value['accepted'] > 4:
        raise TransferError('JTAG scan error or unknown result; no replay')
    if value['code'] == 0 and (value['accepted'] or value['seq'] or value['total']):
        raise TransferError('inconsistent JTAG reset status')
    if (value['code'] == 1 and not value['accepted']) or (value['code'] == 2 and value['accepted'] == 4):
        raise TransferError('inconsistent JTAG accepted status')
    return value


class Session:
    def __init__(self, transport, *, timeout=BATCH_SECONDS):
        self.transport, self.timeout = transport, timeout
        self.poisoned = False
        self.last = self._ready(time.monotonic() + timeout)
        self.seq = 0 if self.last['code'] == 0 else (self.last['seq'] + 1) & 255
        self.total = self.last['total']

    def _ready(self, deadline):
        for _ in range(1024):
            if time.monotonic() >= deadline:
                break
            value = status(self.transport.scan(2, deadline=deadline))
            if time.monotonic() >= deadline:
                break
            if not value['busy']:
                return value
            time.sleep(0.001)
        raise TransferError('JTAG completion deadline; no replay')

    def send(self, data, *, deadline=None):
        """Exactly one submit; caller may resubmit only its proven refused suffix."""
        if self.poisoned:
            raise TransferError('JTAG session uncertain; explicit recovery required')
        deadline = deadline or time.monotonic() + self.timeout
        try:
            # A reset or another owner between batches must not silently establish a new baseline.
            if self._ready(deadline) != self.last:
                raise TransferError('JTAG status discontinuity before submit')
            self.transport.scan(1, packet(self.seq, data), deadline=deadline)
            value = self._ready(deadline)
            count = value['accepted']
            if (value['code'] not in (1, 2) or value['seq'] != self.seq or count > len(data)
                    or (value['code'] == 1) != (count == len(data))
                    or value['total'] != (self.total + count) & MASK32):
                raise TransferError('JTAG acceptance/reset/sequence mismatch')
            self.seq, self.total, self.last = (self.seq + 1) & 255, value['total'], value
            return count
        except Exception as exc:
            self.poisoned = True
            raise TransferError(f'JTAG delivery uncertain; no replay: {exc}') from exc


def send_all(session, codes, *, deadline, sleep=time.sleep):
    """Deliver every scan code through `session` in batches of at most four,
    resubmitting only a proven refused suffix until `deadline`. Returns the
    number of codes accepted; raises TransferError on uncertainty (which
    poisons the session) or when the bound expires with codes still unsent
    (which does not: the refused codes are known)."""
    codes, sent = bytes(codes), 0
    while codes:
        if time.monotonic() >= deadline:
            raise TransferError(f'{len(codes)} scan codes unsent at the delivery bound; no replay')
        accepted = session.send(codes[:4], deadline=min(deadline, time.monotonic() + BATCH_SECONDS))
        sent += accepted
        codes = codes[accepted:]
        if not accepted:
            sleep(0.01)
    return sent


# ----------------------------------------------------------------------------------------------
# Keyboard: host bytes -> Set-2 scancode sequences a PS/2 set-2 decoder understands
# ----------------------------------------------------------------------------------------------
SET2 = {'a': 0x1C, 'b': 0x32, 'c': 0x21, 'd': 0x23, 'e': 0x24, 'f': 0x2B, 'g': 0x34, 'h': 0x33, 'i': 0x43, 'j': 0x3B,
        'k': 0x42, 'l': 0x4B, 'm': 0x3A, 'n': 0x31, 'o': 0x44, 'p': 0x4D, 'q': 0x15, 'r': 0x2D, 's': 0x1B, 't': 0x2C,
        'u': 0x3C, 'v': 0x2A, 'w': 0x1D, 'x': 0x22, 'y': 0x35, 'z': 0x1A,
        '1': 0x16, '2': 0x1E, '3': 0x26, '4': 0x25, '5': 0x2E, '6': 0x36, '7': 0x3D, '8': 0x3E, '9': 0x46, '0': 0x45,
        ' ': 0x29, '\r': 0x5A, '\n': 0x5A, '\t': 0x0D, '\x1b': 0x76, '\x7f': 0x66, '\x08': 0x66,
        '-': 0x4E, '=': 0x55, '[': 0x54, ']': 0x5B, '\\': 0x5D, ';': 0x4C, "'": 0x52, '`': 0x0E, ',': 0x41, '.': 0x49, '/': 0x4A}
SHIFTED = {'!': '1', '@': '2', '#': '3', '$': '4', '%': '5', '^': '6', '&': '7', '*': '8', '(': '9', ')': '0',
           '_': '-', '+': '=', '{': '[', '}': ']', '|': '\\', ':': ';', '"': "'", '~': '`', '<': ',', '>': '.', '?': '/'}
SHIFT, CTRL, RELEASE, EXTENDED = 0x12, 0x14, 0xF0, 0xE0
EXTENDED_KEYS = {b'\x1b[A': 0x75, b'\x1b[B': 0x72, b'\x1b[C': 0x74, b'\x1b[D': 0x6B,   # arrows
             b'\x1b[H': 0x6C, b'\x1b[F': 0x69, b'\x1b[5~': 0x7D, b'\x1b[6~': 0x7A,  # home end pgup pgdn
             b'\x1b[2~': 0x70, b'\x1b[3~': 0x71}                                    # insert delete


def _press(code, extended=False):
    make = ([EXTENDED] if extended else []) + [code]
    brk = ([EXTENDED] if extended else []) + [RELEASE, code]
    return make + brk


def keys_to_scancodes(data: bytes) -> tuple[bytes, bytes]:
    """Translate host key bytes (a TERM=linux stream) into Set-2 make/break sequences.
    Returns (scancodes, unsupported) where `unsupported` holds the bytes that have no key."""
    out, rest, i = [], bytearray(), 0
    while i < len(data):
        for seq, code in EXTENDED_KEYS.items():
            if data.startswith(seq, i):
                out += _press(code, extended=True); i += len(seq); break
        else:
            ch = chr(data[i]); i += 1
            if ch in SET2:
                out += _press(SET2[ch])
            elif ch.isupper() and ch.lower() in SET2:
                out += [SHIFT] + _press(SET2[ch.lower()]) + [RELEASE, SHIFT]
            elif ch in SHIFTED:
                out += [SHIFT] + _press(SET2[SHIFTED[ch]]) + [RELEASE, SHIFT]
            elif 1 <= ord(ch) <= 26 and chr(ord(ch) + 96) in SET2:              # Ctrl-A .. Ctrl-Z
                out += [CTRL] + _press(SET2[chr(ord(ch) + 96)]) + [RELEASE, CTRL]
            else:
                rest.append(ord(ch))
    return bytes(out), bytes(rest)


def split_trailing_escape(data: bytes) -> tuple[bytes, bytes]:
    """(data, held): a trailing prefix of an extended-key escape (ESC, ESC [, ESC [ 5) is held back so a
    sequence split over two reads is translated whole; a lone ESC is a key only when nothing follows."""
    for n in (3, 2, 1):
        tail = data[-n:]
        if len(tail) == n and any(seq.startswith(tail) and len(seq) > n for seq in EXTENDED_KEYS):
            return data[:-n], tail
    return data, b''


def key_strokes(data: bytes) -> list[tuple[bytes, bytes]]:
    """One (make, break) pair per key in `data`, in order: the codes that press
    the key (with its shift/ctrl modifier made first) and the codes that release
    it. Raises ValueError for a byte without a scan code."""
    strokes, i = [], 0
    while i < len(data):
        step = next((len(seq) for seq in EXTENDED_KEYS if data.startswith(seq, i)), 1)
        codes, unsupported = keys_to_scancodes(data[i:i + step])
        if unsupported:
            raise ValueError(f'no scan code for {unsupported!r}')
        first_break = codes.index(RELEASE)
        split = first_break - 1 if first_break and codes[first_break - 1] == EXTENDED else first_break
        strokes.append((codes[:split], codes[split:]))
        i += step
    return strokes


def type_text(session, text, *, hold=0.05, seconds=20.0, sleep=time.sleep):
    """Type `text` (str or key bytes) as held keys: each key's make codes, a `hold`
    pause so a polling program (the Hack KBD register) observes the key, then its
    break codes. Returns the number of keys typed; raises TransferError on
    uncertainty, leaving the session poisoned (keys may remain pressed)."""
    strokes = key_strokes(text.encode() if isinstance(text, str) else bytes(text))
    deadline = time.monotonic() + seconds
    for make, brk in strokes:
        send_all(session, make, deadline=deadline, sleep=sleep)
        sleep(hold)
        send_all(session, brk, deadline=deadline, sleep=sleep)
    return len(strokes)


class WindowsJob:
    """Native kernel job: descendants die even if the Quartus leader exits first."""
    def __init__(self, process):
        import ctypes
        from ctypes import wintypes as w
        class Basic(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                        ('flags', w.DWORD), ('min_ws', ctypes.c_size_t), ('max_ws', ctypes.c_size_t),
                        ('active_limit', w.DWORD), ('affinity', ctypes.c_size_t),
                        ('priority', w.DWORD), ('scheduling', w.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]
        class Extended(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', IO), ('process_memory', ctypes.c_size_t),
                        ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t),
                        ('peak_job', ctypes.c_size_t)]
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        self.api.CreateJobObjectW.restype = w.HANDLE
        self.api.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        self.api.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.api.CloseHandle.argtypes = [w.HANDLE]
        self.handle = self.api.CreateJobObjectW(None, None)
        info = Extended()
        info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.handle:
            raise OSError(ctypes.get_last_error(), 'CreateJobObjectW failed')
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            self.close()
            raise OSError(ctypes.get_last_error(), 'SetInformationJobObject failed')
        if not self.api.AssignProcessToJobObject(self.handle, w.HANDLE(int(process._handle))):
            self.close()
            raise OSError(ctypes.get_last_error(), 'AssignProcessToJobObject failed')

    def resume(self, process):
        """Resume only after assignment. Popen closes CreateProcess's thread handle.

        Suspended-create/assign/resume follows tools/dashboard/hw_native.py.
        Reopen the sole primary thread of our still-suspended child; no child
        user code or descendants can run before it belongs to this job.
        """
        import ctypes
        from ctypes import wintypes as w
        class ThreadEntry(ctypes.Structure):
            _fields_ = [('size', w.DWORD), ('usage', w.DWORD), ('tid', w.DWORD),
                        ('pid', w.DWORD), ('base_priority', w.LONG),
                        ('delta_priority', w.LONG), ('flags', w.DWORD)]
        api = self.api
        api.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
        api.CreateToolhelp32Snapshot.restype = w.HANDLE
        api.Thread32First.argtypes = [w.HANDLE, ctypes.POINTER(ThreadEntry)]
        api.Thread32Next.argtypes = [w.HANDLE, ctypes.POINTER(ThreadEntry)]
        api.OpenThread.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        api.OpenThread.restype = w.HANDLE
        api.ResumeThread.argtypes = [w.HANDLE]
        api.ResumeThread.restype = w.DWORD
        snapshot = api.CreateToolhelp32Snapshot(4, 0)  # TH32CS_SNAPTHREAD
        if snapshot == ctypes.c_void_p(-1).value:
            raise OSError(ctypes.get_last_error(), 'thread snapshot failed')
        threads = []
        try:
            entry = ThreadEntry()
            entry.size = ctypes.sizeof(entry)
            found = api.Thread32First(snapshot, ctypes.byref(entry))
            deadline = time.monotonic() + OP_SECONDS
            while found:
                if time.monotonic() >= deadline:
                    raise TransferError('native primary-thread lookup deadline')
                if entry.pid == process.pid:
                    threads.append(entry.tid)
                found = api.Thread32Next(snapshot, ctypes.byref(entry))
        finally:
            api.CloseHandle(snapshot)
        if len(threads) != 1:
            raise TransferError('suspended child does not have exactly one primary thread')
        thread = api.OpenThread(2, False, threads[0])  # THREAD_SUSPEND_RESUME
        if not thread:
            raise OSError(ctypes.get_last_error(), 'OpenThread failed')
        try:
            if api.ResumeThread(thread) != 1:
                raise TransferError('primary thread suspension count mismatch')
        finally:
            api.CloseHandle(thread)

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


class ProcessTransport:
    """One persistent child; stderr protocol, bounded reads and native tree cleanup."""
    def __init__(self, argv, *, timeout=OP_SECONDS, startup_timeout=10.0, native_proxy=False):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('positive finite timeout required')
        if not math.isfinite(startup_timeout) or startup_timeout <= 0:
            raise ValueError('positive finite startup timeout required')
        self.timeout, self.native_proxy = timeout, native_proxy
        self.events, self.diagnostics = queue.Queue(maxsize=32), deque(maxlen=20)
        self.closed, self.counter = False, 0
        self.cleanup = 'not closed'
        kwargs = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP | 0x4} if os.name == 'nt' else {'start_new_session': True}
        self.process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, bufsize=1, **kwargs)
        self.job = None
        try:
            if os.name == 'nt':
                self.job = WindowsJob(self.process)
                self.job.resume(self.process)
        except BaseException:
            if self.job is not None:
                self.job.close()
            subprocess.run(['taskkill', '/PID', str(self.process.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False)
            self.process.wait(timeout=1)
            self.process.stdin.close()
            self.process.stdout.close()
            raise
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        try:
            opened = self._event(time.monotonic() + startup_timeout, limit=startup_timeout)
            if opened != 'N2A READY':
                raise TransferError('JTAG open/lock/TCK failed: ' + opened)
        except BaseException:
            self.abort()
            raise

    def _read(self):
        try:
            for line in iter(lambda: self.process.stdout.readline(2048), ''):
                if not line.endswith('\n'):
                    break
                if line.startswith('N2A '):
                    try:
                        self.events.put_nowait(line.strip())
                    except queue.Full:
                        break
                else:
                    self.diagnostics.append(line[:512])
        finally:
            self.process.stdout.close()
            try:
                self.events.put_nowait(None)
            except queue.Full:
                pass

    def _event(self, deadline, *, limit=None):
        remaining = min(self.timeout if limit is None else limit, deadline - time.monotonic())
        try:
            if remaining <= 0:
                raise queue.Empty
            value = self.events.get(timeout=remaining)
        except queue.Empty as exc:
            raise TransferError('JTAG process deadline expired') from exc
        if time.monotonic() >= deadline:
            raise TransferError('JTAG reply after deadline')
        if value is None:
            raise TransferError('JTAG process disconnected')
        return value

    def scan(self, ir, value=0, *, deadline=None):
        if ir not in (1, 2) or not 0 <= value < 1 << 64 or self.closed:
            raise TransferError('invalid scan or closed transport')
        self.counter += 1
        try:
            self.process.stdin.write(f'SCAN {self.counter} {ir} {value:016x}\n')
            self.process.stdin.flush()
            reply = self._event(deadline or time.monotonic() + self.timeout)
            match = re.fullmatch(r'N2A OK ([0-9]+) ([0-9a-fA-F]{1,16})', reply)
            if not match or int(match[1]) != self.counter:
                raise TransferError('JTAG response mismatch: ' + reply)
            return int(match[2], 16)
        except (OSError, TransferError) as exc:
            self.abort()
            raise TransferError(f'JTAG scan outcome unknown; no replay: {exc}') from exc

    def abort(self):
        if self.closed:
            return self.cleanup
        self.closed = True
        self.cleanup = 'device release unconfirmed'
        # EOF lets the native owner finish its bounded operation and kill its own
        # Windows tree. A WSL PID is never mistaken for a Windows Quartus PID.
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            if self.native_proxy:
                self.process.wait(timeout=10)
            elif os.name == 'nt':
                if self.job is not None:
                    self.job.close()
                subprocess.run(['taskkill', '/PID', str(self.process.pid), '/T', '/F'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False)
                self.process.wait(timeout=1)
            else:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self.process.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            self.cleanup = 'native/process cleanup unconfirmed; explicit recovery required'
        self.reader.join(timeout=1)
        return self.cleanup

    def close(self):
        if self.closed:
            return self.cleanup
        try:
            self.process.stdin.write('QUIT\n')
            self.process.stdin.flush()
            if self._event(time.monotonic() + self.timeout) != 'N2A CLOSED':
                raise TransferError('JTAG unlock/close unconfirmed')
            self.process.wait(timeout=self.timeout)
            if self.process.returncode:
                raise TransferError('JTAG close failed')
            self.process.stdin.close()
            if self.job is not None:
                self.job.close()
            self.reader.join(timeout=1)
            if self.reader.is_alive():
                raise TransferError('JTAG descendants remain active')
            self.closed, self.cleanup = True, 'closed'
        except (OSError, TransferError, subprocess.TimeoutExpired):
            self.abort()
        return self.cleanup


def windows_path(path, distro):
    value = str(Path(path).absolute())
    match = re.match(r'^/mnt/([a-z])/(.*)', value)
    if match:
        return match[1].upper() + ':\\' + match[2].replace('/', '\\')
    return '\\\\wsl.localhost\\' + distro + value.replace('/', '\\')


def open_transport(data):
    data = settings(data)
    if not data['jtag_cable']:
        raise TransferError('JTAG input unavailable: configure exact cable and device; no UART fallback')
    binary = Path(data.get('quartus_bin', ''))
    native = os.name == 'nt' or (binary / 'quartus_stp.exe').is_file()
    executable = binary / ('quartus_stp.exe' if native else 'quartus_stp')
    tcl = Path(__file__).with_suffix('.tcl')
    args = [data[k] for k in JTAG_KEYS]
    if native and os.name != 'nt':
        python = data.get('windows_python', '')
        if not python or not Path(python).is_file():
            raise TransferError('native Windows Python required for Windows Quartus')
        distro = data.get('wsl_distro', 'Ubuntu')
        argv = [python, '-I', '-u', windows_path(__file__, distro), '--native-worker',
                windows_path(executable, distro), windows_path(tcl, distro), *args]
        return ProcessTransport(argv, timeout=5, startup_timeout=12, native_proxy=True)
    return ProcessTransport([str(executable), '-t', str(tcl), *args])


def native_worker(argv):
    if os.name != 'nt':
        raise TransferError('native worker requires Windows Python')
    transport = None
    try:
        transport = ProcessTransport([argv[0], '-t', *argv[1:]])
        print('N2A READY', flush=True)
        for line in sys.stdin:
            if line.strip() == 'QUIT':
                break
            match = re.fullmatch(r'SCAN ([0-9]+) ([12]) ([0-9a-f]{16})\n', line)
            if not match:
                raise TransferError('invalid native worker request')
            result = transport.scan(int(match[2]), int(match[3], 16))
            print(f'N2A OK {match[1]} {result:016x}', flush=True)
        if transport.close() != 'closed':
            raise TransferError('native Quartus cleanup unconfirmed')
        print('N2A CLOSED', flush=True)
        return 0
    except Exception as exc:
        print('N2A ERROR ' + str(exc), flush=True)
        return 1
    finally:
        if transport is not None:
            transport.close()


if __name__ == '__main__':
    if len(sys.argv) != 10 or sys.argv[1] != '--native-worker':
        raise SystemExit('private native JTAG worker; use console settings')
    raise SystemExit(native_worker(sys.argv[2:]))
