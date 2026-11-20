# nand2doom host UART

A small native Windows or POSIX Python CLI and client for the N2D1 debug
endpoint: status, halt, bounded memory inspection and modification, image
loading, entry selection, explicit resume and Set-2 input. The
[wire protocol](../../rtl/io/spec.md#version-1-wire-protocol), its
[bulk extension](../../rtl/io/spec.md#bulk-wire-extension) and
[streaming session](../../rtl/io/spec.md#streaming-load-session), and
the [image/asset contract](../../sw/spec.md) are
authoritative. Every argument and manifest byte is validated before opening a
port; help and dry run need neither pyserial nor hardware; live use names a port
explicitly and verifies N2D1 identity before any mutation (the operator
identifies the loaded image and holds exclusive bench ownership; nothing is
discovered or negotiated by sending packets). No GDB, GUI, driver install, D2XX,
asset download, programming, reset-line control or physical acceptance; native
Windows pyserial needs no WSL USB passthrough.

## Command line

`n2d_uart.py` keeps the legacy API of
the N2D1 and bulk codec (`src/nand2doom/scripts/n2d_uart_debug_host.py`)
(serial-pin DV consumers unchanged); `uart.json`
records baud, identity and windows. Python 3.10+; global options precede the
command; numbers are decimal or `0x`.

```text
python src/nand2doom/scripts/n2d_uart.py --dry-run ping
python src/nand2doom/scripts/n2d_uart.py --dry-run poke 0 0x12345678 --byte-enable 15
python src/nand2doom/scripts/n2d_uart.py --dry-run load workdir/software/n2d_validation/manifest.json --bulk --verify
python src/nand2doom/scripts/n2d_uart.py --dry-run dump 0x10000000 64000 --bulk --output workdir/frame.bin
python src/nand2doom/scripts/n2d_uart.py --baud de2_115 --dry-run load workdir/software/n2d_doom/manifest.json --stream
```

| Command | Contract |
| --- | --- |
| `ping` | N2D1 identity and acknowledged halt bit. |
| `caps` | Validate N2B2 identity, 4096-byte maximum, write/read flags. |
| `halt`, `resume` | Explicit; ordinary peek, poke and reset-PC never halt implicitly. |
| `reset-pc ADDRESS` | Aligned SDRAM entry; CPU already maintained/halted. |
| `peek ADDRESS COUNT` | 1..8 aligned words in one debug window, printed hex. |
| `poke ADDRESS WORD... [--byte-enable MASK]` | 1..8 words, mask 1..15 (default 15), same windows. |
| `input EVENT` | One ten-bit `{extended, break, scan_code}` event; no halt needed. |
| `load MANIFEST [--verify] [--resume]` | Validate, Ping, Halt, acknowledged writes, optional chunk readback, reset-PC, optional Resume. |
| `load MANIFEST --bulk --verify [--resume]` | Capabilities, maintained Halt, bulk writes and full readback, verified masked tails. |
| `load MANIFEST --stream [--resume]` | [Streaming load](#streaming-load). |
| `dump ADDRESS LENGTH --bulk --output workdir/FILE` | Requires maintained Halt; exact aligned SDRAM/framebuffer bytes; a fresh `workdir/` file only after complete validation; no frame coherency, execution change or side-effecting MMIO. |

| Option | Contract |
| --- | --- |
| `--port` | Required live; no default, enumeration or trial writes. Board identity per the [FPGA contract](../../fpga/spec.md); a different protocol is never probed. |
| `--baud` | The image's link rate: integer or `uart.json` `board_baudrates` name (`de10_lite` 1,000,000; `de2_115` 3,000,000, the FT232R maximum); default `baudrate` 1 Mbaud. `n2d_capture.py` and `n2d_control.py` take the same option and pass it to the shared `open_serial`. |
| `--timeout` | 0.01..60 s (default 1) for request write plus response read. |
| `--budget` | Whole operation 1800 s default (finite, ≥ one timeout, ≤ 86400), from after open including the identity check. |
| `--plan`, `--dry-run` | Never import serial, open a port, write output or send; single-command plans show an illustrative sequence-zero frame (live commands send Ping first, so sequence one). |

Exit: 0 success; 2 argument or manifest failure; 3 dependency, transport,
device, readback or close failure. An interrupt closes the handle and sends no
recovery. After a failure an unacknowledged transaction's effect may be
uncertain: inspect and recover the endpoint, never blindly rerun a load.

## Transport and failure

Pyserial is a live-only dependency (3.5 on native Windows supported; never
installed by the tool). One owned handle: `--baud` 8-N-1, no flow control,
DTR/RTS false before open, nonblocking reads, bounded writes; no purge or reset
toggling; timeout properties never change mid-packet. Each request is exactly
one write; a partial write aborts (never finishing a frame across the FPGA's
short inter-byte deadline); then fragmented reads of exactly header and
payload against a monotonic deadline. Validated: delimiter/version, command OR
response bit, sequence, legal status, exact count, CRC, Ping identity and halt;
an error response has zero words; stale or malformed data aborts without
resynchronization or flushing; sequences wrap modulo 256 within a session (no
cross-invocation replay guard). Any failure poisons the client: no further
command or retry. The 65,536-clock Halt allowance is 2.62144 ms, within the 1 s
timeout; a Halt timeout may leave the CPU stopped with maintenance active, and
the [Halt contract](../../rtl/memory/spec.md#maintained-halt-bound)
authorizes no retry, reset, Resume or capture reuse.

**Bulk API.** `Client.capabilities()` sends the non-mutating query once and
requires N2B2/4096/flags 3; `Client.bulk_write(address, data)` and
`Client.bulk_read(address, length)` each perform one exchange after discovery,
word-aligned, 4..4096 bytes; malformed requests are rejected before writing;
failures poison with no fallback; device errors keep a completed-prefix count
but never return partial data. `Client.operation_deadline` (default `None`) is
an absolute value in the same monotonic domain; before each request the client
reserves its full timeout, and exhaustion sends nothing. The capture caller owns
its own 120 s budget and must keep normal (105 s) and cleanup deadlines apart.
`BulkError.completed` is `None` on uncertain completion; only a CRC-checked
device error supplies a prefix, never an accepted partial image. Capability
success does not prove the image. Models of 16 ms response latency at 1 Mbaud
count two write and two read exchanges per verified 8192 bytes and 17 reads per
64000 + 1024 snapshot (not physical rates).

## Manifest and loader

Schema 1, little-endian RV32IM/ilp32, the builder's map revision; `entry_address`
or `entry` and segment `file` or `path` (conflicting aliases and duplicate keys
fail); 1..256 nonempty segments with aligned starts, nonoverlapping ranges in
SDRAM, a complete aligned entry instruction inside a segment; partial final
words legal. Files resolve inside the manifest directory by portable relative
paths (drive paths and backslashes rejected everywhere) with exact lengths and
SHA-256, held as immutable snapshots before open; storage bounded by SDRAM.
Engine/game-data metadata, a `.wad` segment or the reserved high game-data
window triggers Doom validation: engine and Freedoom metadata must exactly
match the pinned `src/nand2doom/scripts/n2d_doom.py`
(URLs, versions, archive digests, licenses) and the WAD its exact address,
length and SHA-256 (declared provenance, not authentication; nothing fetched).

Legacy load: identity Ping, maintained Halt, full words in ≤ 8-word LOAD
packets, a 1..3-byte tail as one POKE with only low lanes (adjacent bytes
preserved), each packet completing before the next; `--verify` peeks ≤ 8 words
after each ≤ 32-byte chunk comparing exactly the segment bytes; reset-PC only
after all segments; `--resume` explicit. The plan prints bytes, acknowledgement
count and a wire lower bound; progress prints each segment and about every
64 KiB with rate and ETA. Not atomic: acknowledged packets stay after failure,
which reports acknowledged bytes with no cleanup, reset or resume. About 29 MB
needs about 900,000 packets: ≥ 450 s wire, plus about 30 min per 2 ms added
round trip. Bulk load keeps snapshots, Halt, all-byte readback, reset-PC and
explicit Resume, with masked legacy Poke and exact Peek for sub-word tails.

## Streaming load

`load MANIFEST --stream [--resume]`: identity Ping, maintained Halt, stream
Hello (identity `N2S1`), all bytes as windowed Write frames, one chained
stored-SDRAM Digest per segment (compared with the snapshot before reset-PC; the
verification, so not combinable with `--bulk` or `--verify`), End, reset-PC,
optional Resume. Frames ≤ 1024 bytes at 1024-byte offsets per segment; a final
partial word is a short last frame (no masked Poke).

| Rule | Contract |
| --- | --- |
| Window | `StreamSession` keeps up to eight frames in flight; each carries its absolute address (resends idempotent) and a new sequence per transmission; replies arrive in order, so a reply for a later frame marks earlier unanswered frames lost. |
| Resend | Lost frames, replies checksum, memory, timeout, grant or busy, and frames unanswered after the window timeout (max(0.2 s, three window wire times) + 50 ms) are resent, each ≤ 8 times; Hello ≤ 3 times; a Digest waits with a length-scaled timeout; stale replies are ignored; parsing resynchronizes on `0x7a` and CRC-8. |
| Stop | Any other status, unexpected reply kind, wrong identity, digest mismatch, short write, exhausted retries or budget raise `StreamError`, poison the client and send no reset-PC or Resume (the endpoint stays halted). |
| Interruption | No reset needed: the device drops a partial frame after its 40 ms gap and closes the session after 2 s of silence; inside a session legacy Ping/Halt are stream data, so wait ≥ 2 s before the next invocation. |
| Plan | Frame count and wire time at the rate: the pinned Doom manifest (29,172,220 bytes, 28,490 frames) is 98.9 s at 3 Mbaud, 296.6 s at 1 Mbaud, plus device digest scans, no per-frame turnaround. |

## Tests

```text
python -B -m unittest discover -s src/nand2doom/scripts/tests -p test_n2d_uart.py -v
python -B -m unittest discover -s src/nand2doom/scripts/tests -p test_n2d_uart_stream.py -v
python tools/build/build.py test n2d_uart_debug --verilator VERILATOR_PATH --seed 33
python tools/build/build.py test n2d_uart_debug_memory --verilator VERILATOR_PATH --seed 31
python tools/build/build.py test n2d_uart_stream --verilator VERILATOR_PATH --seed 125
```

| Test | Coverage |
| --- | --- |
| `test_n2d_uart.py` | Fragmenting transport and fake memory: every command, identity gating, sequence wrap, CRC/header/count/status failures, deadlines, short writes and reads, no retries, handle cleanup, pre-open rejection, both manifest formats, Doom metadata and asset validation, multiple segments, all tail lengths, sentinels, explicit resume, failed-load stop; help and dry run without site packages. |
| `test_n2d_uart_stream.py` | Independent in-order device model with fragmented reads and fake clock: frame alignment and coverage, command order, a silently dropped frame, checksum reply, lost reply, reply noise, memory status, lost final reply (window timeout), protocol rejection, retry bound, digest mismatch, wrong identity, malformed frames and replies, chained digest identity, the Doom wire-time model, dry-run plan, live CLI through an injected opener, `--baud` parsing and delivery to `n2d_control` and `n2d_capture`. |

The Verilator targets keep the serial-pin [DV contract](../../dv/io/plan.md);
mocks and Verilator are not physical Windows/USB/FPGA acceptance.
Provenance: the bulk design inspected (read-only, nothing copied) the owner's
Linux baseline `tools/fpga/n2l_uart_load.py` at
`5898f868902d75baf38c55d24e67b0800533dbce` (different framing, CRC/echo, baud
negotiation, compression and retry; N2D2 also protects its header, keeps fixed
baud and never retries uncertain commands), and the transport patterns of
`tools/uart_ctrl/uart_ctrl.py`, `d2xx_transport.py` and nand2mario
`tools/n2m/host/transport.py` (protocols, reset/purge/discovery and D2XX not
adopted). No runtime reference-repository dependency.
