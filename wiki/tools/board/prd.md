# Board console: requirements

## Problem

Each project grew its own board host flow: nand2doom programs, loads and opens Doom controls with one command on the DE2-115, and nand2mario has program, load and joypad commands but no live keyboard. They program with the same vendor command and send keys through the same common JTAG keyboard, but find the cable and the device differently, and a captain has to learn a new set of commands per project. A captain wants one simple console, with as few controls as possible, that works for any program on either board.

## Requirements

1. Work for every project that declares its boards, on the DE10-Lite and the DE2-115, without knowing any design: a new project is supported by adding its board declaration, not by changing the console.
2. Select a SOF, defaulting to the selected board's built image; choosing a SOF whose name belongs to one board selects that board.
3. Program the FPGA through the one cable whose chain is exactly the selected board's FPGA, and refuse when there is none, more than one, or the wrong board.
4. Load a program over the board UART with the project's own loader, at the board's declared rate, showing the loader's output.
5. Run in a terminal like a small GUI (drop-down lists, buttons, status lights), so it works on any host and over SSH and an agent can drive it headless. Guide the user to a running program in plain steps, with one obvious next action and a plain fix when a step fails. In play, forward every key press and release to the board's JTAG keyboard, one make and one break per held key, never leaving a key held, and show the project's controller, lit by the keys and clickable.
6. Find the board's JTAG device name by its IDCODE, so nobody types it.
7. Offer the same four actions on a command line, where nothing touches the board without an explicit confirmation and the plan is printed otherwise.
8. Share the JTAG cable, device and programming helpers with the project scripts, so there is one copy.

## Out of scope

- Building SOFs or programs.
- A serial terminal for program output.
- Loading over JTAG (issue #128) and one common UART load protocol (issue #129): the console takes the loader each project declares.

## Acceptance

| Requirement | Check |
|---|---|
| 1 | Both projects' boards load from their board declarations (unit test) |
| 2 | The default SOF is the board's; a known SOF name selects its board (unit test) |
| 3 | Programming runs `jtagconfig` then `quartus_pgm` on the matching cable, and refuses a wrong chain (unit test with a fake runner) |
| 4 | Loading runs the declared loader with every field filled, from the repository root, and fails on a nonzero exit (unit test) |
| 5 | Kitty, plain and mixed terminal input give one make and one break per key and taps never stick (unit test); play through a pseudo-terminal delivers keys and pad clicks and lights the pad (unit test); the console's steps, failures with fixes and next-step button run under Textual's pilot against a fake board (unit test) |
| 6 | Text typed from the command line reaches a wire-level fake device through the device found by IDCODE (unit test) |
| 7 | Without `--yes` no runner or loader is started (unit test) |
| 8 | nand2doom and nand2mario import the shared helpers; their tests pass |
