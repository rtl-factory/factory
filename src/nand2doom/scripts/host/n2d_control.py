#!/usr/bin/env python3
"""Local controls for running Doom over UART or JTAG; never open a link without Connect.

Without --connect the window starts disconnected. --terminal (or a Python without
Tk) uses the terminal controls instead. Spec: wiki/src/nand2doom/scripts/host/spec.md#local-controls.
"""
from __future__ import annotations

import argparse
import functools
import json
import math
from pathlib import Path
import queue
import sys
import threading

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Ref: sw/doom/keymap.h maps every one of these make codes.
ACTIONS = {
    "Forward": 0x275, "Backward": 0x272, "Turn left": 0x26B,
    "Turn right": 0x274, "Fire": 0x014, "Use": 0x029,
    "Run": 0x012, "Strafe": 0x011, "Enter": 0x05A, "Escape": 0x076,
    "Yes": 0x035, "No": 0x031, "Map": 0x00D,
    "Weapon 1": 0x016, "Weapon 2": 0x01E, "Weapon 3": 0x026, "Weapon 4": 0x025,
    "Weapon 5": 0x02E, "Weapon 6": 0x036, "Weapon 7": 0x03D,
}
BINDINGS = {
    "Up": "Forward", "w": "Forward", "Down": "Backward", "s": "Backward",
    "Left": "Turn left", "a": "Turn left", "Right": "Turn right", "d": "Turn right",
    "Control_L": "Fire", "Control_R": "Fire", "f": "Fire",
    "space": "Use", "e": "Use", "Shift_L": "Run", "Shift_R": "Run", "r": "Run",
    "Alt_L": "Strafe", "Alt_R": "Strafe", "q": "Strafe",
    "Return": "Enter", "KP_Enter": "Enter", "Escape": "Escape",
    "y": "Yes", "n": "No", "Tab": "Map",
    **{str(number): f"Weapon {number}" for number in range(1, 8)},
}
JTAG_EVENT_SECONDS = 3.0


class ControlError(Exception):
    """Control has stopped; endpoint input state may be uncertain."""


class JtagClient:
    """One event per call through the common JTAG keyboard Session; `failed` once uncertain."""

    def __init__(self, jtag, transport, *, clock=None):
        import time
        self.jtag, self.clock = jtag, clock or time.monotonic
        self.session = jtag.Session(transport)

    @property
    def failed(self):
        return self.session.poisoned

    def send(self, event):
        from src.nand2doom.scripts.host.n2d_jtag_keyboard import event_codes
        codes = event_codes(event)
        sent = self.jtag.send_all(self.session, codes, deadline=self.clock() + JTAG_EVENT_SECONDS)
        if sent != len(codes):
            self.session.poisoned = True
            raise ControlError("JTAG event only partly accepted")


