# nand2doom play

One command for the owner to play Doom on the DE2-115: check the board,
program a DE2-115 image, stream-load Doom over the 3 Mbaud UART, start it, and
open real-time controls over UART or the USB-Blaster JTAG keyboard. A reload
after a software change takes seconds, not the full 29 MB. One explicit
invocation authorizes programming, loading and playing the board it names; it
never discovers ports, refuses busy devices and kills nothing. It reuses the
[UART client](../n2d_uart/spec.md), [local controls](../n2d_control/spec.md)
and [JTAG library](../../../common/scripts/jtag/spec.md). Physical acceptance is one invocation from
programming to Doom on VGA with webcam evidence of the player turning and
moving under held keys over JTAG and over UART, plus the measured load time;
only the operator holding the board runs it.

## Command line

`n2d_play.py` (launcher
`bin/n2d_play`), Python 3.10+; live use needs pyserial, `jtagconfig` and
`quartus_pgm` (`--quartus-bin` or `PATH`), and `quartus_stp` for JTAG controls.

```text
python3 src/nand2doom/scripts/n2d_play.py --port PORT --plan
python3 src/nand2doom/scripts/n2d_play.py --port PORT
python3 src/nand2doom/scripts/n2d_play.py --port PORT --transport jtag
python3 src/nand2doom/scripts/n2d_play.py --port PORT --skip-program
python3 src/nand2doom/scripts/n2d_play.py --port PORT --skip-load --controls terminal
```

| Option | Meaning |
| --- | --- |
| `--port` | UART port; default `$N2D_UART_PORT`; required unless `--skip-load` with JTAG controls. |
| `--board` | `de2_115` (IDCODE `020F70DD`, baud 3,000,000). |
| `--sof` | Default the newest `workdir/quartus/n2d_de2_115/output_files/n2d_de2_115.sof` in this or the main checkout. |
| `--manifest` | Default `workdir/software/n2d_doom/manifest.json` of this checkout. |
| `--skip-program` | Keep the FPGA image; Halt, reload, Resume. |
| `--skip-load` | Ping only and reconnect controls to a running game; implies `--skip-program`. |
| `--force-full` | Load every segment. |
| `--controls` | `auto` (Tk, terminal without Tk), `gui`, `terminal`, `none` (print the controls command). |
| `--transport` | `uart` (default) or `jtag`. |
| `--jtag-device` | Exact `quartus_stp` name; default resolved by IDCODE at the board check. |
| `--timeout`, `--budget` | UART transaction 0.01..1 s; whole load (default 900 s). |
| `--plan`, `--dry-run` | Steps, inputs and modelled wire times; opens nothing. |

Exit: 2 argument error before any device action; 3 device or tool failure; 4
refusal for a busy device; 130 interrupt; otherwise the controls' status.

## Steps

Each prints `[i/N] name ...` and `ok in S s`; the total time to a running game
prints before the controls open.

1. **Exclusive use.** Any `/proc/PID/fd` entry matching the port's real path,
   or (when programming or using JTAG controls) any `quartus_pgm`,
   `quartus_stp`, `quartus_stpw`, `system-console`, `nios2-terminal`,
   `jtagconfig` or `quartus_jli` process refuses with PID and name (`jtagd` is
   the shared server, not a holder).
2. **Board check.** One `jtagconfig` (20 s) must list exactly one cable whose
   chain is exactly the board IDCODE (skipped with `--skip-program` and UART
   controls). With JTAG controls and no `--jtag-device` the device name is
   [resolved by IDCODE](../n2d_jtag_keyboard/spec.md#device-resolution) on that
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
   [`n2d_control`](../n2d_control/spec.md) runs in process with `--connect`,
   the board rate and the transport.

## Stream load with digest skip

One [stream session](../../rtl/io/mas.md#streaming-load-session)
([streaming loader](../n2d_uart/spec.md#streaming-load)): Hello, then per
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

## Failure, interrupt and recovery

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

## Tests

```text
python3 -B -m unittest discover -s src/nand2doom/scripts/tests -p test_n2d_play.py -v
```

test_n2d_play.py (`src/nand2doom/scripts/tests/test_n2d_play.py`) runs
the loader against the independent stream device model: all non-program
segments matching (only program frames sent), all mismatching, one stale byte,
a Digest status error and a silent Digest falling back to a full load, a failed
program verification stopping halted, `--force-full` without pre-digests,
post-load corruption stopping halted, Ctrl-C ending the session without
reset-PC or Resume. Flow tests with fake `jtagconfig`/`quartus_pgm` runners and
openers: exact programmer command, step order, refusal without running tools,
`--skip-load`, `--skip-program`, programmer failure before the UART, the
controls argument vector with the resolved device, the `--jtag-device`
override skipping the listing, an unresolved device stopping before
programming; a fake `/proc` checks holder detection; the plan test proves no
opener or runner is called (with JTAG it prints the pending resolution or the
override). Unit tests are not physical evidence.
