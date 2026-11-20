# nand2doom coherent frame capture

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
| Transport | One [`n2d_uart.Client`](../n2d_uart/spec.md) and serial handle (its codec, identity, limits, poisoning, no-retry policy, optional opener); maintained Halt for coherent access; no retries or speculative cleanup after uncertain failures. N2B2 capabilities on the same client after running Ping and before Halt; exactly 17 CRC32-validated bulk exchanges; unsupported endpoints fail without fallback; mailbox/control commands stay N2D1. |
| Bound | Whole capture including release acknowledgement ≤ 120 s. |
| Output | Fresh ignored `workdir/` artifacts and provenance; the [offline decoder](../n2d_frame_decode/spec.md) owns PNG/GIF. |

## Command line

```text
python -B src/nand2doom/scripts/n2d_capture.py --help
python -B src/nand2doom/scripts/n2d_capture.py --dry-run --manifest workdir/software/n2d_doom/manifest.json --hardware-image workdir/bench/validated.sof --output workdir/captures/frame01
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

## Identity and exact addresses

The [Doom builder](https://github.com/amichai-bd/nand2anything/blob/aeebe92/wiki/tools/build/SPEC.md) hashes owned firmware sources, builder
source, the adapted engine tree, pinned engine/WAD hashes, verified libc and
compiler version into 32 bytes compiled as `n2d_capture_identity`; after
linking it resolves unique symbols with build-time binutils, checks mailbox BSS
kind/size/alignment and identity bytes, and records mailbox and identity
addresses with the full ELF digest. Linker assertions keep the mailbox in
startup-cleared BSS outside heap, stack and WAD. The embedded identity describes
inputs; ELF and binary hashes are manifest records (no circular hash). This is
build provenance, not authentication or live-memory readback; runtime uses no
`nm`, compiler, WSL or address guess.

## Transaction order

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

## Clock bounds and failures

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

## Artifacts

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

## Tests

Host byte-transport tests run the production Client: exact addresses and
counts, fragmentation, initial and foreign state, no sample progress, wrong
READY, bad palette, header changes, poison without cleanup, healthy deadline
release. Firmware and public-port RTL coverage: [software DV](../../dv/software/dv-plan.md).
No mock or Verilator result certifies USB latency or Windows serial behavior.
