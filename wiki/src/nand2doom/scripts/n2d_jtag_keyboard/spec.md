# nand2doom JTAG keyboard

Plays an already running nand2doom image from the host keyboard over the
USB-Blaster, without a PS/2 keyboard: Doom actions, raw Set-2 events or typed
text reach the same input FIFO as the PS/2 connector and the UART input
command, through the shared [JTAG library](../../../common/scripts/jtag/spec.md) (explicit names,
one bounded `quartus_stp` session, exactly-once delivery resubmitting only a
proven refused suffix). Only the existing Set-2 format: the
[local control actions](../n2d_control/spec.md#supported-logical-actions), raw
10-bit events and shared text translation; no new key mapping, discovery,
programming or UART fallback. Sending needs `--yes` and the bench owner's JTAG
access (shared with programming and other projects). Simulation evidence:
[io DV](../../dv/io/plan.md#jtag-keyboard-input); physical evidence is
a board run.

## Command line

`n2d_jtag_keyboard.py`
(Python 3.10+) imports only the action table from `n2d_control.py`, the
resolver `n2d_jtag_device.py`
and `src/common/scripts/jtag/n2a_jtag_keyboard.py`.

```text
python3 src/nand2doom/scripts/n2d_jtag_keyboard.py key "Turn left" Fire --hold 0.5
python3 src/nand2doom/scripts/n2d_jtag_keyboard.py key Forward --mode press
python3 src/nand2doom/scripts/n2d_jtag_keyboard.py send 0x275 0x375
python3 src/nand2doom/scripts/n2d_jtag_keyboard.py type idkfa
python3 src/nand2doom/scripts/n2d_jtag_keyboard.py --cable 'USB-Blaster [1-1]' --yes key Fire
python3 src/nand2doom/scripts/n2d_jtag_keyboard.py --cable 'USB-Blaster [1-1]' \
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

## Device resolution

Installs name the DE2-115 (IDCODE `0x020F70DD`) differently (`@1: EP4CE115
(0x020F70DD)` or `@1: 10CL120(Y|Z)/EP3C120/.. (0x020F70DD)`). An explicit
`--device` is used verbatim (`jtag_device_source` `--device`). Otherwise, with
`--cable`, one `quartus_stp -t n2d_jtag_devices.tcl CABLE` (30 s) prints one
`N2D_DEVICE ` line per `get_device_names` entry, and the name whose trailing
`(0xXXXXXXXX)` equals `0x020F70DD` is chosen (`IDCODE 0x020F70DD`). Zero or
several matches, nonzero exit or timeout refuse with the listed names before
any transport. The shared library still takes exact names.

## Results

The JSON adds `sent`, `status` and `close`. Exit 0: every byte accepted and
closed. Exit 1: transfer error, expired bound (a FIFO that never drains) or
unconfirmed close, with `error` and no replay; key state may be uncertain.
Exit 2: refused before any device effect (unknown action, invalid event,
untranslatable text, missing or invalid settings, device not resolving to
exactly one match).

## Tests

`src/nand2doom/scripts/tests/test_n2d_jtag_keyboard.py`: translation of every
non-prefix event and Doom action, chord order, batching, inert dry run and
`--help`, resolution from both listing forms, zero and ambiguous matches,
listing failure and timeout, the resolved name in the dry run, `--device`
skipping the listing, refusal before opening, delivery through the real shared
`Session` against the shared test's independent wire peer with refused
suffixes, uncertain delivery and the whole-request bound.