class Session:
    """Worker-owned single Client/handle; no GUI calls or protocol duplication."""

    def __init__(self, port, timeout=1.0, baud=None, *, opener=None, jtag=None, jtag_module=None):
        if not isinstance(port, str) or not port.strip():
            raise ValueError("an explicit port is required")
        if not math.isfinite(timeout) or not .01 <= timeout <= 1:
            raise ValueError("timeout must be finite and in 0.01..1 seconds")
        self.port, self.timeout = port.strip(), timeout
        self.jtag, self.jtag_module = jtag, jtag_module
        if jtag is None:
            from src.nand2doom.scripts.host import n2d_uart as uart
            self.uart = uart
            self.baud = uart.CONFIG["baudrate"] if baud is None else baud
            self.opener = opener or uart.open_serial
        else:
            self.uart, self.baud, self.opener = None, baud, None
        self.transport = self.client = None
        self.sources = {}
        self.failed = self.closed = self.started = False

    def _close_handle(self):
        transport, self.transport = self.transport, None
        self.closed = True
        if transport is not None:
            result = transport.close()  # Why: close is attempted once, even if it raises, so a release is never repeated.
            if self.jtag is not None and result != "closed":
                raise ControlError(f"JTAG release: {result}")

    def abort(self, reason):
        self.failed = True
        self.sources.clear()
        try:
            self._close_handle()
        except Exception as error:
            return f"{reason}; handle close failed: {error}"
        return str(reason)

    def connect(self):
        if self.started:
            raise ControlError("this session cannot reconnect")
        self.started = True
        if self.jtag is not None:
            try:
                jtag = self.jtag_module
                if jtag is None:
                    from src.common.scripts.jtag import n2a_jtag_keyboard as jtag
                self.transport = jtag.open_transport(self.jtag)
                self.client = JtagClient(jtag, self.transport)
            except Exception as error:
                raise ControlError(self.abort(error)) from error
            return
        try:
            self.transport = self.opener(self.port, self.timeout, self.baud)
            self.client = self.uart.Client(self.transport, self.timeout)
            identity = self.client.command(self.uart.wire.CMD_PING)
            if identity != (self.uart.CONFIG["identity"], 0):
                raise ControlError("endpoint is halted; controller never resumes it")
        except Exception as error:
            raise ControlError(self.abort(error)) from error

    def _require(self):
        if (self.closed or self.failed or self.client is None or self.client.failed):
            raise ControlError("session is not healthy and connected")

    def _send(self, action, release):
        self._require()
        event = ACTIONS[action] | (0x100 if release else 0)
        try:
            if self.jtag is not None:
                self.client.send(event)
            else:
                self.client.command(self.uart.wire.CMD_INPUT,
                                    address=self.uart.wire.INPUT_ADDRESS,
                                    words=(event,), byte_enable=15)
        except Exception as error:
            raise ControlError(self.abort(error)) from error

    def press(self, source, action):
        self._require()
        if action not in ACTIONS:
            raise ValueError("unsupported control action")
        if source in self.sources:
            if self.sources[source] != action:
                raise ValueError("source already owns a different action")
            return
        if action not in self.sources.values():
            self._send(action, False)
        self.sources[source] = action

    def release(self, source):
        self._require()
        if source not in self.sources:
            return
        action = self.sources[source]
        if list(self.sources.values()).count(action) == 1:
            self._send(action, True)
        del self.sources[source]

    def release_all(self):
        self._require()
        for action in list(dict.fromkeys(self.sources.values())):
            self._send(action, True)
            self.sources = {s: a for s, a in self.sources.items() if a != action}

    def close(self):
        if self.closed:
            return
        try:
            if self.client is not None and not self.failed and not self.client.failed:
                self.release_all()
            elif self.client is not None and self.client.failed:
                raise ControlError("client poisoned; no release commands sent")
            self._close_handle()
        except Exception as error:
            raise ControlError(self.abort(error)) from error


class Worker:
    """One serialized worker, bounded input FIFO, priority stop/abort flags."""

    def __init__(self, port, timeout=1.0, baud=None, *, factory=Session):
        self.port, self.timeout, self.factory = port, timeout, factory
        self.baud = baud
        self.commands = queue.Queue(maxsize=32)
        self.notices = queue.SimpleQueue()
        self.stopping, self.aborting, self.ready = (threading.Event() for _ in range(3))
        self.reason = ""
        self.terminal = None  # Why: set once before the thread exits; it, not the advisory notices, is the final disposition.
        self.thread = threading.Thread(target=self._run, name="n2d-controls", daemon=True)

    def start(self):
        self.thread.start()

    def post(self, operation, *args):
        if not self.ready.is_set() or self.stopping.is_set() or self.aborting.is_set():
            return False
        if operation not in ("press", "release", "release_all"):
            raise ValueError("unsupported worker operation")
        try:
            self.commands.put_nowait((operation, args))
            return True
        except queue.Full:
            self.reason = "input backlog exceeded 32 operations; held state is uncertain"
            self.aborting.set()
            self.ready.clear()
            return False

    def stop(self):
        self.stopping.set()
        self.ready.clear()

    def _finish(self, kind, text):
        self.terminal = (kind, text)
        self.notices.put(self.terminal)

    def _run(self):
        session = None
        try:
            if self.stopping.is_set():
                self._finish("closed", "Connection cancelled before opening transport")
                return
            session = self.factory(self.port, self.timeout, self.baud)
            session.connect()
            if not self.stopping.is_set():
                self.ready.set()
                self.notices.put(("connected", f"Connected on {self.port}; input enabled"))
            while True:
                if self.aborting.is_set():
                    raise ControlError(self.reason)
                if self.stopping.is_set():
                    break  # Why: once stopping, queued input is stale; only actually owned holds are released.
                try:
                    operation, args = self.commands.get(timeout=.02)
                except queue.Empty:
                    continue
                if self.aborting.is_set() or self.stopping.is_set():
                    continue
                getattr(session, operation)(*args)
            session.close()
            self._finish("closed", "Disconnected; acknowledged held inputs released")
        except Exception as error:
            self.aborting.set()
            self.reason = str(error)
            if session is not None:
                self.reason = session.abort(self.reason)
            self._finish("fault", self.reason + "; input state may be uncertain; operator recovery required")
        finally:
            self.ready.clear()


