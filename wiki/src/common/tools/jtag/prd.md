# JTAG keyboard host: requirements

## Problem

A design under test on a board often needs keyboard input: a console to type into, a game to drive, a menu to step through. A captain at the board can plug in a keyboard, but a crewmate or a scripted board test cannot, and a board host may sit in another room. The `common` JTAG keyboard block gives every design a keyboard input over the board's existing USB-Blaster cable; the host side must deliver key presses to it exactly once and in order, or fail loudly, because a dropped or doubled key silently corrupts what the test sees.

## Requirements

1. Translate text and terminal key sequences (letters, digits, punctuation, Shift and Ctrl combinations, arrows, Home, End, Page Up and Down, Insert, Delete) into PS/2 Set-2 make and break codes, and refuse any byte with no key.
2. Deliver scan codes to the JTAG keyboard endpoint in order, with each code accepted exactly once: resend only the codes the endpoint reports it refused, and never resend a batch whose outcome is unknown.
3. Detect a reset of the endpoint, or a second host writing to it, between two batches, and stop instead of carrying on from a stale baseline.
4. Stop the whole session at the first uncertain outcome (a malformed status, a sequence or count mismatch, a timeout) and require an explicit recovery; never retry silently.
5. Type a key as a press, a hold long enough for a polling design to see it, and a release.
6. Open the board's JTAG connection only from explicit settings: an exact cable name, an exact device name and a clock. Never scan for cables or devices, never program the board, and treat missing settings as "no keyboard" rather than guessing.
7. Hold the board's JTAG connection for the whole session, and release it on close or on any failure, leaving no Quartus process behind, on Linux and on Windows Quartus driven from WSL.
8. Bound every step by a deadline: opening the connection, each scan, each batch and the whole delivery.
9. Be usable as a library by any project's board flow, with no dependency on the rest of the repository.

## Out of scope

- Programming the board, building the image, or choosing which board is attached: the board flow of each project owns those.
- Keyboard layouts other than US Set-2, and key codes the endpoint does not carry (Set-1, Set-3, mouse).
- Reading anything back from the design other than the endpoint's status.

## Acceptance

Every requirement except 7 on Windows is checked without hardware by the tool's own tests; the endpoint's side of the format is checked by the JTAG keyboard block's simulation test.

| Requirement | Check |
|---|---|
| 1 | Translation test: modifiers, extended keys and the make and break split of each key; an unsupported byte is refused |
| 2 | Delivery test: a partially accepted batch resends only the refused suffix; the wire peer sees each code once |
| 3 | A status change between batches stops the session before anything is sent |
| 4 | Malformed, inconsistent or late statuses poison the session; no later send is attempted |
| 5 | Typing test: each key's release follows its press after the hold |
| 6 | Validating settings starts no process and opens no device; missing or partial settings fail |
| 7 | Real Tcl process test: the connection is opened once, used for many scans and closed with confirmation; errors and end of input close it. Windows: the native worker is the only path from WSL |
| 8 | Separate start-up and per-operation deadlines both fire |
| 9 | The tool imports nothing from the repository |
