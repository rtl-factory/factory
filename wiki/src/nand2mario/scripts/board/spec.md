# Board flow: spec

How the board flow meets its [requirements](prd.md). Code: `src/nand2mario/scripts/board/`; tests in its `tests/`.

## Parts

| Part | Role |
|---|---|
| `n2m_board.py` | The command: `compile`, `program`, `load`, `snapshot`, `joypad`. Each subcommand without `--yes` is a dry run |
| `n2m_quartus.py` | Stages the Quartus project from the board config, runs the build and a separate timing audit, applies the report gate, writes `evidence.json` and the image identity |
| `cart_loader.py` | Host side of the UART loader protocol: frame codec, CRC, and a `Session` with bounded stop-and-wait retries over any byte transport |
| `frame_snapshot.py` | Frame snapshot: `plan`, `capture`, `decode`, `gif` |

## Configs

The board configs are `src/nand2mario/cfg/n2m_quartus.json` (DE10-Lite), `src/nand2mario/cfg/n2m_de2_115_quartus.json` (DE2-115) and `src/nand2mario/cfg/n2m_library_quartus_profile.json` (the game-library profile). Each holds project, top, family, device, clock, timeout, timing corners, the top library and its `intel` flavor (resolved into the sources and headers, [Bazel spec, Libraries](../../../../tools/bazel/spec.md#libraries)), pins and I/O standards, reviewed warnings and expected RAM geometry. `validate_config` rejects a config missing any required key before anything is staged.

## Build and gate

1. Take the build lock and stage the project under `workdir/quartus/<project>/`; every output path is confined below `workdir/`.
2. Run `quartus_sh` compile, then a separate timing audit.
3. Gate the reports: fitted resources and RAM geometry against the config; the system clock name and frequency; `check_timing` categories (exactly one `no_clock`, one `virtual_clock` and one `no_output_delay`, or two `no_output_delay` on the DE2-115; any other count or category fails); unconstrained and illegal clocks and ports (UCP); setup and hold slack at every corner, including the SDRAM and DAC paths (an SDRAM write path starts in the SDRAM controller, or in the board reset synchronizer for `DRAM_CKE` and `DRAM_CS_N`, which follow reset); and every warning, which must be listed as reviewed (the one known connectivity warning is matched exactly).
4. Write `evidence.json` (schema, project, device, resources, slack per corner, Fmax, clocks, warnings, Quartus version, source commit) and the SOF's identity sidecar. `program` refuses an SOF whose identity does not match its board.

## Loader protocol

The link runs 8-N-1 at 2 Mbaud on both boards, the factory board rate (`cart_loader.py` and `n2m_board.py` default `--baud` 2000000). Request: SYNC, command, 24-bit address, length minus one, payload (WRITE only), CRC-16/CCITT-FALSE. Response: SYNC, status, command, address, length minus one, payload (READ only), CRC. Commands: PING, HOLD, RELEASE, WRITE, READ, SNAP_PIN, SNAP_READ, SNAP_RELEASE. Every request is idempotent, so a lost reply is retried (three retries by default) after draining the input; a reply whose echoed command or address differs, or a stale ack of an earlier write, is never accepted. I/O timeouts are 1 s per operation, never below 50 ms, and 0.5 s for the release on the way out. The protocol's RTL side is the UART loader behind the board UART link ([HAS](../../rtl/has.md), uart row). The same link also serves the common UART memory endpoint, which the board console uses to load a cartridge (`src/common/scripts/uart_mem/uart_mem_load.py --project 'n2m '`); `n2m_board.py load` keeps the loader protocol. The two holds are separate: after an endpoint load that failed and left the core held, a loader RELEASE does not run the core; an endpoint release (a successful load) or a system reset does.

## Frame snapshot

A snapshot record is the displayed 160x144 frame read from the frame store: an eight-byte header (magic `NM`, version 1, on-screen flag and bank, 32-bit frame ID) and four two-bit DMG shades per byte. `capture` releases any stale pin, pins the frame, reads the header and the record in chunks, reads the header again and releases. It fails if the pin is lost or the frame ID changes, and reports, without failing, a frame that went off screen. It writes `<out>.bin` and a `<out>.json` manifest. `decode` and `gif` work offline from those files.

## Joypad

`joypad` maps button names to the Set-2 keys the board decodes (arrows, X, Z, Space, Enter) and sends them through the `common` JTAG keyboard host ([its spec](../../../common/scripts/jtag/spec.md)) as press, hold and release, releasing in reverse order. It needs the exact `--cable` name. Without `--device` it names the device on that cable whose IDCODE is the selected board's (`031050DD` DE10-Lite, `020F70DD` DE2-115), with one read-only `quartus_stp` listing through the board console's [JTAG chain helpers](../../../../tools/board/spec.md#jtag-chain); a given `--device` must carry that IDCODE. Without `--cable`, or without `--yes`, it sends nothing. `program` builds its `quartus_pgm` command with the same helpers.

## Safety

Without `--yes`, `program` prints the exact `quartus_pgm` command, and `load` and `snapshot` write their planned request frames to `workdir/`; no port or cable is opened. Every board operation has a deadline, and a transport error, timeout or interrupt releases the console and the frame pin before the command exits.

## Origin

Ported from the captain's nand2anything repository with the design (issue #67), where these were one board tool; regrouped into this folder in issue #74.