class InputEvents:
    """Tk-independent callback state; scheduler is Tk's main-thread idle queue."""

    def __init__(self, submit, after_idle, cancel):
        self.submit, self.after_idle, self.cancel = submit, after_idle, cancel
        self.keys, self.pending, self.suppressed = {}, {}, set()
        self.mouse = False

    def key_press(self, code, symbol):
        if code in self.pending:
            self.cancel(self.pending.pop(code))
        if code in self.keys or code in self.suppressed:
            return
        action = BINDINGS.get(symbol, BINDINGS.get(symbol.lower()))
        if action and self.submit("press", ("key", code), action):
            self.keys[code] = action

    def key_release(self, code):
        if (code in self.keys or code in self.suppressed) and code not in self.pending:
            self.pending[code] = self.after_idle(lambda: self._key_up(code))

    def _key_up(self, code):
        self.pending.pop(code, None)
        self.suppressed.discard(code)
        if code in self.keys:
            self.submit("release", ("key", code))
            del self.keys[code]

    def mouse_press(self, action):
        if not self.mouse and self.submit("press", ("mouse", 1), action):
            self.mouse = True

    def mouse_release(self):
        if self.mouse:
            self.submit("release", ("mouse", 1))
            self.mouse = False

    def clear_local(self):
        for job in self.pending.values():
            self.cancel(job)
        self.pending.clear()
        self.suppressed.update(self.keys)
        self.keys.clear()
        self.mouse = False

    def release_all(self):
        self.clear_local()
        self.submit("release_all")


