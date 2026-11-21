"""No hardware: real Client bytes, bounded worker and Tk-independent events."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import queue
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[5]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from src.nand2doom.scripts.host import n2d_control as control  # noqa: E402
from src.nand2doom.scripts.host import n2d_control_terminal as terminal  # noqa: E402
from src.nand2doom.scripts.host import n2d_uart as uart  # noqa: E402
from src.nand2doom.scripts.host import n2d_uart_debug_host as wire  # noqa: E402
from src.common.scripts.jtag import n2a_jtag_keyboard as jtag  # noqa: E402
from src.common.scripts.jtag.tests.test_n2a_jtag_keyboard import Device  # noqa: E402


def reply(command, sequence, words=(), status=0):
    body = bytes((1, command | 0x80, sequence, status, len(words)))
    body += b"".join(w.to_bytes(4, "little") for w in words)
    return bytes((0x5A,)) + body + bytes((wire.crc8(body),))


class Transport:
    def __init__(self, *, identity=0x3144324E, halted=0):
        self.identity, self.halted = identity, halted
        self.packets, self.buffer = [], bytearray()
        self.close_count = 0
        self.fault = None

    def write(self, packet):
        assert not self.close_count and not self.buffer
        self.packets.append(packet)
        command, sequence = packet[2:4]
        words = (self.identity, self.halted) if command == wire.CMD_PING else ()
        data = reply(command, sequence, words, wire.ST_BUSY if self.fault == "status" else 0)
        if self.fault == "crc":
            data = data[:-1] + bytes((data[-1] ^ 1,))
        if self.fault == "header":
            data = b"\x00" + data[1:]
        if self.fault != "timeout":
            self.buffer.extend(data)
        return len(packet) - int(self.fault == "short")

    def read(self, count):
        data = bytes(self.buffer[:min(count, 2)])
        del self.buffer[:len(data)]
        return data

    def close(self):
        self.close_count += 1
        if self.fault == "close":
            raise OSError("close fixture")


class SessionTests(unittest.TestCase):
    def session(self, transport=None):
        fake = transport or Transport()
        opener = Mock(return_value=fake)
        session = control.Session("PORT", .01, opener=opener)
        session.connect()
        return session, fake, opener

    def test_exact_all_make_break_packets_real_client(self):
        session, fake, opener = self.session()
        expected = [0x275, 0x272, 0x26B, 0x274, 0x14, 0x29, 0x12, 0x11, 0x5A, 0x76,
                    0x35, 0x31, 0x0D, 0x16, 0x1E, 0x26, 0x25, 0x2E, 0x36, 0x3D]
        self.assertEqual(list(control.ACTIONS.values()), expected)
        seq = 1
        for action, make in zip(control.ACTIONS, expected):
            session.press("key", action)
            session.release("key")
            self.assertEqual(fake.packets[-2:], [wire.input_request(seq, make),
                                               wire.input_request(seq + 1, make | 0x100)])
            seq += 2
        session.close()
        self.assertEqual(fake.packets[0], wire.request(wire.CMD_PING, 0))
        opener.assert_called_once_with("PORT", .01, 1_000_000)
        self.assertEqual(fake.close_count, 1)

    def test_mixed_sources_repeats_and_combined_holds(self):
        session, fake, _ = self.session()
        session.press("w", "Forward")
        session.press("w", "Forward")
        session.press("up", "Forward")
        session.press("mouse", "Forward")
        session.press("ctrl", "Fire")
        session.release("w")
        session.release("mouse")
        self.assertEqual(len(fake.packets), 3)
        session.release("up")
        session.release("up")
        session.close()
        self.assertEqual(fake.packets[1:], [wire.input_request(1, 0x275), wire.input_request(2, 0x14),
                                          wire.input_request(3, 0x375), wire.input_request(4, 0x114)])

    def test_release_all_once_each_healthy_close_idempotent(self):
        session, fake, _ = self.session()
        for action in control.ACTIONS:
            session.press(action, action)
            session.press((action, "mouse"), action)
        session.release_all()
        session.release_all()
        session.close()
        session.close()
        self.assertEqual(len(fake.packets), 1 + 2 * len(control.ACTIONS))
        self.assertEqual(fake.close_count, 1)
        self.assertFalse(session.sources)

    def test_wrong_identity_halted_and_invalid_halt_enable_nothing(self):
        for fake in (Transport(identity=5), Transport(halted=1), Transport(halted=2)):
            with self.subTest(identity=fake.identity, halted=fake.halted):
                session = control.Session("PORT", .01, opener=lambda *args: fake)
                with self.assertRaises(control.ControlError):
                    session.connect()
                for action in (lambda: session.press("k", "Fire"), session.release_all, session.connect):
                    with self.assertRaises(control.ControlError):
                        action()
                session.close()
                self.assertEqual(len(fake.packets), 1)
                self.assertEqual(fake.close_count, 1)

    def test_all_uncertain_failures_close_without_cleanup_or_retry(self):
        for fault in ("status", "crc", "header", "timeout", "short"):
            with self.subTest(fault=fault):
                session, fake, _ = self.session()
                session.press("k", "Fire")
                fake.fault = fault
                with self.assertRaises(control.ControlError):
                    session.press("m", "Run")
                before = len(fake.packets)
                session.close()
                with self.assertRaises(control.ControlError):
                    session.release_all()
                self.assertEqual(before, len(fake.packets))
                self.assertEqual(fake.close_count, 1)
                self.assertTrue(session.failed)

    def test_poisoned_client_never_cleanup(self):
        session, fake, _ = self.session()
        session.press("k", "Forward")
        session.client.failed = True
        with self.assertRaises(control.ControlError):
            session.close()
        self.assertEqual(len(fake.packets), 2)
        self.assertEqual(fake.close_count, 1)

    def test_break_failure_stops_release_all(self):
        session, fake, _ = self.session()
        session.press("k", "Forward")
        session.press("f", "Fire")
        fake.fault = "status"
        with self.assertRaises(control.ControlError):
            session.close()
        self.assertEqual(len(fake.packets), 4)
        self.assertEqual(fake.close_count, 1)

    def test_close_error_once_and_opener_error(self):
        session, fake, _ = self.session()
        fake.fault = "close"
        with self.assertRaisesRegex(control.ControlError, "close fixture"):
            session.close()
        session.close()
        self.assertEqual(fake.close_count, 1)
        opener = Mock(side_effect=OSError("open fixture"))
        session = control.Session("PORT", .01, opener=opener)
        with self.assertRaisesRegex(control.ControlError, "open fixture"):
            session.connect()
        self.assertTrue(session.failed)
        opener.assert_called_once()

    def test_local_validation_before_open(self):
        for port, timeout in (("", 1), ("PORT", 0), ("PORT", float("nan")), ("PORT", 2)):
            with self.assertRaises(ValueError):
                control.Session(port, timeout, opener=Mock())


class Idle:
    def __init__(self):
        self.jobs, self.next = {}, 0

    def schedule(self, fn):
        self.next += 1
        self.jobs[self.next] = fn
        return self.next

    def cancel(self, job):
        del self.jobs[job]

    def drain(self):
        for job, fn in list(self.jobs.items()):
            del self.jobs[job]
            fn()


class EventTests(unittest.TestCase):
    def setUp(self):
        self.ops, self.idle = [], Idle()
        self.events = control.InputEvents(lambda *args: self.ops.append(args) or True,
                                          self.idle.schedule, self.idle.cancel)

    def test_repeat_and_shifted_release_physical_keycode(self):
        self.events.key_press(25, "w")
        self.events.key_press(25, "W")
        self.events.key_release(25)
        self.events.key_press(25, "W")
        self.idle.drain()
        self.assertEqual(self.ops, [("press", ("key", 25), "Forward")])
        self.events.key_release(25)
        self.idle.drain()
        self.assertEqual(self.ops[-1], ("release", ("key", 25)))

    def test_release_all_suppresses_held_auto_repeat(self):
        self.events.key_press(25, "w")
        self.events.release_all()
        self.events.key_press(25, "w")
        self.events.key_release(25)  # Why: X11 reports auto-repeat as a synthetic release/press pair.
        self.events.key_press(25, "w")
        self.idle.drain()
        self.assertEqual(len(self.ops), 2)
        self.events.key_release(25)
        self.idle.drain()
        self.events.key_press(25, "w")
        self.assertEqual(len(self.ops), 3)

    def test_mouse_release_outside_duplicate_and_focus_barrier(self):
        self.events.mouse_press("Fire")
        self.events.mouse_press("Fire")
        self.events.mouse_release()
        self.events.mouse_release()
        self.events.key_press(24, "q")
        self.events.key_release(24)
        self.events.release_all()
        self.idle.drain()
        self.assertEqual(self.ops, [("press", ("mouse", 1), "Fire"),
                                    ("release", ("mouse", 1)),
                                    ("press", ("key", 24), "Strafe"), ("release_all",)])

    def test_all_bindings_and_rejected_submission(self):
        for index, (symbol, action) in enumerate(control.BINDINGS.items()):
            self.events.key_press(index, symbol)
            self.assertEqual(self.ops[-1], ("press", ("key", index), action))
        events = control.InputEvents(lambda *args: False, self.idle.schedule, self.idle.cancel)
        events.key_press(1, "Up")
        events.mouse_press("Fire")
        self.assertFalse(events.keys)
        self.assertFalse(events.mouse)

    def test_app_focus_internal_and_external(self):
        app = object.__new__(control.App)
        app.root, app.release_all = Mock(), Mock()
        focused = Mock()
        focused.winfo_toplevel.return_value = app.root
        app.root.focus_displayof.return_value = focused
        app.focus_check()
        app.release_all.assert_not_called()
        app.root.focus_displayof.return_value = None
        app.focus_check()
        app.release_all.assert_called_once()

    def test_app_close_and_mouse_ungrab(self):
        app = object.__new__(control.App)
        app.events, app.worker, app.root, app.render = Mock(), Mock(), Mock(), Mock()
        app.status = Mock()
        button = app.grabbed = Mock()
        app.close()
        self.assertTrue(app.closing)
        app.worker.stop.assert_called_once()
        app.events.clear_local.assert_called_once()
        button.grab_release.assert_called_once()
        app.root.destroy.assert_not_called()

    def test_terminal_publication_between_empty_and_exit(self):
        for terminal in (("fault", "connect failed; recovery required"),
                         ("fault", "input failed; recovery required"),
                         ("fault", "close failed; recovery required"),
                         ("closed", "healthy stop"), None):
            for closing in (False, True):
                with self.subTest(terminal=terminal, closing=closing):
                    app = object.__new__(control.App)
                    worker = app.worker = Mock()
                    worker.terminal = None
                    def empty_then_exit():
                        worker.terminal = terminal
                        raise queue.Empty
                    worker.notices.get_nowait.side_effect = empty_then_exit
                    worker.thread.is_alive.return_value = False
                    app.root, app.status, app.events = Mock(), Mock(), Mock()
                    app.closing, app.faulted, app.smoke = closing, False, False
                    app.grabbed = None
                    app.entry, app.connect_button, app.disconnect_button = Mock(), Mock(), Mock()
                    app.release_button, app.buttons = Mock(), []
                    app.poll()
                    fault = terminal is None or terminal[0] == "fault"
                    self.assertEqual(app.faulted, fault)
                    self.assertIsNone(app.worker)
                    if closing:
                        app.root.destroy.assert_called_once()
                    else:
                        self.assertEqual(app.connect_button.configure.call_args.kwargs['state'],
                                         "disabled" if fault else "normal")
                    if fault:
                        app.events.clear_local.assert_called_once()
                        self.assertIn("recovery required", app.status.set.call_args.args[0])
                    else:
                        app.events.clear_local.assert_not_called()
                        self.assertEqual(app.status.set.call_args.args[0], "healthy stop")


class WorkerTests(unittest.TestCase):
    def test_stop_before_start_never_opens(self):
        factory = Mock(side_effect=AssertionError("no transport after cancellation"))
        worker = control.Worker("PORT", .01, factory=factory)
        worker.stop()
        worker.start()
        worker.thread.join(1)
        self.assertFalse(worker.thread.is_alive())
        factory.assert_not_called()
        self.assertEqual(worker.terminal, worker.notices.get(timeout=1))
        self.assertEqual(worker.terminal[0], "closed")

    def worker(self, session):
        worker = control.Worker("PORT", .01, factory=lambda *args: session)
        worker.start()
        self.assertEqual(worker.notices.get(timeout=1)[0], "connected")
        self.addCleanup(lambda: (worker.stop(), worker.thread.join(1)))
        return worker

    def test_healthy_close_drops_queued_presses_after_inflight(self):
        session, fake, _ = SessionTests().session()
        session.connect = Mock()  # Why: real Client setup is already checked above.
        entered, finish = threading.Event(), threading.Event()
        original = session.press
        def blocked(*args):
            entered.set()
            if not finish.wait(1):
                raise AssertionError("fixture unblock timed out")
            original(*args)
        session.press = blocked
        worker = self.worker(session)
        self.assertTrue(worker.post("press", "key", "Fire"))
        self.assertTrue(entered.wait(1))
        self.assertTrue(worker.post("press", "other", "Run"))
        worker.stop()
        self.assertFalse(worker.post("press", "late", "Use"))
        finish.set()
        worker.thread.join(1)
        self.assertFalse(worker.thread.is_alive())
        self.assertEqual([p[2] for p in fake.packets], [wire.CMD_PING, wire.CMD_INPUT, wire.CMD_INPUT])
        self.assertEqual(fake.packets[-1], wire.input_request(2, 0x114))
        self.assertEqual(fake.close_count, 1)
        self.assertEqual(worker.terminal, worker.notices.get(timeout=1))
        self.assertEqual(worker.terminal[0], "closed")

    def test_overflow_fails_closed_does_not_drop_break_silently(self):
        entered, finish = threading.Event(), threading.Event()
        session = Mock()
        session.abort.side_effect = lambda reason: reason
        session.press.side_effect = lambda *args: (entered.set(), finish.wait(1))
        worker = self.worker(session)
        worker.post("press", "key", "Fire")
        self.assertTrue(entered.wait(1))
        for index in range(32):
            self.assertTrue(worker.post("press", index, "Run"))
        self.assertFalse(worker.post("release", "key"))
        self.assertTrue(worker.aborting.is_set())
        finish.set()
        worker.thread.join(1)
        self.assertFalse(worker.thread.is_alive())
        session.press.assert_called_once()
        session.release.assert_not_called()
        session.close.assert_not_called()
        session.abort.assert_called_once()
        kind, text = worker.notices.get(timeout=1)
        self.assertEqual(kind, "fault")
        self.assertEqual(worker.terminal, (kind, text))
        self.assertIn("uncertain", text)

    def test_worker_identity_failure_no_ready_no_commands(self):
        fake = Transport(halted=1)
        worker = control.Worker("PORT", .01,
                                factory=lambda *args: control.Session(*args, opener=lambda *a: fake))
        worker.start()
        worker.thread.join(1)
        self.assertFalse(worker.ready.is_set())
        self.assertEqual(worker.terminal, worker.notices.get(timeout=1))
        self.assertEqual(worker.terminal[0], "fault")
        self.assertFalse(worker.post("press", 1, "Fire"))
        self.assertEqual(len(fake.packets), 1)
        self.assertEqual(fake.close_count, 1)


class CliTests(unittest.TestCase):
    def test_stdlib_help_plan_no_optional_imports(self):
        script = str(ROOT / "src/nand2doom/scripts/host/n2d_control.py")
        for arg in ("--help", "--plan", "--dry-run"):
            result = subprocess.run([sys.executable, "-B", "-S", script, arg],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
        code = ("import sys; sys.path.insert(0," + repr(str(Path(script).parent)) + "); "
                "import n2d_control as c; c.main(['--plan']); "
                "assert 'tkinter' not in sys.modules and 'serial' not in sys.modules")
        result = subprocess.run([sys.executable, "-B", "-S", "-c", code], capture_output=True,
                                text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_plan_never_creates_worker(self):
        with patch.object(control, "Worker", side_effect=AssertionError("worker forbidden")):
            with redirect_stdout(io.StringIO()) as stream:
                self.assertEqual(control.main(["--plan", "--port", "PORT"]), 0)
            self.assertFalse(json.loads(stream.getvalue())["auto_connect"])

    def test_smoke_connect_gated(self):
        app = object.__new__(control.App)
        app.smoke = True
        app.worker_factory = Mock(side_effect=AssertionError("no smoke serial"))
        app.connect()
        app.worker_factory.assert_not_called()


def decode_set2(stream):
    """Independent Set-2 fold: E0/F0 qualify the next byte."""
    events, extended, release = [], 0, 0
    for byte in stream:
        if byte == 0xE0:
            extended = 1
        elif byte == 0xF0:
            release = 1
        else:
            events.append(extended << 9 | release << 8 | byte)
            extended = release = 0
    return events


class FakeJtag:
    """jtag module stand-in: real Session/send_all against the common wire peer."""

    def __init__(self, peer=None):
        self.peer, self.opened = peer or Device(), []
        self.Session, self.send_all, self.TransferError = jtag.Session, jtag.send_all, jtag.TransferError

    def open_transport(self, data):
        self.opened.append(data)
        return self.peer


SETTINGS = {"jtag_cable": "USB-Blaster [1-1]", "jtag_device": "@1: EP4CE115 (0x020F70DD)"}


class JtagSessionTests(unittest.TestCase):
    def session(self, peer=None):
        fake = FakeJtag(peer)
        session = control.Session("USB-Blaster [1-1]", .01, jtag=SETTINGS, jtag_module=fake)
        session.connect()
        return session, fake

    def test_every_action_make_and_break_reach_the_endpoint(self):
        session, fake = self.session()
        for action in control.ACTIONS:
            session.press("key", action)
            session.release("key")
        session.close()
        expected = [e for event in control.ACTIONS.values() for e in (event, event | 0x100)]
        self.assertEqual(decode_set2(fake.peer.injected), expected)
        self.assertEqual(fake.opened, [SETTINGS])
        self.assertTrue(fake.peer.closed)

    def test_close_releases_held_keys_once(self):
        session, fake = self.session()
        session.press("up", "Forward")
        session.press("ctrl", "Fire")
        session.press("w", "Forward")
        session.close()
        session.close()
        self.assertEqual(decode_set2(fake.peer.injected), [0x275, 0x014, 0x375, 0x114])

    def test_uncertain_delivery_latches_without_release(self):
        session, fake = self.session(Device(defect="lost"))
        with self.assertRaises(control.ControlError):
            session.press("k", "Fire")
        self.assertTrue(session.failed)
        before = len(fake.peer.commands)
        session.close()
        self.assertEqual(len(fake.peer.commands), before)
        with self.assertRaises(control.ControlError):
            session.press("k", "Use")

    def test_unconfirmed_jtag_close_is_a_fault(self):
        session, _ = self.session(Device(defect="close"))
        with self.assertRaisesRegex(control.ControlError, "JTAG release"):
            session.close()

    def test_open_failure_is_a_fault(self):
        fake = FakeJtag()
        fake.open_transport = Mock(side_effect=jtag.TransferError("no cable"))
        session = control.Session("USB-Blaster [1-1]", .01, jtag=SETTINGS, jtag_module=fake)
        with self.assertRaisesRegex(control.ControlError, "no cable"):
            session.connect()
        self.assertTrue(session.failed)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class TerminalParserTests(unittest.TestCase):
    def test_kitty_press_repeat_release_modifiers_arrows_focus_and_quit(self):
        parser = terminal.KittyParser()
        stream = (b"\x1b[119u" b"\x1b[119;1:2u" b"\x1b[119;1:3u" b"\x1b[1;1:1A" b"\x1b[1;1:3A"
                  b"\x1b[57442;5u" b"\x1b[57441;2:3u" b"\x1b[9u" b"\x1b[13u" b"\x1b[27u" b"\x1b[32;1:3u"
                  b"\x1b[O" b"\x1b[I" b"\x1b[?11u" b"\x1b[99;5u" b"\x1b[21~")
        events = []
        for index in range(0, len(stream), 5):  # Why: escape sequences may split across reads
            events += parser.feed(stream[index:index + 5])
        self.assertEqual(events, [
            ("press", "w"), ("repeat", "w"), ("release", "w"), ("press", "Up"), ("release", "Up"),
            ("press", "Control_L"), ("release", "Shift_L"), ("press", "Tab"), ("press", "Return"), ("press", "Escape"),
            ("release", "space"), ("focus-out", None), ("focus-in", None), ("quit", None), ("quit", None)])

    def test_tap_parser_legacy_bytes_and_lone_escape(self):
        parser = terminal.TapParser()
        self.assertEqual(parser.feed(b"w\x1b[Ay 1\r\t"),
                         [("tap", "w"), ("tap", "Up"), ("tap", "y"), ("tap", "space"),
                          ("tap", "1"), ("tap", "Return"), ("tap", "Tab")])
        self.assertEqual(parser.feed(b"\x1b"), [])
        self.assertEqual(parser.feed(b"", flush=True), [("tap", "Escape")])
        self.assertEqual(parser.feed(b"\x1b[O\x03"), [("focus-out", None), ("quit", None)])

    def test_kitty_detection(self):
        replies = [b"\x1b[?0u\x1b[?62c"]
        self.assertTrue(terminal.detect_kitty(lambda t: replies.pop(0) if replies else b"", lambda d: None))
        replies = [b"\x1b[?62;22c"]
        self.assertFalse(terminal.detect_kitty(lambda t: replies.pop(0) if replies else b"", lambda d: None))
        clock = Clock()

        def silent(timeout):
            clock.now += timeout
            return b""
        self.assertFalse(terminal.detect_kitty(silent, lambda d: None, clock=clock))


class TerminalLoopTests(unittest.TestCase):
    def setUp(self):
        self.fake = Transport()
        self.session = control.Session("PORT", .01, opener=lambda *a: self.fake)
        self.session.connect()
        self.clock = Clock()

    def events(self):
        decoded = []
        for packet in self.fake.packets[1:]:
            match = [e for e in range(1024) if wire.input_request(packet[3], e) == packet]
            self.assertEqual(len(match), 1)
            decoded += match
        return decoded

    def run_loop(self, chunks, *, kitty):
        def read(timeout):
            self.clock.now += timeout
            return chunks.pop(0) if chunks else b"\x03" if not kitty else b"\x1b[21~"
        written = []
        code = terminal.run(self.session, read, written.append, kitty=kitty, clock=self.clock)
        return code, b"".join(written)

    def test_kitty_holds_are_make_until_release_and_quit_releases_the_rest(self):
        chunks = [b"\x1b[1;1:1A", b"\x1b[57442u", b"\x1b[1;1:2A", b"\x1b[1;1:3A", b"\x1b[100u", b"\x1b[21~"]
        code, output = self.run_loop(chunks, kitty=True)
        self.assertEqual(code, 0)
        self.assertEqual(self.events(), [0x275, 0x014, 0x375, 0x274, 0x114, 0x374])
        self.assertEqual(self.fake.close_count, 1)
        self.assertIn(b"held: Fire, Forward", output)

    def test_focus_loss_releases_everything(self):
        chunks = [b"\x1b[1;1:1C", b"\x1b[57441u", b"\x1b[O", b"\x1b[21~"]
        self.run_loop(chunks, kitty=True)
        self.assertEqual(self.events(), [0x274, 0x012, 0x374, 0x112])

    def test_tap_mode_auto_repeat_extends_then_releases(self):
        chunks = [b"\x1b[A"] + [b""] * 10 + [b"\x1b[A"] * 5 + [b""] * 30 + [b"\x03"]
        self.run_loop(chunks, kitty=False)
        self.assertEqual(self.events(), [0x275, 0x375])

    def test_device_failure_reports_fault(self):
        self.fake.fault = "status"
        code, output = self.run_loop([b"\x1b[1;1:1A"], kitty=True)
        self.assertEqual(code, 1)
        self.assertIn(b"FAULT", output)

    def test_interrupt_still_releases(self):
        def read(timeout):
            if not self.session.sources:
                return b"\x1b[1;1:1B"
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            terminal.run(self.session, read, lambda d: None, kitty=True, clock=self.clock)
        self.assertEqual(self.events(), [0x272, 0x372])
        self.assertEqual(self.fake.close_count, 1)


class TransportCliTests(unittest.TestCase):
    def test_plan_reports_transport(self):
        with redirect_stdout(io.StringIO()) as stream:
            self.assertEqual(control.main(["--plan", "--transport", "jtag", "--cable", "USB-Blaster [1-1]",
                                           "--device", "@1: EP4CE115 (0x020F70DD)", "--connect"]), 0)
        plan = json.loads(stream.getvalue())
        self.assertEqual((plan["transport"], plan["auto_connect"]), ("jtag", True))
        self.assertEqual(plan["make"]["Map"], 0x00D)
        self.assertNotIn("firmware_ignored", plan)

    def test_terminal_requires_a_link(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exit_:
            control.main(["--terminal"])
        self.assertEqual(exit_.exception.code, 2)

    def test_terminal_mode_dispatches_a_jtag_session(self):
        captured = []
        with patch.object(terminal, "main_loop", side_effect=lambda s: captured.append(s) or 0):
            self.assertEqual(control.main(["--terminal", "--transport", "jtag", "--cable", "USB-Blaster [1-1]",
                                           "--device", "@1: EP4CE115 (0x020F70DD)"]), 0)
        self.assertEqual(captured[0].port, "USB-Blaster [1-1]")
        self.assertEqual(captured[0].jtag["jtag_device"], "@1: EP4CE115 (0x020F70DD)")


if __name__ == "__main__":
    unittest.main()
