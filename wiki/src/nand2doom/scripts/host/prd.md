# Host: requirements

## Problem

nand2doom runs Doom on a board whose only links to the host are the UART debug endpoint and the USB-Blaster's JTAG keyboard. An operator must be able to load a 29 MB image, start it, play it with held keys, and capture what it shows, often from a native Windows Python with no extra drivers, and without ever turning a lost byte or a busy cable into a corrupted image, a stuck key or a capture nobody can trust. Every mutation of the board is the operator's explicit choice: nothing is discovered, retried blindly or reset behind their back.

## Requirements

1. Speak the N2D1 debug protocol and its bulk and streaming extensions exactly as the I/O block defines them: check identity before any mutation, validate every reply (delimiter, version, sequence, status, length, CRC), and poison the session on the first uncertain reply, with no blind retry or resynchronization outside the streaming window.
2. Validate every argument and every manifest byte (schema, segments, lengths, SHA-256, the pinned Doom engine and game-data identity) before a port opens; a help or dry run needs neither pyserial nor hardware and writes nothing.
3. Load an image by acknowledged writes, bulk writes with full readback, or a windowed stream with per-segment stored digests; set the entry and resume only on explicit request, and never after a failed load.
4. Bound every transaction and every whole operation by a deadline; on failure or interrupt close the handle once, send no recovery, and print what the operator must do next.
5. Play Doom on the DE2-115 with one command: refuse when a port or the JTAG cable is already in use, check the cable and chain, program the image, stream-load only segments whose stored digest differs, start it and open the controls.
6. Deliver held host keys to Doom as make on press and break on release, over UART or JTAG, from a window or a terminal; release every held key on focus loss, close, failure or interrupt; never drop a break.
7. Send Doom actions, raw Set-2 events or typed text over the JTAG keyboard only with explicit confirmation and an exact cable; resolve the device name by the board's IDCODE when none is given, and refuse zero or several matches before any transfer.
8. Capture one completed gameplay framebuffer and its displayed palette coherently through firmware cooperation, within a fixed whole-operation bound that keeps the firmware's clock samples continuous; write success artifacts with full provenance only after the complete release handshake, and failure records otherwise.
9. Decode captured framebuffer and palette bytes offline into exact PNGs and a sampled GIF, deterministic within one Pillow environment, with no hardware, network or firmware access.
10. Write output only to a fresh file or directory below the checkout's scratch area; reject absolute paths, traversal and links.

## Out of scope

- Building the images: [sw_build](../sw_build/README.md) does that.
- Building the board image with Quartus: [board](../board/README.md) does that; only the play command programs a board, with an image already built.
- Port discovery, driver installation, reset-line control and physical acceptance: the operator owns the bench.
- The JTAG transport itself: the `common` [JTAG keyboard host](../../../common/scripts/jtag/README.md).

## Acceptance

The scripts' tests check every requirement without hardware; the simulation targets of the [io DV plan](../../dv/io/dv-plan.md) check the device side of requirements 1 and 3. Physical acceptance (programming to Doom on VGA, captures from a board) is a board run by the operator.

| Requirement | Check |
|---|---|
| 1 | UART tests on a fragmenting transport: every command, identity gating, sequence wrap, CRC, header, count and status failures, no retries, poisoning |
| 2 | Pre-open rejection, both manifest formats, Doom metadata and asset validation; help and dry run without site packages for the UART, controls, JTAG keyboard, capture and decoder commands |
| 3 | All tail lengths, readback, explicit resume; the stream tests against an independent device model (dropped frame, lost reply, noise, retry bound, digest mismatch, wrong identity) |
| 4 | Deadline, short write and read, handle cleanup, failed-load stop; play tests: Ctrl-C ends the stream session without reset-PC or Resume |
| 5 | Play tests with fake `jtagconfig`, `quartus_pgm` and the process table: holder refusal, exact programmer command, step order, digest skip and fallback, `--skip-program`, `--skip-load` |
| 6 | Controls tests: ownership, repeats, combined holds, Release All, focus loss, bounded queue, close and failure latching; terminal parser tests; JTAG delivery through the common session |
| 7 | JTAG keyboard tests: translation of every event and action, dry run opens nothing, resolution from both listing forms, zero and ambiguous matches, refusal before opening |
| 8 | Capture tests running the real client on a byte transport: exact addresses and counts, foreign state, no sample progress, header changes, poison without cleanup, deadline release |
| 9 | Decoder tests: channels and endianness, boundaries, high bits, readback of PNG and GIF, deterministic bytes, size limits, missing Pillow |
| 10 | Output confinement tests of the capture, decoder and UART `dump` commands |