class App:
    """All Tk calls stay on the creating/UI thread."""

    def __init__(self, root, tk, *, port="", timeout=1., baud=None, smoke=False, worker_factory=Worker,
                 link="Port", connect_now=False):
        self.root, self.tk, self.timeout, self.smoke = root, tk, timeout, smoke
        self.baud = baud
        self.worker_factory, self.worker = worker_factory, None
        self.closing = self.faulted = False
        self.grabbed = None
        root.title("nand2doom — local controls")
        root.geometry("640x460")
        self.port = tk.StringVar(value=port)
        self.status = tk.StringVar(value=f"Disconnected — select the known {link.lower()}, then Connect")
        bar = tk.Frame(root, padx=12, pady=12)
        bar.pack(fill="x")
        tk.Label(bar, text=f"{link}:").pack(side="left")
        self.entry = tk.Entry(bar, textvariable=self.port, width=16)
        self.entry.pack(side="left", padx=6)
        self.connect_button = tk.Button(bar, text="Connect", command=self.connect, takefocus=False)
        self.connect_button.pack(side="left")
        self.disconnect_button = tk.Button(bar, text="Disconnect", command=self.disconnect, takefocus=False)
        self.disconnect_button.pack(side="left", padx=6)
        self.release_button = tk.Button(bar, text="Release All", command=self.release_all, takefocus=False)
        self.release_button.pack(side="left")
        tk.Label(root, textvariable=self.status, wraplength=600, fg="#184c70").pack(padx=12, pady=8)
        controls = tk.Frame(root)
        controls.pack(pady=8)
        positions = [("Forward", 0, 1), ("Turn left", 1, 0), ("Backward", 1, 1),
                     ("Turn right", 1, 2), ("Fire", 2, 0), ("Use", 2, 1), ("Run", 2, 2),
                     ("Strafe", 3, 0), ("Enter", 3, 1), ("Escape", 3, 2)]
        self.buttons = []
        for action, row, column in positions:
            button = tk.Button(controls, text=action, width=15, height=2, takefocus=False)
            button.grid(row=row, column=column, padx=4, pady=3)
            button.bind("<ButtonPress-1>", lambda event, a=action: self.mouse_down(event, a))
            self.buttons.append(button)
        tk.Label(root, text="Arrows / WASD: move & turn   Ctrl/F: Fire   Space/E: Use\n"
                 "Shift/R: Run   Alt/Q + turn: Strafe   Enter / Esc   Y / N   1-7: weapons   Tab: map\n"
                 "Hold controls; Release All or leave this window to release.").pack(pady=8)
        self.events = InputEvents(self.submit, root.after_idle, root.after_cancel)
        root.bind("<KeyPress>", self.key_down)
        root.bind("<KeyRelease>", self.key_up)
        root.bind_all("<ButtonRelease-1>", self.mouse_up)
        root.bind("<FocusOut>", lambda event: root.after_idle(self.focus_check))
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.render()
        root.after(20, self.poll)
        if connect_now and not smoke:
            root.after_idle(self.connect)

    def enabled(self):
        return bool(self.worker and self.worker.ready.is_set() and not self.worker.aborting.is_set()
                    and not self.worker.stopping.is_set() and not self.closing and not self.faulted)

    def submit(self, operation, *args):
        return bool(self.enabled() and self.worker.post(operation, *args))

    def render(self):
        live = self.enabled()
        busy = self.worker is not None
        self.entry.configure(state="disabled" if busy else "normal")
        self.connect_button.configure(state="normal" if not busy and not self.faulted and not self.smoke and not self.closing else "disabled")
        self.disconnect_button.configure(state="normal" if busy and not self.closing else "disabled")
        for widget in [self.release_button, *self.buttons]:
            widget.configure(state="normal" if live else "disabled")

    def connect(self):
        if self.smoke or self.worker or self.faulted or self.closing:
            return
        if not self.port.get().strip():
            self.status.set("Enter the explicitly identified port; no port scanning is performed")
            return
        self.worker = self.worker_factory(self.port.get().strip(), self.timeout, self.baud)
        self.status.set("Connecting: checking the endpoint before any input…")
        self.render()
        self.worker.start()
        self.root.focus_set()

    def key_down(self, event):
        if self.enabled() and event.widget is not self.entry:
            self.events.key_press(event.keycode, event.keysym)
            return "break"

    def key_up(self, event):
        self.events.key_release(event.keycode)
        if self.enabled() and event.widget is not self.entry:
            return "break"

    def mouse_down(self, event, action):
        if self.enabled():
            self.events.mouse_press(action)
            if self.events.mouse:
                self.grabbed = event.widget
                self.grabbed.grab_set()
        return "break"

    def ungrab(self):
        if self.grabbed is not None:
            self.grabbed.grab_release()
            self.grabbed = None

    def mouse_up(self, event=None):
        self.events.mouse_release()
        self.ungrab()

    def release_all(self):
        self.events.release_all()
        self.ungrab()

    def focus_check(self):
        focused = self.root.focus_displayof()
        if focused is None or focused.winfo_toplevel() != self.root:
            self.release_all()

    def disconnect(self):
        self.events.clear_local()
        self.ungrab()
        if self.worker:
            self.worker.stop()
            self.status.set("Disconnecting: releasing acknowledged holds, then closing…")
        self.render()

    def close(self):
        self.closing = True
        self.disconnect()
        if self.worker is None:
            self.root.destroy()

    def poll(self):
        if self.worker:
            while True:
                try:
                    kind, text = self.worker.notices.get_nowait()
                except queue.Empty:
                    break
                self.notice(kind, text)
            if not self.worker.thread.is_alive():
                # Why: the worker may publish after the empty-queue observation, so its exit,
                # not queue timing, makes its final disposition authoritative.
                terminal = self.worker.terminal
                if (not isinstance(terminal, tuple) or len(terminal) != 2
                        or terminal[0] not in ("closed", "fault")):
                    terminal = ("fault", "Worker exited without terminal disposition; "
                                "input state may be uncertain; operator recovery required")
                self.notice(*terminal)
                self.worker = None
                if self.closing:
                    self.root.destroy()
                    return
        self.render()
        self.root.after(20, self.poll)

    def notice(self, kind, text):
        self.status.set(text)
        if kind == "fault":
            self.faulted = True
            self.events.clear_local()
            self.ungrab()


