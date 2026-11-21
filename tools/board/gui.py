"""The board console window: four controls over board.py.

Board, SOF and Program, program file and serial port and Load, and a keyboard
pane that forwards every key press and release while it has focus. Board
actions run on one worker thread so the window never blocks, and programming
closes the JTAG keyboard first because quartus_pgm needs the cable alone.
Spec: wiki/tools/board/spec.md#window.
"""
from __future__ import annotations

import queue
import threading
import time
from pathlib import Path

from tools.board import board as console
from tools.board.keys import KeyState

FLUSH_MS = 10


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
            except Exception as exc:  # Why: any failure is reported in the window; the worker keeps serving
                self.post("error", f"{type(exc).__name__}: {exc}")

    def submit(self, job):
        self.jobs.put(job)

    def close_keyboard(self):
        if self.keyboard is not None:
            keyboard, self.keyboard = self.keyboard, None
            self.post("keyboard", f"keyboard closed ({keyboard.close()})")

    def send(self, codes: bytes):
        if self.keyboard is None or not codes:
            return
        try:
            self.keyboard.send(codes)
        except Exception as exc:
            self.close_keyboard()
            raise RuntimeError(f"keyboard stopped: {exc}") from exc


def main(boards: dict, *, port: str = "", quartus_bin=None) -> int:
    try:
        import tkinter as tk
        from tkinter import filedialog, ttk
    except ImportError:
        raise RuntimeError("the window needs Tk (python3-tk); use the list/program/load/keys commands") from None
    if not boards:
        raise RuntimeError("no src/*/cfg/board.json found")

    root = tk.Tk()
    root.title("Board console")
    events = queue.Queue()
    worker = Worker(lambda kind, text: events.put((kind, text)))
    state = KeyState()

    keys = sorted(boards)
    board_var = tk.StringVar(value=keys[0])
    sof_var, program_var = tk.StringVar(), tk.StringVar()
    port_var = tk.StringVar(value=port)
    status_var = tk.StringVar(value="keyboard: not connected")

    def selected():
        return boards[board_var.get()]

    def on_board(*_):
        board = selected()
        sof_var.set(str(console.resolve_path(board.sof)))
        program_var.set(str(console.resolve_path(board.program_default)) if board.program_default else "")
        program_label.config(text=board.program_label)

    frame = ttk.Frame(root, padding=8)
    frame.grid(sticky="nsew")
    root.columnconfigure(0, weight=1)
    root.rowconfigure(0, weight=1)
    frame.columnconfigure(1, weight=1)

    ttk.Label(frame, text="Board").grid(row=0, column=0, sticky="w")
    ttk.Combobox(frame, textvariable=board_var, values=keys, state="readonly").grid(
        row=0, column=1, columnspan=3, sticky="ew")

    def browse_sof():
        path = filedialog.askopenfilename(filetypes=[("FPGA image", "*.sof")])
        if path:
            sof_var.set(path)
            match = console.board_for_sof(boards, Path(path))
            if match is not None and match.key != board_var.get():
                board_var.set(match.key)
                sof_var.set(path)

    def do_program():
        board, sof = selected(), Path(sof_var.get())
        def job():
            worker.close_keyboard()
            console.program(board, sof, quartus_bin=quartus_bin, out=lambda t: worker.post("log", t))
            worker.post("log", "programmed: load the program, then connect the keyboard")
        worker.submit(job)

    ttk.Label(frame, text="SOF").grid(row=1, column=0, sticky="w")
    ttk.Entry(frame, textvariable=sof_var).grid(row=1, column=1, sticky="ew")
    ttk.Button(frame, text="…", width=3, command=browse_sof).grid(row=1, column=2)
    ttk.Button(frame, text="Program FPGA", command=do_program).grid(row=1, column=3, sticky="ew")

    def browse_program():
        board = selected()
        path = filedialog.askopenfilename(filetypes=[(board.program_label, " ".join(board.program_patterns)),
                                                     ("All files", "*")])
        if path:
            program_var.set(path)

    def do_load():
        board, program_file, chosen_port = selected(), Path(program_var.get()), port_var.get()
        worker.submit(lambda: console.load(board, program_file, chosen_port,
                                           out=lambda t: worker.post("log", t)))

    program_label = ttk.Label(frame, text="Program")
    program_label.grid(row=2, column=0, sticky="w")
    ttk.Entry(frame, textvariable=program_var).grid(row=2, column=1, sticky="ew")
    ttk.Button(frame, text="…", width=3, command=browse_program).grid(row=2, column=2)
    ttk.Button(frame, text="Load program", command=do_load).grid(row=2, column=3, sticky="ew")

    ttk.Label(frame, text="UART port").grid(row=3, column=0, sticky="w")
    ttk.Combobox(frame, textvariable=port_var, values=console.serial_ports(),
                 postcommand=lambda: None).grid(row=3, column=1, columnspan=3, sticky="ew")

    def do_connect():
        board = selected()
        def job():
            worker.close_keyboard()
            cable, device = console.jtag_device(board, quartus_bin=quartus_bin)
            worker.keyboard = console.Keyboard(cable, device, quartus_bin)
            worker.post("keyboard", f"keyboard: {board.key} on {cable}; click the pane and type")
        worker.submit(job)

    pane = tk.Label(frame, text="Keyboard: connect, click here, then type.\nEvery key press and release goes to the board.",
                    relief="sunken", height=6, takefocus=1, background="#20242a", foreground="#e8e8e8")
    pane.grid(row=4, column=0, columnspan=4, sticky="nsew", pady=6)
    frame.rowconfigure(4, weight=1)
    ttk.Button(frame, text="Connect keyboard", command=do_connect).grid(row=5, column=0, sticky="w")
    ttk.Label(frame, textvariable=status_var).grid(row=5, column=1, columnspan=3, sticky="w")

    log = tk.Text(frame, height=10, state="disabled")
    log.grid(row=6, column=0, columnspan=4, sticky="nsew")
    frame.rowconfigure(6, weight=1)

    def press(event):
        codes = state.press(event.keysym, time.monotonic())
        worker.submit(lambda: worker.send(codes))
        return "break"

    def release(event):
        state.release(event.keysym, time.monotonic())
        return "break"

    def focus_out(_):
        codes = state.release_all()
        worker.submit(lambda: worker.send(codes))

    pane.bind("<Button-1>", lambda _: pane.focus_set())
    pane.bind("<KeyPress>", press)
    pane.bind("<KeyRelease>", release)
    pane.bind("<FocusOut>", focus_out)
    pane.bind("<FocusIn>", lambda _: pane.config(background="#2d4a2d"))
    pane.bind("<FocusOut>", lambda _: pane.config(background="#20242a"), add="+")

    def tick():
        codes = state.flush(time.monotonic())
        if codes:
            worker.submit(lambda: worker.send(codes))
        while not events.empty():
            kind, text = events.get()
            if kind == "keyboard":
                status_var.set(text)
            log.config(state="normal")
            log.insert("end", ("error: " if kind == "error" else "") + text + "\n")
            log.see("end")
            log.config(state="disabled")
        root.after(FLUSH_MS, tick)

    board_var.trace_add("write", on_board)
    on_board()
    tick()
    try:
        root.mainloop()
    finally:
        worker.close_keyboard()
    return 0
