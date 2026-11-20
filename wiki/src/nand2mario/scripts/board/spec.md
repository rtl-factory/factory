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

The board configs are `src/nand2mario/cfg/n2m_quartus.json` (DE10-Lite), `src/nand2mario/cfg/n2m_de2_115_quartus.json` (DE2-115) and `src/nand2mario/cfg/n2m_library_quartus_profile.json` (the game-library profile). Each holds project, top, family, device, clock, timeout, timing corners, sources, pins and I/O standards, reviewed warnings and expected RAM geometry. `validate_config` rejects a config missing any required key before anything is staged.

## Build and gate

1. Take the build lock and stage the project under `workdir/quartus/<project>/`; every output path is confined below `workdir/`.
2. Run `quartus_sh` compile, then a separate timing audit.
3. Gate the reports: fitted resources and RAM geometry against the config; the system clock name and frequency; `check_timing` categories (only `no_clock`, `virtual_clock` and `no_output_delay` once each are allowed); unconstrained and illegal clocks and ports (UCP); setup and hold slack at every corner, including the SDRAM and DAC paths; and every warning, which must be listed as reviewed (the one known connectivity warning is matched exactly).
4. Write `evidence.json` (schema, project, device, resources, slack per corner, Fmax, clocks, warnings, Quartus version, source commit) and the SOF's identity sidecar. `program` refuses an SOF whose identity does not match its board.

## Loader protocol

Request: SYNC, command, 24-bit address, length minus one, payload (WRITE only), CRC-16/CCITT-FALSE. Response: SYNC, status, command, address, length minus one, payload (READ only), CRC. Commands: PING, HOLD, RELEASE, WRITE, READ, SNAP_PIN, SNAP_READ, SNAP_RELEASE. Every request is idempotent, so a lost reply is retried (three retries by default) after draining the input; a reply whose echoed command or address differs, or a stale ack of an earlier write, is never accepted. I/O timeouts are 1 s per operation, never below 50 ms, and 0.5 s for the release on the way out. The protocol's RTL side is the UART loader block (`wiki/src/nand2mario/rtl/uart/mas.md`, not ported yet).

## Frame snapshot

A snapshot record is the displayed 160x144 frame read from the frame store: an eight-byte header (magic `NM`, version 1, on-screen flag and bank, 32-bit frame ID) and four two-bit DMG shades per byte. `capture` releases any stale pin, pins the frame, reads the header and the record in chunks, reads the header again and releases. It fails if the pin is lost or the frame ID changes, and reports, without failing, a frame that went off screen. It writes `<out>.bin` and a `<out>.json` manifest. `decode` and `gif` work offline from those files.

## Joypad

`joypad` maps button names to the Set-2 keys the board decodes (arrows, X, Z, Space, Enter) and sends them through the `common` JTAG keyboard host ([its spec](../../../common/scripts/jtag/spec.md)) as press, hold and release, releasing in reverse order. It needs exact `--cable` and `--device` names; without them, or without `--yes`, it sends nothing.

## Safety

Without `--yes`, `program` prints the exact `quartus_pgm` command, and `load` and `snapshot` write their planned request frames to `workdir/`; no port or cable is opened. Every board operation has a deadline, and a transport error, timeout or interrupt releases the console and the frame pin before the command exits.

## Origin

Ported from the captain's nand2anything repository with the design (issue #67), where these were one board tool; regrouped into this folder in issue #74.