def jtag_settings(args):
    """Validated common JTAG settings, or None for the UART transport."""
    if args.transport != "jtag":
        return None
    from src.common.scripts.jtag import n2a_jtag_keyboard as jtag
    return jtag.settings({"jtag_cable": args.cable or "", "jtag_device": args.device or "",
                          "jtag_tck_hz": "6000000", "jtag_instance": "0",
                          "jtag_clock_mode": "fixed-original-6mhz", "jtag_cable_index": "",
                          "quartus_bin": args.quartus_bin or ""})


def parser():
    result = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    result.add_argument("--transport", choices=("uart", "jtag"), default="uart",
                        help="UART CMD_INPUT (default) or the USB-Blaster JTAG keyboard")
    result.add_argument("--port", default="", help="UART port; prefills the window, Connect is still required")
    result.add_argument("--cable", help="JTAG: exact cable name, e.g. 'USB-Blaster [1-1]'")
    result.add_argument("--device", help="JTAG: exact device name, e.g. '@1: EP4CE115 (0x020F70DD)'")
    result.add_argument("--quartus-bin", help="JTAG: directory holding quartus_stp (default: PATH)")
    result.add_argument("--timeout", type=float, default=1., help="UART transaction timeout, 0.01..1 seconds")
    from src.nand2doom.scripts.host import n2d_uart as uart
    uart.add_baud_option(result)
    result.add_argument("--connect", action="store_true",
                        help="connect at start instead of waiting for the Connect button")
    result.add_argument("--terminal", action="store_true",
                        help="terminal controls instead of the Tk window (also used when Tk is missing)")
    result.add_argument("--plan", "--dry-run", action="store_true", help="print bindings without Tk/serial")
    result.add_argument("--smoke-test", action="store_true", help="open disconnected GUI for 300ms; never serial")
    return result


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    if not math.isfinite(args.timeout) or not .01 <= args.timeout <= 1:
        p.error("timeout must be finite and in 0.01..1 seconds")
    link = args.cable if args.transport == "jtag" else args.port
    if args.plan:
        print(json.dumps({"transport": args.transport, "port": args.port, "cable": args.cable or "",
                          "device": args.device or "", "auto_connect": args.connect or args.terminal,
                          "timeout": args.timeout, "baud": args.baud, "terminal": args.terminal,
                          "make": ACTIONS, "bindings": BINDINGS,
                          "queue_limit": 32}, indent=2))
        return 0
    try:
        settings = jtag_settings(args)
    except ValueError as error:
        p.error(str(error))
    factory = Session if settings is None else functools.partial(Session, jtag=settings)
    tk = root = None
    if not args.terminal:
        try:
            import tkinter as tk
            root = tk.Tk()
        except (ImportError, RuntimeError) as error:
            if args.smoke_test:
                print(f"GUI unavailable: {error}; use a Python installation with Tk and a desktop", file=sys.stderr)
                return 3
            print(f"GUI unavailable ({error}); using terminal controls", file=sys.stderr)
        except Exception as error:
            print(f"Cannot open Tk window: {error}; --plan needs no display", file=sys.stderr)
            return 3
    if root is None:
        if not link:
            p.error("terminal controls connect at once: give --port (UART) or --cable/--device (JTAG)")
        from src.nand2doom.scripts.host import n2d_control_terminal as terminal
        return terminal.main_loop(factory(link, args.timeout, args.baud))
    app = App(root, tk, port=link or "", timeout=args.timeout, baud=args.baud, smoke=args.smoke_test,
              worker_factory=functools.partial(Worker, factory=factory),
              link="JTAG cable" if settings else "Port", connect_now=args.connect)
    if args.smoke_test:
        root.after(300, app.close)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        # Why: Ctrl-C in the launching terminal must still release acknowledged holds
        # before closing.
        app.close()
        root.mainloop()
    if args.smoke_test:
        print(json.dumps({"smoke": "PASS", "tk": tk.TkVersion, "serial_opened": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
