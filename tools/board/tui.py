"""The board console: a terminal app (Textual) that gets a program running on a
board in four guided steps, cable, program, load and keyboard, then plays.

Project and board are drop-down lists, preset to the board the cable shows;
each step has a light (grey to do, yellow running, green done, red failed)
with what it found and, on a failure, what to do in plain words; one button
always does the next step. Play hands the whole terminal to `play.py` until
Ctrl+]. Board actions run in order on one worker thread, so the screen never
blocks, and programming closes the JTAG keyboard first because quartus_pgm
needs the cable alone.
Spec: wiki/tools/board/spec.md#console.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import queue
import threading

from textual.app import App, ComposeResult, SuspendNotSupported
from textual.containers import Horizontal
from textual.widgets import Button, Footer, Header, Input, Label, RichLog, Select, Static

from tools.board import board as console
from tools.board.keys import brk, make
from tools.board.play import play

STEPS = ("cable", "program", "load", "keyboard")
TITLES = {"cable": "Cable", "program": "FPGA programmed", "load": "Program loaded", "keyboard": "Keyboard"}
LIGHTS = {"todo": ("○", "grey50"), "busy": ("◐", "yellow"), "ok": ("●", "green"), "fail": ("●", "red")}
NEXT = {"cable": "1. Check the cable", "program": "2. Program the FPGA", "load": "3. Load the program",
        "keyboard": "4. Connect the keyboard", None: "5. Play"}


class Worker:
    """One thread runs board actions and key sends in order; results go to `post`."""

    def __init__(self, post):
        self.post, self.jobs = post, queue.Queue()
        self.keyboard = None
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            job = self.jobs.get()
            try:
                job()
            except Exception as exc:  # Why: any failure is reported on screen; the worker keeps serving
                self.post("log", f"error: {type(exc).__name__}: {exc}")

    def submit(self, job):
        self.jobs.put(job)

    def close_keyboard(self):
        if self.keyboard is not None:
            keyboard, self.keyboard = self.keyboard, None
            keyboard.close()
            self.post("step", ("keyboard", "todo", "disconnected", ""))

    def send(self, codes: bytes, label: str = ""):
        if not codes:
            return
        if self.keyboard is None:
            self.post("step", ("keyboard", "fail", f"not connected: {label} was dropped",
                               "press Connect keyboard (the FPGA must be programmed first)"))
            return
        try:
            accepted = self.keyboard.send(codes)
        except Exception as exc:
            self.close_keyboard()
            self.post("step", ("keyboard", "fail", f"key send failed: {exc}",
                               "press Connect keyboard again; if it keeps failing, program the FPGA again"))
            raise
        accepted = len(codes) if accepted is None else accepted
        self.post("sent", (label, accepted, len(codes), codes.hex(" ")))


@dataclass
class Step:
    state: str = "todo"
    detail: str = ""
    fix: str = ""


def image_target(board) -> str:
    return f"bazel build //src/{board.project}:{board.name}_image"


class BoardConsole(App):
    """The console app; `actions` is the board module (a fake in tests)."""

    TITLE = "Board console"
    CSS = """
    .row { height: auto; padding: 0 1; }
    .row Label { padding: 1 1 0 1; }
    #project, #board { width: 30; }
    #sof, #program { width: 1fr; }
    #port { width: 22; }
    #title { padding: 1 2 0 2; }
    .step { height: auto; padding: 0 2; }
    .fix { color: $error; padding: 0 6; height: auto; }
    #next { margin: 1 2 0 2; width: auto; min-width: 36; }
    #buttons Button { margin-right: 1; min-width: 10; }
    #keyline { padding: 1 2; color: $text-muted; height: auto; }
    #log { height: 12; border: round $primary; display: none; }
    #log.shown { display: block; }
    """
    BINDINGS = [("n", "next_step", "Next step"), ("g", "play", "Play"), ("l", "toggle_log", "Log"),
                ("q", "quit", "Quit")]

    def __init__(self, boards: dict, first=None, *, port: str = "", quartus_bin=None, actions=console,
                 play_fn=play):
        super().__init__()
        self.boards, self.actions, self.quartus_bin, self.play_fn = boards, actions, quartus_bin, play_fn
        self.first = first
        # Why: a start-up board the chain did not confirm follows the first check that finds the board
        self.follow = first is None or "choose the board" in first[1]
        self.board = first[0] if first else boards[sorted(boards)[0]]
        self.port = port
        self.steps = {name: Step() for name in STEPS}
        self.last_key = "no key sent yet"
        self.play_pending = False
        self.results = queue.Queue()
        self.worker = Worker(lambda kind, value: self.results.put((kind, value)))

    def drain(self):
        """Apply the worker's results on the app thread, in order."""
        while not self.results.empty():
            self.on_result(*self.results.get())

    def compose(self) -> ComposeResult:
        projects = sorted({b.project for b in self.boards.values()})
        yield Header()
        with Horizontal(classes="row"):
            yield Label("Project")
            yield Select([(p, p) for p in projects], value=self.board.project, allow_blank=False, id="project")
            yield Label("Board")
            yield Select(self.board_options(self.board.project), value=self.board.key, allow_blank=False,
                         id="board")
            yield Label("UART port")
            yield Input(self.port, placeholder="/dev/ttyUSB0", id="port")
        with Horizontal(classes="row"):
            yield Label("FPGA image")
            yield Input(str(self.actions.resolve_path(self.board.sof)), id="sof")
        with Horizontal(classes="row"):
            yield Label(self.board.program_label, id="program_label")
            yield Input(self.default_program(), placeholder="path of the program file", id="program")
        yield Static(id="title")
        for name in STEPS:
            yield Static(id=f"step_{name}", classes="step")
            yield Static(id=f"fix_{name}", classes="fix")
        yield Button("Next", id="next", variant="success")
        with Horizontal(id="buttons", classes="row"):
            yield Button("Check cable", id="do_cable")
            yield Button("Program FPGA", id="do_program")
            yield Button("Load program", id="do_load")
            yield Button("Connect keyboard", id="do_keyboard")
            yield Button("Test key", id="do_test")
            yield Button("Play", id="do_play", variant="primary")
            yield Button("Show log", id="do_log")
        yield Static(id="keyline")
        yield RichLog(id="log", wrap=True, markup=False)
        yield Footer()

    def on_mount(self):
        self.set_interval(0.05, self.drain)
        if self.first:
            self.log_line(self.first[1])
        self.refresh_steps()
        self.do_cable()

    def board_options(self, project: str):
        return [(b.name, b.key) for b in sorted(self.boards.values(), key=lambda b: b.key)
                if b.project == project]

    def default_program(self) -> str:
        default = self.board.program_default
        return str(self.actions.resolve_path(default)) if default else ""

    def on_select_changed(self, event: Select.Changed):
        if event.select.id == "project" and event.value != self.board.project:
            options = self.board_options(event.value)
            same = [key for _, key in options if self.boards[key].name == self.board.name]
            board_select = self.query_one("#board", Select)
            board_select.set_options(options)
            board_select.value = same[0] if same else options[0][1]
        elif event.select.id == "board" and event.value in self.boards and event.value != self.board.key:
            self.choose(self.boards[event.value])

    def choose(self, board):
        self.follow = False
        self.board = board
        self.query_one("#sof", Input).value = str(self.actions.resolve_path(board.sof))
        self.query_one("#program", Input).value = self.default_program()
        self.query_one("#program_label", Label).update(board.program_label)
        self.worker.submit(self.worker.close_keyboard)
        self.steps = {name: Step() for name in STEPS}
        self.log_line(f"selected {board.key}")
        self.do_cable()

    def set_step(self, name, state, detail="", fix=""):
        self.steps[name] = Step(state, detail, fix)
        self.refresh_steps()

    def next_name(self) -> str | None:
        return next((n for n in STEPS if self.steps[n].state != "ok"), None)

    def refresh_steps(self):
        self.query_one("#title", Static).update(
            f"[b]Get {self.board.project} running on the {self.board.name.upper()}[/b]")
        for number, name in enumerate(STEPS, 1):
            step = self.steps[name]
            mark, colour = LIGHTS[step.state]
            self.query_one(f"#step_{name}", Static).update(
                f"[{colour}]{mark}[/{colour}]  {number}. [b]{TITLES[name]}[/b]   {step.detail}")
            fix = self.query_one(f"#fix_{name}", Static)
            fix.update(f"→ {step.fix}" if step.fix else "")
            fix.display = bool(step.fix)
        self.query_one("#next", Button).label = f"Next: {NEXT[self.next_name()]}"
        self.query_one("#keyline", Static).update(f"Last key: {self.last_key}")

    def on_result(self, kind, value):
        if kind == "step":
            self.set_step(*value)
            self.log_line(f"{TITLES[value[0]]}: {value[2]}")
            if value[0] == "keyboard" and self.play_pending and value[1] != "busy":
                self.play_pending = False
                if value[1] == "ok":
                    self.action_play()
        elif kind == "select":
            board, why = value
            self.follow = False
            self.log_line(why)
            self.query_one("#project", Select).value = board.project
            self.query_one("#board", Select).set_options(self.board_options(board.project))
            self.query_one("#board", Select).value = board.key
            self.choose(board)
        elif kind == "sent":
            label, accepted, total, hexed = value
            self.last_key = f"{label}: the board took {accepted} of {total} bytes ({hexed})"
            self.refresh_steps()
        else:
            self.log_line(value)

    def log_line(self, text: str):
        self.query_one("#log", RichLog).write(text)

    def on_button_pressed(self, event: Button.Pressed):
        {"next": self.action_next_step, "do_cable": self.do_cable, "do_program": self.do_program,
         "do_load": self.do_load, "do_keyboard": self.do_keyboard, "do_test": self.do_test,
         "do_play": self.action_play, "do_log": self.action_toggle_log}[event.button.id]()

    def action_next_step(self):
        {"cable": self.do_cable, "program": self.do_program, "load": self.do_load,
         "keyboard": self.do_keyboard, None: self.action_play}[self.next_name()]()

    def action_toggle_log(self):
        log = self.query_one("#log", RichLog)
        log.toggle_class("shown")
        self.query_one("#do_log", Button).label = "Hide log" if log.has_class("shown") else "Show log"

    def do_cable(self):
        board = self.board
        self.set_step("cable", "busy", "checking with jtagconfig...")

        def job():
            if self.worker.keyboard is not None:
                self.worker.post("step", ("cable", "ok", "in use by this console's keyboard", ""))
                return
            ready, finding, hint = self.actions.check_chain(self.boards, board, self.quartus_bin)
            if not ready and self.follow:
                found, why = self.actions.detect_board(self.boards, self.quartus_bin)
                if "choose the board" not in why and found.key != board.key:
                    self.worker.post("select", (found, why))
                    return
            self.worker.post("step", ("cable", "ok" if ready else "fail", finding, "" if ready else hint))
        self.worker.submit(job)

    def do_program(self):
        board, sof = self.board, Path(self.query_one("#sof", Input).value.strip())
        if not sof.is_file():
            self.set_step("program", "fail", f"no FPGA image at {sof}",
                          f"build it first with: {image_target(board)}   then press Program FPGA")
            return
        self.set_step("program", "busy", "programming (about 10 s)...")
        self.steps["load"] = self.steps["keyboard"] = Step()

        def job():
            self.worker.close_keyboard()
            try:
                cable = self.actions.program(board, sof, quartus_bin=self.quartus_bin,
                                             out=lambda t: self.worker.post("log", t))
            except Exception as exc:
                self.worker.post("step", ("program", "fail", str(exc)[:200],
                                   "make step 1 (Cable) green first, then press Program FPGA again"))
                raise
            self.worker.post("step", ("program", "ok", f"{sof.name} through {cable}", ""))
        self.worker.submit(job)

    def do_load(self):
        board = self.board
        program = self.query_one("#program", Input).value.strip()
        port = self.query_one("#port", Input).value.strip()
        label = board.program_label.lower()
        if not port:
            ports = self.actions.serial_ports()
            self.set_step("load", "fail", "no UART port chosen",
                          "type the USB-serial cable's port in UART port"
                          + (f" (found: {', '.join(ports)})" if ports else " (none found: plug the cable in)"))
            return
        if not program or not Path(program).is_file():
            self.set_step("load", "fail", f"no {label} file" + (f" at {program}" if program else ""),
                          f"type the path of the {label} in its box, then press Load program")
            return
        self.set_step("load", "busy", f"loading {Path(program).name} over {port}...")
        self.steps["keyboard"] = Step()

        def job():
            try:
                self.actions.load(board, Path(program), port, out=lambda t: self.worker.post("log", t))
            except Exception as exc:
                self.worker.post("step", ("load", "fail", str(exc)[:200],
                                   "the board did not answer on the UART: program the FPGA (step 2), check the "
                                   "USB-serial cable's TX/RX wires and the port, then press Load program"))
                raise
            self.worker.post("step", ("load", "ok", f"{Path(program).name} loaded and started", ""))
        self.worker.submit(job)

    def do_keyboard(self):
        board = self.board
        self.set_step("keyboard", "busy", "connecting over JTAG (a few seconds)...")

        def job():
            self.worker.close_keyboard()
            try:
                cable, device = self.actions.jtag_device(board, quartus_bin=self.quartus_bin)
                self.worker.keyboard = self.actions.Keyboard(cable, device, self.quartus_bin)
            except Exception as exc:
                self.worker.post("step", ("keyboard", "fail", str(exc)[:200],
                                   "program the FPGA (step 2) and make step 1 green; close any other console "
                                   "using the cable; then press Connect keyboard"))
                raise
            self.worker.post("step", ("keyboard", "ok", f"connected on {cable}: press Test key or Play", ""))
        self.worker.submit(job)

    def do_test(self):
        if self.worker.keyboard is None and self.steps["keyboard"].state != "busy":
            self.do_keyboard()
        self.worker.submit(lambda: self.worker.send(make("space") + brk("space"), "Test key (Space)"))

    def action_play(self):
        if self.worker.keyboard is None:
            self.play_pending = True
            if self.steps["keyboard"].state != "busy":
                self.do_keyboard()
            self.notify("Connecting the keyboard first; play starts when it is connected")
            return
        title = f"{self.board.project} on the {self.board.name.upper()}"
        send = lambda codes, label: self.worker.submit(lambda: self.worker.send(codes, label))  # noqa: E731
        try:
            with self.suspend():
                count = self.play_fn(send, title, pad=dict(self.board.pad))
        except SuspendNotSupported:
            # Why: the headless test driver cannot hand the terminal over; play runs in place
            count = self.play_fn(send, title, pad=dict(self.board.pad))
        self.log_line(f"play: {count} key events sent")


def main(boards: dict, *, port: str = "", quartus_bin=None, first=None) -> int:
    if not boards:
        raise RuntimeError("no src/*/cfg/board.json found")
    BoardConsole(boards, first, port=port, quartus_bin=quartus_bin).run()
    return 0
