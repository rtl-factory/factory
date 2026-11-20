# JTAG keyboard host: spec

How the host works, against its [requirements](prd.md). Code in `src/common/scripts/jtag/`: `n2a_jtag_keyboard.py` (the library), `n2a_jtag_keyboard.tcl` (the Quartus side) and `tests/`. It talks to the JTAG keyboard endpoint of `common` (`src/common/rtl/jtag/`), whose own spec is not ported yet.

## Parts

| Part | Role |
|---|---|
| Translation | `keys_to_scancodes`, `key_strokes`, `split_trailing_escape`: host bytes (a `TERM=linux` key stream) to Set-2 make and break codes |
| Session | `Session`, `send_all`, `type_text`: the command and status protocol with the endpoint, exactly-once delivery |
| Transport | `ProcessTransport` and `open_transport`: one persistent `quartus_stp` process running the Tcl script, driven by line messages |
| Native worker | `native_worker` (`--native-worker`): runs under Windows Python when Quartus is the Windows build and the caller is in WSL |
| Settings | `settings`: validates the explicit connection settings; starts nothing |

## Wire format

The endpoint has two virtual instruction registers, each a 64-bit data scan:

- **IR 1, command.** The host shifts in `0xA3` (bits 63:56), the number of codes 1 to 4 (55:48), an 8-bit sequence number (47:40) and up to four scan codes, first code in bits 7:0 (31:0).
- **IR 2, status.** The host reads `0x5A` (63:56), busy (55), error (54), result code (53:51: 0 reset, 1 all accepted, 2 partly accepted), number accepted (50:48), sequence of the last command (39:32) and the running total of accepted codes (31:0). Bits 47:40 are zero.

Any other identity, a set error bit, an unknown result code or an inconsistent combination (a reset status with nonzero fields, "all accepted" with none accepted) is an uncertain outcome.

## Delivery

1. On attach, poll the status until not busy. A reset status starts the sequence at 0; otherwise continue after the last sequence and total.
2. Before each batch, read the status again; if it differs from the last one seen, stop (a reset or another host). Then send one command of at most four codes and poll until not busy.
3. Accept the result only if the sequence matches, the count is no more than sent, "all accepted" means every code, and the total grew by exactly the count. Advance the sequence and total.
4. `send_all` resends only the refused suffix, pausing 10 ms after a batch that accepted nothing, until every code is accepted or the delivery deadline passes.
5. Any uncertain outcome of a batch marks the session poisoned: every later send raises until the caller recovers explicitly (closes and reattaches after checking the design). `send_all` reaching its delivery deadline raises without poisoning, because which codes were refused is known.

`type_text` sends each key's make codes, waits `hold` (default 50 ms), then its break codes, all under one deadline (default 20 s).

## Transport

`open_transport` starts `quartus_stp -t n2a_jtag_keyboard.tcl <cable> <device> <tck_hz> <instance> <clock_mode> <cable_index>` in its own process group. The script opens the named device, sets or checks the clock, locks the device and replies `N2A READY`. Each request line `SCAN <id> <ir> <16 hex digits>` gets `N2A OK <id> <hex>`; `QUIT` unlocks, closes and replies `N2A CLOSED`. Errors reply `N2A OPEN_ERROR`, `N2A ERROR` or `N2A CLOSE_ERROR` and exit nonzero. A reply with the wrong id, a late reply or a lost process aborts the transport; abort kills the process group (Linux) or the job object that holds the Quartus tree (Windows).

From WSL with Windows Quartus (`quartus_stp.exe` in `quartus_bin`), the host starts `windows_python -I -u n2a_jtag_keyboard.py --native-worker ...` instead; the worker owns the Quartus process on the Windows side and relays the same line protocol.

## Settings

| Key | Meaning |
|---|---|
| `jtag_cable`, `jtag_device` | Exact names, both or neither; neither means no keyboard |
| `jtag_tck_hz` | 1 to 10,000,000 |
| `jtag_instance` | Must be 0 |
| `jtag_clock_mode` | `fixed-original-6mhz` (default: an original USB-Blaster at exactly 6 MHz, clock never queried) or `adjustable` (sets the clock through `jtagconfig` and reads it back) |
| `jtag_cable_index` | Required for `adjustable`; must match the cable name in `jtagconfig`'s list |
| `quartus_bin`, `windows_python`, `wsl_distro` | Where Quartus is, and the Windows side for WSL |

## Deadlines

| Step | Bound |
|---|---|
| Process start to `N2A READY` | 10 s (12 s through the native worker) |
| One scan | 2 s (5 s through the native worker) |
| One batch, from status check to accepted | 3 s |
| Whole delivery | set by the caller |

## Users

nand2mario's board flow (`src/nand2mario/scripts/board/n2m_board.py`, `joypad`) maps button names to keys and sends them through this library.

## Origin

Ported from the captain's nand2anything repository (`tools/jtag/`) with nand2mario (issue #67).
