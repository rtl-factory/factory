# Host: spec

How the host scripts meet their [requirements](prd.md). Code: `src/nand2doom/scripts/host/`; tests in its `tests/`. Each section below was its own spec in nand2anything; they came over with the design (issue #78) and were merged into this one page when the scripts were grouped (issue #81). Simulation evidence for the device side is in the [io DV plan](../../dv/io/dv-plan.md); none of these tests is physical board evidence.

## Parts

| Part | Role | Section |
|---|---|---|
| `n2d_uart.py` | The UART command (`ping`, `caps`, `halt`, `resume`, `reset-pc`, `peek`, `poke`, `input`, `load`, `dump`) and the `Client`, bulk and `StreamSession` API the other parts use | [UART host](#uart-host) |
| `n2d_uart_debug_host.py` | Pure N2D1, bulk and stream wire codec (frames, CRCs, decoders, wire-time model), also imported by the simulation benches | [UART host](#uart-host) |
| `n2d_play.py` | One command from programming the DE2-115 or the DE10-Lite to playing Doom | [Play](#play) |
| `n2d_control.py`, `n2d_control_terminal.py` | Real-time Doom controls in a Tk window or a terminal, over UART or JTAG | [Local controls](#local-controls) |
| `n2d_jtag_keyboard.py` | Doom actions, raw Set-2 events or text over the JTAG keyboard | [JTAG keyboard](#jtag-keyboard) |
| `n2d_capture.py` | One bounded, coherent framebuffer and palette snapshot | [Coherent frame capture](#coherent-frame-capture) |
| `n2d_frame_decode.py` | Offline decode of snapshots to PNG and GIF | [Offline frame decoder](#offline-frame-decoder) |

Every part imports the others as `src.nand2doom.scripts.host.<part>`, and each entry file puts the checkout root on `sys.path` before those imports, so it runs as a file from any directory. `n2d_uart.py` takes the manifest constants (architecture, ABI, memory-map revision) from [`n2d_image.py`](../sw_build/spec.md#validation-image) and the Doom pins from [`n2d_doom.py`](../sw_build/spec.md#doom-build); `n2d_capture.py` takes the Doom build's output confinement. The JTAG parts use the `common` [JTAG keyboard host](../../../common/scripts/jtag/spec.md), and find the cable and the device name, and program, with the board console's [JTAG chain helpers](../../../../tools/board/spec.md#jtag-chain) (`tools/board/jtag_chain.py`).

## UART host

A small native Windows or POSIX Python CLI and client for the N2D1 debug
endpoint: status, halt, bounded memory inspection and modification, image
loading, entry selection, explicit resume and Set-2 input. The
[wire protocol](../../rtl/io/mas.md#version-1-wire-protocol), its
[bulk extension](../../rtl/io/mas.md#bulk-wire-extension) and
[streaming session](../../rtl/io/mas.md#streaming-load-session), and
the [image/asset contract](../../sw/doom/spec.md) are
authoritative. Every argument and manifest byte is validated before opening a
port; help and dry run need neither pyserial nor hardware; live use names a port
explicitly and verifies N2D1 identity before any mutation (the operator
identifies the loaded image and holds exclusive bench ownership; nothing is
discovered or negotiated by sending packets). No GDB, GUI, driver install, D2XX,
asset download, programming, reset-line control or physical acceptance; native
Windows pyserial needs no WSL USB passthrough.

### UART command line

`n2d_uart.py` keeps the legacy API of
the N2D1 and bulk codec (`src/nand2doom/scripts/host/n2d_uart_debug_host.py`)
(serial-pin DV consumers unchanged); `uart.json`
records baud, identity and windows. Python 3.10+; global options precede the
command; numbers are decimal or `0x`.

```text
python src/nand2doom/scripts/host/n2d_uart.py --dry-run ping
python src/nand2doom/scripts/host/n2d_uart.py --dry-run poke 0 0x12345678 --byte-enable 15
python src/nand2doom/scripts/host/n2d_uart.py --dry-run load workdir/software/n2d_validation/manifest.json --bulk --verify
python src/nand2doom/scripts/host/n2d_uart.py --dry-run dump 0x10000000 64000 --bulk --output workdir/frame.bin
python src/nand2doom/scripts/host/n2d_uart.py --baud de2_115 --dry-run load workdir/software/n2d_doom/manifest.json --stream
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
| `--port` | Required live; no default, enumeration or trial writes. Board identity per the [FPGA contract](../../fpga/de10_lite/board-spec.md); a different protocol is never probed. |
| `--baud` | The image's link rate: integer or `uart.json` `board_baudrates` name (`de10_lite` 1,000,000; `de2_115` 3,000,000, the FT232R maximum); default `baudrate` 1 Mbaud. `n2d_capture.py` and `n2d_control.py` take the same option and pass it to the shared `open_serial`. |
| `--timeout` | 0.01..60 s (default 1) for request write plus response read. |
| `--budget` | Whole operation 1800 s default (finite, ≥ one timeout, ≤ 86400), from after open including the identity check. |
| `--plan`, `--dry-run` | Never import serial, open a port, write output or send; single-command plans show an illustrative sequence-zero frame (live commands send Ping first, so sequence one). |

Exit: 0 success; 2 argument or manifest failure; 3 dependency, transport,
device, readback or close failure. An interrupt closes the handle and sends no
recovery. After a failure an unacknowledged transaction's effect may be
uncertain: inspect and recover the endpoint, never blindly rerun a load.

### Transport and failure

Pyserial is a live-only dependency (3.5 on native Windows supported; never
installed by the script). One owned handle: `--baud` 8-N-1, no flow control,
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
the [Halt contract](../../rtl/memory/mas.md#maintained-halt-bound)
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

### Manifest and loader

Schema 1, little-endian RV32IM/ilp32, the builder's map revision; `entry_address`
or `entry` and segment `file` or `path` (conflicting aliases and duplicate keys
fail); 1..256 nonempty segments with aligned starts, nonoverlapping ranges in
SDRAM, a complete aligned entry instruction inside a segment; partial final
words legal. Files resolve inside the manifest directory by portable relative
paths (drive paths and backslashes rejected everywhere) with exact lengths and
SHA-256, held as immutable snapshots before open; storage bounded by SDRAM.
Engine/game-data metadata, a `.wad` segment or the reserved high game-data
window triggers Doom validation: engine and Freedoom metadata must exactly
match the pinned `src/nand2doom/scripts/sw_build/n2d_doom.py`
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

### Streaming load

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

### UART tests

```text
python3 -m pytest src/nand2doom/scripts/host/tests/test_n2d_uart.py src/nand2doom/scripts/host/tests/test_n2d_uart_bulk.py
python3 -m pytest src/nand2doom/scripts/host/tests/test_n2d_uart_stream.py
python3 tools/sim/sim.py test n2d_uart_debug --seed 33
python3 tools/sim/sim.py test n2d_uart_debug_memory --seed 31
python3 tools/sim/sim.py test n2d_uart_stream --seed 125
```

| Test | Coverage |
| --- | --- |
| `test_n2d_uart.py` | Fragmenting transport and fake memory: every command, identity gating, sequence wrap, CRC/header/count/status failures, deadlines, short writes and reads, no retries, handle cleanup, pre-open rejection, both manifest formats, Doom metadata and asset validation, multiple segments, all tail lengths, sentinels, explicit resume, failed-load stop; help and dry run without site packages. |
| `test_n2d_uart_stream.py` | Independent in-order device model with fragmented reads and fake clock: frame alignment and coverage, command order, a silently dropped frame, checksum reply, lost reply, reply noise, memory status, lost final reply (window timeout), protocol rejection, retry bound, digest mismatch, wrong identity, malformed frames and replies, chained digest identity, the Doom wire-time model, dry-run plan, live CLI through an injected opener, `--baud` parsing and delivery to `n2d_control` and `n2d_capture`. |

The Verilator targets keep the serial-pin [DV contract](../../dv/io/dv-plan.md);
mocks and Verilator are not physical Windows/USB/FPGA acceptance.
Provenance: the bulk design inspected (read-only, nothing copied) the owner's
Linux baseline (paths in nand2anything) `tools/fpga/n2l_uart_load.py` at
`5898f868902d75baf38c55d24e67b0800533dbce` (different framing, CRC/echo, baud
negotiation, compression and retry; N2D2 also protects its header, keeps fixed
baud and never retries uncertain commands), and the transport patterns of
`tools/uart_ctrl/uart_ctrl.py`, `d2xx_transport.py` and nand2mario
`tools/n2m/host/transport.py` (protocols, reset/purge/discovery and D2XX not
adopted). No runtime reference-repository dependency.

## Play

One command for the owner to play Doom on the DE2-115 (or the DE10-Lite): check the board,
program its image, stream-load Doom over the 3 Mbaud UART, start it, and
open real-time controls over UART or the USB-Blaster JTAG keyboard. A reload
after a software change takes seconds, not the full 29 MB. One explicit
invocation authorizes programming, loading and playing the board it names; it
never discovers ports, refuses busy devices and kills nothing. It reuses the
[UART client](#uart-host), [local controls](#local-controls)
and [JTAG library](../../../common/scripts/jtag/spec.md). Physical acceptance is one invocation from
programming to Doom on VGA with webcam evidence of the player turning and
moving under held keys over JTAG and over UART, plus the measured load time;
only the operator holding the board runs it.

### Play command line

`n2d_play.py`, Python 3.10+; live use needs pyserial, `jtagconfig` and
`quartus_pgm` (`--quartus-bin` or `PATH`), and `quartus_stp` for JTAG controls.

```text
python3 src/nand2doom/scripts/host/n2d_play.py --port PORT --plan
python3 src/nand2doom/scripts/host/n2d_play.py --port PORT
python3 src/nand2doom/scripts/host/n2d_play.py --port PORT --transport jtag
python3 src/nand2doom/scripts/host/n2d_play.py --port PORT --skip-program
python3 src/nand2doom/scripts/host/n2d_play.py --port PORT --skip-load --controls terminal
```

| Option | Meaning |
| --- | --- |
| `--port` | UART port; default `$N2D_UART_PORT`; required unless `--skip-load` with JTAG controls. |
| `--board` | `de2_115` (default; IDCODE `020F70DD`, baud 3,000,000) or `de10_lite` (IDCODE `031050DD`, baud 1,000,000). |
| `--sof` | Default the newest `workdir/quartus/n2d_<board>/output_files/n2d_<board>.sof` in this or the main checkout. |
| `--manifest` | Default `workdir/software/n2d_doom/manifest.json` of this checkout. |
| `--skip-program` | Keep the FPGA image; Halt, reload, Resume. |
| `--skip-load` | Ping only and reconnect controls to a running game; implies `--skip-program`. |
| `--force-full` | Load every segment. |
| `--controls` | `auto` (Tk, terminal without Tk), `gui`, `terminal`, `none` (print the controls command). |
| `--transport` | `uart` (default) or `jtag`. |
| `--jtag-device` | Exact `quartus_stp` name; default resolved by IDCODE at the board check. |
| `--timeout`, `--budget` | UART transaction 0.01..1 s; whole load (default 900 s). |
| `--plan`, `--dry-run` | Steps, inputs and modelled wire times; opens nothing. |

Exit: 2 argument error before any device action; 3 device or Quartus failure; 4
refusal for a busy device; 130 interrupt; otherwise the controls' status.

### Play steps

Each prints `[i/N] name ...` and `ok in S s`; the total time to a running game
prints before the controls open.

1. **Exclusive use.** Any `/proc/PID/fd` entry matching the port's real path,
   or (when programming or using JTAG controls) any `quartus_pgm`,
   `quartus_stp`, `quartus_stpw`, `system-console`, `nios2-terminal`,
   `jtagconfig` or `quartus_jli` process refuses with PID and name (`jtagd` is
   the shared server, not a holder).
2. **Board check.** One `jtagconfig` (30 s) must list exactly one cable whose
   chain is exactly the board IDCODE (skipped with `--skip-program` and UART
   controls). With JTAG controls and no `--jtag-device` the device name is
   [resolved by IDCODE](#device-resolution) on that
   cable (zero or several matches stop before programming); the detail prints
   it.
3. **Program.** `quartus_pgm -c CABLE -m jtag -o p;SOF@1` (180 s) must exit 0
   and report success; SDRAM is not preserved.
4. **Ping and Halt.** Opens the port at the board rate; the identity Ping is
   tried up to three times on fresh clients, discarding input between tries (a
   fresh FPGA can emit a stray byte); Halt follows in the load. `--skip-load`
   requires the CPU running and sends nothing else.
5. **Stream load** below.
6. **Reset PC and Resume** at the manifest entry.
7. **Controls.** The UART handle closes, then
   [`n2d_control`](#local-controls) runs in process with `--connect`,
   the board rate and the transport.

### Stream load with digest skip

One [stream session](../../rtl/io/mas.md#streaming-load-session)
([streaming loader](#streaming-load)): Hello, then per
segment in address order:

| Segment | Rule |
| --- | --- |
| Program segment (holds the entry) | Always loaded (its data changes at run time). |
| Any, with `--force-full` | Loaded. |
| Others | A Digest frame asks the CRC-32 of the stored range with seed `0xFFFFFFFF`; equal to the manifest bytes' digest: **skipped**; different: loaded. A failed Digest (error or no reply) drains in-flight replies for one window timeout and loads. |

Loaded segments stream as `n2d_uart.py load --stream`, then their stored
digest must equal the manifest's (mismatch or failure stops the load); End must
report at least the frames sent. Each segment prints `skipped`, or `loading`
with the reason (program segment, `--force-full`, mismatch with both values,
failed request) then `loaded and verified` with seconds. A matching digest is
the same evidence as full verification; after reprogramming SDRAM refresh
stopped, so digests differ and segments reload. For the pinned image the
program segment is 377,144 bytes (1.3 s at 3 Mbaud) and the WAD 28,795,076
bytes (97.6 s).

### Play failure, interrupt and recovery

A load failure poisons the client, sends no reset-PC or Resume, leaves the CPU
halted and prints the recovery: wait 2 s (the device closes an abandoned stream
session), rerun with `--skip-program`; if Ping does not answer, rerun without
it. Ctrl-C or SIGTERM during the load stops sending, waits 50 ms (the device
drops a partial frame after a 40 ms gap), sends End with its bounded wait so the
link returns to legacy protocols, and prints whether End was confirmed with the
recovery command; the CPU stays halted and a partial image is never resumed. A
second interrupt abandons End (the device's 2 s idle bound applies). An
interrupt while programming leaves the configuration undefined (rerun). The
serial handle closes exactly once; interrupts in the controls release held
keys.

### Play tests

```text
python3 -m pytest src/nand2doom/scripts/host/tests/test_n2d_play.py
```

test_n2d_play.py (`src/nand2doom/scripts/host/tests/test_n2d_play.py`) runs
the loader against the independent stream device model: all non-program
segments matching (only program frames sent), all mismatching, one stale byte,
a Digest status error and a silent Digest falling back to a full load, a failed
program verification stopping halted, `--force-full` without pre-digests,
post-load corruption stopping halted, Ctrl-C ending the session without
reset-PC or Resume. Flow tests with fake `jtagconfig`/`quartus_pgm` runners and
openers: exact programmer command, step order, refusal without running Quartus,
`--skip-load`, `--skip-program`, programmer failure before the UART, the
controls argument vector with the resolved device, the `--jtag-device`
override skipping the listing, an unresolved device stopping before
programming; a fake `/proc` checks holder detection; the plan test proves no
opener or runner is called (with JTAG it prints the pending resolution or the
override). Unit tests are not physical evidence.

## Local controls

Plays an already running nand2doom image in real time: held host keys reach
Doom as make on key down and break on key up, combined actions work, and
nothing stays held after the window or terminal loses focus or closes. VGA
remains the display (no video streaming). The operator must own the bench
before Connect (a running-state Ping proves neither wiring, game identity nor
ownership); only the assigned operator runs live trials. The
[play command](#play) launches these controls after loading.
Framing, baud, deadlines and transports belong to the
[UART scripts](#uart-host) and [JTAG library](../../../common/scripts/jtag/spec.md). No
installation, network service, global keyboard hook, capture, framebuffer,
driver or RTL change.

### Controls command line

n2d_control.py (`src/nand2doom/scripts/host/n2d_control.py`), Python 3.10+
(native Windows with Tk 8.6 supported).

```text
python -B -S src/nand2doom/scripts/host/n2d_control.py --help
python -B -S src/nand2doom/scripts/host/n2d_control.py --plan
python -B -S src/nand2doom/scripts/host/n2d_control.py --smoke-test
python -B src/nand2doom/scripts/host/n2d_control.py --port COM7
python3 src/nand2doom/scripts/host/n2d_control.py --baud de2_115 --port PORT --connect
python3 src/nand2doom/scripts/host/n2d_control.py --transport jtag --cable 'USB-Blaster [1-1]' \
    --device '@1: EP4CE115 (0x020F70DD)' --terminal
```

| Option | Contract |
| --- | --- |
| `--transport uart` (default) or `jtag` | Selects the link. |
| `--port` / `--cable --device [--quartus-bin]` | Prepopulate the link field only; nothing opens until Connect. The JTAG device is the exact `quartus_stp` name (install-dependent; `n2d_play` passes the one it [resolved by IDCODE](#device-resolution)). |
| `--baud` | Image link rate or board name (`de2_115` = 3,000,000). |
| `--timeout` | 0.01..1 s per UART transaction, default 1. |
| `--connect` | Connect at start (otherwise a human Connect click). |
| `--plan`, `--dry-run`, `--help` | JSON bindings, transport and policy without importing Tk, serial or Quartus, creating output or opening handles. |
| `--smoke-test` | Opens a disconnected window for 300 ms with Connect disabled. |
| `--terminal` | [Terminal controls](#terminal-controls), also chosen when Python lacks Tk (other Tk failures exit 3); they connect at once, so link arguments are required. |

No default port, scan, reconnection, Halt, Resume, reset, memory operation or
capture. Missing pyserial or Quartus is an actionable error. Exit 2 arguments,
3 unavailable GUI; live errors stay visible in the window.

### Supported logical actions

The GUI binding policy; the firmware
[keyboard map](../../sw/doom/spec.md#doom-keyboard-map) maps every
make code; events use the [ten-bit Set-2 contract](../../rtl/io/mas.md#ps2-keyboard-receiver).
Break ORs 0x100, preserving extended bit 0x200.

| Action | Keyboard keys | Make | Break |
| --- | --- | ---: | ---: |
| Forward | Up, W | 0x275 | 0x375 |
| Backward | Down, S | 0x272 | 0x372 |
| Turn left | Left, A | 0x26b | 0x36b |
| Turn right | Right, D | 0x274 | 0x374 |
| Fire | Control left/right, F | 0x014 | 0x114 |
| Use | Space, E | 0x029 | 0x129 |
| Run | Shift left/right, R | 0x012 | 0x112 |
| Strafe modifier | Alt left/right, Q | 0x011 | 0x111 |
| Enter | Return, keypad Enter | 0x05a | 0x15a |
| Escape | Escape | 0x076 | 0x176 |
| Yes | Y | 0x035 | 0x135 |
| No | N | 0x031 | 0x131 |
| Map | Tab | 0x00d | 0x10d |
| Weapon 1..7 | 1..7 | 0x016, 0x01e, 0x026, 0x025, 0x02e, 0x036, 0x03d | make OR 0x100 |

WASD is forward/back/turn, not strafe (hold Strafe with a turn arrow). Keys
apply only in this application's focused window or terminal; the link field
never injects keys.

### Transports

| Link | Contract |
| --- | --- |
| UART | One `n2d_uart.Client` on one serial handle at `--baud`; each make or break is one CRC-checked `CMD_INPUT`. Connect requires a checked Ping with identity and halted=0. |
| JTAG | One persistent `quartus_stp` transport with exact names (fixed original USB-Blaster at 6 MHz, instance 0) and one Session whose construction checks endpoint status identity; each event becomes Set-2 bytes (`E0` extended, `F0` break) delivered by `send_all` within 3 s. Connect proves the keyboard endpoint, not that Doom runs. A poisoned Session latches; a close not reporting release is a fault. |

### Lifecycle and concurrency

One worker thread owns the Session and its one handle; Tk callbacks never wait
on the link. Connect (button or `--connect`) is asynchronous; input stays
disabled until the check completes; nothing mutates before it. A halted, wrong
or malformed endpoint closes without input or recovery. A healthy disconnect
allows a new Connect; failures latch the application (new invocation needed).

| Rule | Contract |
| --- | --- |
| Queue | Bounded FIFO of 32 operations (at most 32 queued plus one in flight before a new barrier); failure stops at the first uncertain command; overflow faults the session (a break is never dropped). |
| Ownership | Each press has a source (physical keycode or mouse) and action: the first source sends make, the last release sends break, repeats send nothing; auto-repeat release/press pairs cancel an idle-deferred release; physical keycodes keep Shift/case changes from stranding keys. |
| Release All, focus loss | Clear keyboard and mouse ownership through a queued barrier; held keys must be released before reactivating; mouse uses a local grab (release outside the button is seen); internal focus moves are not focus loss. |
| Close | Disconnect, close or Ctrl-C in the launching terminal set a stop flag, disable submissions and discard unexecuted input; an issued transaction may finish once; a stop before worker start never opens a transport; then a healthy Session releases at most one break per owned action (20 actions) and closes (worst case one command plus releases, each at the transaction bound: the UART timeout or 3 s per JTAG event); the UI stays responsive. Serial open/close run off the UI thread, never duplicated or forcibly retried. |
| Failure | Any command/open/close exception, overflow or poisoned Client latches failure, disables submissions, discards work and closes once; no break, Ping or retry follows; the UI warns that key state may be uncertain. An input ACK proves FIFO acceptance, not consumption. |
| Disposition | The worker publishes an immutable terminal result before exiting, which the UI consumes before discarding the worker; a missing result is a latched fault. Only the UI thread changes widgets, focus and grab. |

### Terminal controls

n2d_control_terminal.py (`src/nand2doom/scripts/host/n2d_control_terminal.py`)
connects the same Session, sets raw mode, enables focus reporting (`CSI ?1004
h`) and queries the [kitty keyboard protocol](https://sw.kovidgoyal.net/kitty/keyboard-protocol/)
(`CSI ? u` then primary device attributes, 0.5 s bound):

| Mode | Behavior |
| --- | --- |
| Press/release (kitty, foot, Ghostty, WezTerm, recent Alacritty) | Flags 1+2+8 pushed; every key including bare Shift, Ctrl, Alt reports press, repeat, release: press makes, release breaks, repeats send nothing. |
| Tap (other terminals, multiplexers that do not pass the protocol) | Make on the first byte, break 0.5 s later, extended 0.15 s per auto-repeat byte. Bare modifiers send nothing (use F, R, Q); no chords with Shift/Ctrl/Alt; holds shorter than the auto-repeat delay may stutter; a lone Escape is recognized after 50 ms. |

Focus-out releases every held key. F10 or Ctrl-C quits (in press/release mode
Ctrl-C is Ctrl+C after Ctrl's make); every exit, interrupts included, releases
held keys and closes once, then pops the flags, disables focus reporting and
restores the terminal. Failures latch with the uncertain-state warning. One
status line shows held actions.

### Controls tests

```text
python3 -m pytest src/nand2doom/scripts/host/tests/test_n2d_control.py
```

test_n2d_control.py (`src/nand2doom/scripts/host/tests/test_n2d_control.py`)
uses the real Client on a fake framed transport: exact make/break bytes,
identity and running gating, mixed ownership and repeats, combined holds,
Release All, poison/status/CRC/timeouts/short writes/close failures, no command
after failure; bounded backlog, in-flight close, dropped queued presses, one
handle; keycode repeat and deferred-release cancel, mouse release, focus and
close without hardware. JTAG tests drive the common Session and `send_all`
against the common wire peer: every action's make and break decoded from the
Set-2 bytes, release on close, uncertain delivery latching without release,
unconfirmed close, open failure. Terminal tests: kitty parser (press, repeat,
release, modifiers, arrows, focus, replies, split reads), tap parser,
detection, event-loop holds, focus-loss release, tap repeat extension, device
fault, interrupt still releasing. CLI tests: transport plan and terminal
dispatch. Native Tk smoke proves window construction only.

## JTAG keyboard

Plays an already running nand2doom image from the host keyboard over the
USB-Blaster, without a PS/2 keyboard: Doom actions, raw Set-2 events or typed
text reach the same input FIFO as the PS/2 connector and the UART input
command, through the shared [JTAG library](../../../common/scripts/jtag/spec.md) (explicit names,
one bounded `quartus_stp` session, exactly-once delivery resubmitting only a
proven refused suffix). Only the existing Set-2 format: the
[local control actions](#supported-logical-actions), raw
10-bit events and shared text translation; no new key mapping, discovery,
programming or UART fallback. Sending needs `--yes` and the bench owner's JTAG
access (shared with programming and other projects). Simulation evidence:
[io DV](../../dv/io/dv-plan.md#jtag-keyboard-input); physical evidence is
a board run.

### JTAG keyboard command line

`n2d_jtag_keyboard.py`
(Python 3.10+) imports only the action table from `n2d_control.py`, the
resolver `tools/board/jtag_chain.py`
and `src/common/scripts/jtag/n2a_jtag_keyboard.py`.

```text
python3 src/nand2doom/scripts/host/n2d_jtag_keyboard.py key "Turn left" Fire --hold 0.5
python3 src/nand2doom/scripts/host/n2d_jtag_keyboard.py key Forward --mode press
python3 src/nand2doom/scripts/host/n2d_jtag_keyboard.py send 0x275 0x375
python3 src/nand2doom/scripts/host/n2d_jtag_keyboard.py type idkfa
python3 src/nand2doom/scripts/host/n2d_jtag_keyboard.py --cable 'USB-Blaster [1-1]' --yes key Fire
python3 src/nand2doom/scripts/host/n2d_jtag_keyboard.py --cable 'USB-Blaster [1-1]' \
    --device '@1: EP4CE115 (0x020F70DD)' --yes key Fire
```

| Command | Contract |
| --- | --- |
| `key ACTION...` | Doom actions, case-insensitive, `-` and `_` as spaces. `--mode tap` (default) makes all in order, holds `--hold` (default 0.1 s), breaks in reverse; `press`/`release` send one half. |
| `send EVENT...` | Raw 10-bit `{extended, break, scan_code}` (0..1023, any Python base); scan code `0xE0` or `0xF0` refused (a prefix, not an event). |
| `type TEXT` | Shared `key_strokes`: per key make, `--hold` (default 0.05 s), break, `--gap` (default 0.05 s). |

Each event becomes `E0` if extended, `F0` if break, then the scan code. Without
`--yes`: JSON with each step's Set-2 bytes and batches of at most four, cable,
`jtag_device`, `jtag_device_source` and a `dry_run` note; no transport (the
read-only device listing is the only cable access). With `--yes`: `--cable`
required; cable and device validated by the shared `settings` (`--tck-hz`,
`--clock-mode`, `--cable-index`, `--quartus-bin` pass through); one transport
and `Session`, every step via `send_all` within `--seconds` (default 20) for
the whole request, then close.

### Device resolution

Installs name the DE2-115 (IDCODE `0x020F70DD`) differently (`@1: EP4CE115
(0x020F70DD)` or `@1: 10CL120(Y|Z)/EP3C120/.. (0x020F70DD)`). An explicit
`--device` is used verbatim (`jtag_device_source` `--device`). Otherwise, with
`--cable`, one `quartus_stp -t tools/board/jtag_devices.tcl CABLE` (30 s) prints one
`N2A_DEVICE ` line per `get_device_names` entry, and the name whose trailing
`(0xXXXXXXXX)` equals `0x020F70DD` is chosen (`IDCODE 0x020F70DD`). Zero or
several matches, nonzero exit or timeout refuse with the listed names before
any transport. The shared library still takes exact names.

### JTAG keyboard results

The JSON adds `sent`, `status` and `close`. Exit 0: every byte accepted and
closed. Exit 1: transfer error, expired bound (a FIFO that never drains) or
unconfirmed close, with `error` and no replay; key state may be uncertain.
Exit 2: refused before any device effect (unknown action, invalid event,
untranslatable text, missing or invalid settings, device not resolving to
exactly one match).

### JTAG keyboard tests

`src/nand2doom/scripts/host/tests/test_n2d_jtag_keyboard.py`: translation of every
non-prefix event and Doom action, chord order, batching, inert dry run and
`--help`, resolution from both listing forms, zero and ambiguous matches,
listing failure and timeout, the resolved name in the dry run, `--device`
skipping the listing, refusal before opening, delivery through the real shared
`Session` against the shared test's independent wire peer with refused
suffixes, uncertain delivery and the whole-request bound.

## Coherent frame capture

Captures one completed gameplay framebuffer and its matching RGB444 palette
through the existing N2D1 endpoint, for the sole authorized operator after the
exact capture-enabled Doom image is loaded. Capture pauses the game; VGA and
the hardware clock keep running. Firmware side:
[software HAS](../../sw/doom/spec.md#coherent-capture). Excluded:
discovery, image loading, CPU reset, programming, palette commits, input,
cameras, publication, VGA electrical proof and native frame rate; simulation is
never a physical capture.

| Requirement | |
| --- | --- |
| Data | Exactly 64,000 row-major 320x200 indices and 256 little-endian u32 RGB444 entries; partial data, boot/title/wipe frames, inactive palette readback and uninitialized firmware are failures. |
| Firmware | Proves the completed gameplay boundary and known displayed palette, lets a stable raster finish, parks until explicit release. |
| Transport | One [`n2d_uart.Client`](#uart-host) and serial handle (its codec, identity, limits, poisoning, no-retry policy, optional opener); maintained Halt for coherent access; no retries or speculative cleanup after uncertain failures. N2B2 capabilities on the same client after running Ping and before Halt; exactly 17 CRC32-validated bulk exchanges; unsupported endpoints fail without fallback; mailbox/control commands stay N2D1. |
| Bound | Whole capture including release acknowledgement ≤ 120 s. |
| Output | Fresh ignored `workdir/` artifacts and provenance; the [offline decoder](#offline-frame-decoder) owns PNG/GIF. |

### Capture command line

```text
python -B src/nand2doom/scripts/host/n2d_capture.py --help
python -B src/nand2doom/scripts/host/n2d_capture.py --dry-run --manifest workdir/software/n2d_doom/manifest.json --hardware-image workdir/bench/validated.sof --output workdir/captures/frame01
```

`n2d_capture.py` needs Python
3.10+ standard library and local modules (native Windows included), plus
pyserial only for a live run. Live adds `--port` (the identified OS port) and
`--clock-history-attested`, operator inputs, not discovery or authorization.
`--timeout` is 0.01..1 s per transaction (default 0.5). The plan validates
inputs and prints the mailbox, required N2B2 capabilities, 17 payload
transactions, block lengths and deadline, importing no serial and creating
nothing. The output is a fresh directory under this checkout's `workdir/` with
the Doom builder's confinement, also rejecting any existing non-directory
component from `workdir` through the output and symlinks at the root or nested;
failures return before any creation, write or serial open. Normal N2D1 segment
validation checks every binary/WAD byte before opening; capture also verifies
ABI/layout, recipe digest, full ELF digest and linked identity bytes. The
hardware image is only hashed (its loading is the operator's to establish); no
programmer runs. After a successful run, decode `indices.bin` and `palette.bin`
with `n2d_frame_decode.py`; the
[showcase recipe](https://github.com/amichai-bd/nand2anything/blob/aeebe92/wiki/showcase/doom/README.md#reconstructing-the-media)
documents the three-sample conversion and publication set. A poisoned session
or unknown clock history needs operator reconciliation before another run.

### Identity and exact addresses

The [Doom build](../sw_build/spec.md#doom-build) hashes owned firmware sources, builder
source, the adapted engine tree, pinned engine/WAD hashes, verified libc and
compiler version into 32 bytes compiled as `n2d_capture_identity`; after
linking it resolves unique symbols with build-time binutils, checks mailbox BSS
kind/size/alignment and identity bytes, and records mailbox and identity
addresses with the full ELF digest. Linker assertions keep the mailbox in
startup-cleared BSS outside heap, stack and WAD. The embedded identity describes
inputs; ELF and binary hashes are manifest records (no circular hash). This is
build provenance, not authentication or live-memory readback; runtime uses no
`nm`, compiler, WSL or address guess.

### Transaction order

1. Validate inputs and the attestation. Ping must show a running endpoint (an
   initially halted CPU has unknown halt age and is rejected without Resume).
   Negotiate N2B2, 4096-byte maximum and flags 3 on the same Client before the
   first Halt; unsupported capabilities fail with no second handle, fallback or
   mutation.
2. Maintained Halt; read and validate the 24-word header. An outstanding
   request, error, stale READY or exhausted sequence is rejected; an earlier
   snapshot is never adopted (the operator must release an outstanding request
   and observe acknowledgement first).
3. Resume, then short Halt/header/Resume polls observe a changed clock-service
   generation within 15 s of start; recheck IDLE, identity and accepted-sequence
   history; write only the request word `last+1` and verify it while halted.
4. Resume and poll until 30 s from start for READY with matching request,
   accepted and ready sequences, changed generation, known palette, no pending
   commit and no error (PREPARING or partial READY is not a snapshot).
5. Under continuous Halt, `Client.bulk_read` fifteen 4096-byte and one
   2560-byte framebuffer blocks, then one 1024-byte SDRAM palette-shadow block;
   recheck all header words; reject upper palette bits.
6. While halted write request = 0 and verify; Resume; poll for request 0,
   ready 0, IDLE and changed generation; Resume after that acknowledgement.
   Only this complete sequence permits success artifacts.

**Ownership.** Once any header shows `last == owned sequence`, every later
release header and acknowledgement poll must keep it; before a successful
snapshot's release write the exact captured header is reread; acknowledgement
must keep palette knowledge too. A same-image reset is invalid even with
matching magic, ABI and identity. Observed ownership loss (also in an
intermediate acknowledgement poll) disables cleanup: no release write or Resume
after that header. Before acceptance, cleanup may observe the prior sequence;
once acceptance is observed the new sequence is mandatory. Generation is a
wrapping u32: only inequality proves a sample (a wrap decrease is valid).
Replacement, reset or identity mismatch invalidates ownership (no cleanup
against an unknown image). The host never touches inactive palette MMIO or
sends palette commits.

### Clock bounds and failures

The firmware sample gap is strictly **< 171.79869184 s** at 25 MHz, and missed
counter periods cannot be reconstructed. Attestation means the operator
established no earlier loss, exclusive ownership and a sample age ≤ 10 s at
entry; success keeps that assumption.

| Bound | Value |
| --- | --- |
| Whole operation | 120 s monotonic from before Ping, never reset; 10 s entry age + 120 s stays below a period. |
| New commands | Must finish before 105 s; each reserves its whole timeout first; 15 s reserved for cleanup. |
| Polling halts | ≤ 5 s ordinary (far below the 85.89934592 s signed sleep half-period). |
| Generation | Must change before READY and after release; a Resume alone is never sample evidence. READY park services the clock outside `DG_SleepMs`. |

Capability and bulk calls use the same reservation and post-call checks; the
caller owns phase deadlines (no permanent 105 s `Client.operation_deadline`
blocking cleanup); Client and Capture share one monotonic clock. A block is
appended only after full Client validation and Capture's post-check;
`BulkError.completed` is diagnostic only. On a healthy local abort with known
ownership and reserve, the host releases, resumes, observes release and
sampling, and discards the capture. Firmware ERROR permits cleanup, never
acceptance. A poisoned Client sends nothing more; keyboard interrupt does no
speculative cleanup; a close failure cannot succeed. Failures write
`failure.json` and `.partial` raw files, never `capture.json`, marking
continuity unknown, or `lost-clock-continuity` when elapsed time plus the entry
bound reaches the period; any later unattended halt beyond it is lost
continuity too. Long successful captures advance game time.

### Capture artifacts

Framebuffer blocks read the back-bank window, which firmware makes equal to the
displayed frame before READY (completing the swap and copying the front bank
back). On half-width hardware (DE10-Lite) each stored even-x pixel is returned
for both columns, so `indices.bin` is already the pixel-doubled 320-wide image
and `horizontal_pairs_equal` is always true. Success writes `indices.bin`,
`palette.bin` and `capture.json`: raw hashes; source/recipe, ELF, firmware, WAD
and selected hardware-image hashes (which must identify the actual validated
bulk SOF); sequence, frame, video status, generations, event order and
timings, UTC time, dimensions, coherency method, conditional clock history,
limitations; bulk protocol, capabilities and block lengths. No images are
produced here.

### Capture tests

Host byte-transport tests run the production Client: exact addresses and
counts, fragmentation, initial and foreign state, no sample progress, wrong
READY, bad palette, header changes, poison without cleanup, healthy deadline
release. Firmware and public-port RTL coverage: [software DV](../../dv/software/dv-plan.md).
No mock or Verilator result certifies USB latency or Windows serial behavior.

## Offline frame decoder

Converts supplied framebuffer/palette snapshots into exact PNGs and a small
sampled GIF for inspection and documentation. It never acquires data, opens
serial, imports firmware or the [UART script](#uart-host), touches the
board, fetches game assets, edits a README or labels a decode as physical
acceptance. The caller owns active-palette identity, complete-frame
synchronization and SOF/firmware/input provenance (palette MMIO reads the
inactive bank; a CPU halt can freeze a partial frame); the
[capture script](#coherent-frame-capture) and showcase workflow own acquisition
and publication.

### Decoder format

`n2d_frame_decode.py` is a
pure API with a small CLI. The [video HAS](../../rtl/video/mas.md#framebuffer-and-palette)
owns the format: 320x200, one row-major index byte per pixel (exactly 64,000
bytes, no reserved tail), 256 little-endian uint32 palette entries (exactly
1,024 bytes) with only the low 12 bits (RGB444) set. Expansion: `R = ((word >>
8) & 15) * 17`, `G = ((word >> 4) & 15) * 17`, `B = (word & 15) * 17`. Output
is 320x200 with no resizing, borders, interpolation, annotation, transparency,
gamma or dithering; not a physical VGA capture.

### Decoder API

| Call | Contract |
| --- | --- |
| `decode_frame(indices, palette) -> bytes` | Validates immutable bytes; 192,000 row-major RGB888 bytes; standard library only. |
| `encode_frames(frames, durations_ms) -> EncodedFrames` | 1..16 `(indices, palette)` pairs and equally many durations; returns `.pngs` and `.gif`; no files, ports, clocks or hardware; invalid input raises `ValueError`; Pillow imported lazily. |

Durations are integers (not booleans), multiples of 10 ms in 10..60,000:
sampled pacing, not measured FPS. Every sample is kept, including repeated or
black frames; the GIF loops forever. Pillow's public `GifImagePlugin.getheader`
/ `getdata` write full-frame rectangles with local palettes and disposal 1
(avoiding automatic merging of identical samples); PNG compression level 9
without optimization; no quantization. Every encode reopens all PNGs and the GIF
and checks exact RGB, sample count, durations and loop; encoder changes fail
rather than alter output. Baseline Pillow 10.2.0 (never auto-installed);
determinism holds within one Pillow/compression environment. Bounds: each PNG ≤
256 KiB, the GIF ≤ 4 MiB, raw input ≤ 16 × (64,000 + 1,024) bytes (frame count
rejected before reading files).

### Decoder command line

```text
python src/nand2doom/scripts/host/n2d_frame_decode.py --help
python src/nand2doom/scripts/host/n2d_frame_decode.py --frame workdir/scene.idx workdir/scene.pal --frame workdir/turn.idx workdir/turn.pal --duration-ms 500 500 --output workdir/doom-frames --dry-run
```

Python 3.10+. `--frame INDICES PALETTE` repeats in order with one
`--duration-ms` each; inputs are explicit local regular files (symlinks to
regular files allowed), read to expected length plus one byte. `--output` is
mandatory, relative to the checkout root, a fresh directory strictly below
`workdir/`: absolute paths, traversal, drive/backslash forms, any symlink, any
existing non-directory component (including `workdir`) and an existing leaf
are rejected; parents are rechecked after encoding (guarding mistakes, not
concurrent adversaries). `--dry-run` validates and prints a plan without Pillow,
directories or writes; help works without site packages. Exit 0 success; 2
validation, dependency, filesystem or argument errors. Success writes
`frame-000.png`, …, `sampled.gif`, then `decode.json` (dimensions, ordered input
SHA-256, durations, output names and SHA-256, and a statement that hardware
provenance and coherence were not verified; no time, host details or assets).
A missing `decode.json` means incomplete output; retry into a new directory
(nothing is deleted or retried).

### Decoder tests

```text
python3 -m pytest src/nand2doom/scripts/host/tests/test_n2d_frame_decode.py
```

Tests (`src/nand2doom/scripts/host/tests/test_n2d_frame_decode.py`) use only
generated bytes under ignored `workdir/`: channels and endianness, row and index
boundaries, high bits, length/type/count/duration failures, per-frame palette
changes, identical and black frames, exact PNG/GIF readback, deterministic
bytes, size limits, output confinement, missing Pillow, help and dry run
without site packages. No RTL, camera or physical acceptance is implied.
