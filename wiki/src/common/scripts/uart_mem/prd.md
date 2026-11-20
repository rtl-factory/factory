# UART memory host: requirements

## Problem

A design on a board needs its program image in memory before its CPU starts, and a board test needs to read memory (a framebuffer, a result word) while the CPU runs. The `common` UART memory endpoint gives every design that access over the board's UART; the host side must load an image so that the CPU never starts on a partial or unverified image, and must fail loudly instead of retrying, because a silently repeated write or read corrupts what the test sees.

## Requirements

1. Speak the endpoint's request and response framing (start bytes, sequence number, command, CRC-8 header check, CRC-32 payload check) and reject any malformed, out-of-sequence or failed response.
2. Identify the endpoint first, and refuse an image built for another project.
3. Load as one transaction: hold the CPU once, write every segment and read it back, set the entry state only after every segment has verified, release the CPU once. A failure before the last verify leaves the CPU held.
4. Read a memory window while the CPU runs.
5. Poison the session at the first failure; never retry a command.
6. Separate the project's own UART traffic (program prints) that arrives before a response from the response itself.
7. Bound every command by a deadline.
8. Depend on no serial library: any object that can write bytes and read them before a deadline is a transport, so tests run against a fake endpoint.

## Out of scope

Opening the serial port, programming the board and choosing the image: the project's board flow owns those.
