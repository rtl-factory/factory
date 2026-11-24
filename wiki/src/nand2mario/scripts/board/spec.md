# Board flow: spec

How the board flow meets its [requirements](prd.md). Code: `src/nand2mario/scripts/board/`; tests in its `tests/`.

## Parts

| Part | Role |
|---|---|
| `n2m_board.py` | The command: `compile`, `program`, `load`, `snapshot`, `joypad`. Each subcommand without `--yes` is a dry run |
| `cart_loader.py` | Host side of the UART loader protocol: frame codec, CRC, and a `Session` with bounded stop-and-wait retries over any byte transport |
| `frame_snapshot.py` | Frame snapshot: `plan`, `capture`, `decode`, `gif` |

## Configs

The Quartus manifests are `src/nand2mario/cfg/n2m_quartus.json` (DE10-Lite), `src/nand2mario/cfg/n2m_de2_115_quartus.json` (DE2-115) and the profile `src/nand2mario/cfg/n2m_library_quartus_profile.json` (the game-library image: `CART_LIBRARY=1`, its active-cartridge RAM and the exact save-RAM removal warnings). Each manifest names its board, project, top, clock, timeout, timing corners, the top library and its `intel` flavor, the pins its top uses (locations and standards from `src/common/fpga/<board>/pins.json`; `UART_RXD`/`UART_TXD` are wired to `ARDUINO_IO[0]`/`[1]` on the DE10-Lite and `GPIO[0]`/`[1]` on the DE2-115), forwarded clocks, timing path groups, RAM requirements and reviewed warnings, and sets `require_clean_source` ([Quartus tool spec, Manifests](../../../../tools/quartus/spec.md#manifests)).

## Build and gate

`compile` runs `tools/quartus/quartus.py` on the selected board's manifest, with the library profile for `--library` (DE2-115 only); `--plan` and `--quartus` pass through. The tool stages `workdir/quartus/<project>/`, compiles, audits timing and applies the [report gate](../../../../tools/quartus/spec.md#report-gate), including the path groups: `sdram_read` (from `DRAM_DQ[*]` to the controller's `regs_q.read_data`), `sdram_write` (to the `sdram_clock` pin, from the SDRAM controller, or from the board reset synchronizer for `DRAM_CKE` and `DRAM_CS_N`, which follow reset) and, on the DE2-115, `dac` (to the `vga_dac_clock` pin). A passing build binds the SOF to its commit ([image identity](../../../../tools/quartus/spec.md#image-identity)); `program` calls `verify_image` with the selected board's manifest and profile and refuses an image that fails it.

## Loader protocol

The link runs 8-N-1 at 2 Mbaud on both boards, the factory [board rate](../../../common/rtl/uart/mas.md#board-rate) (`cart_loader.py` and `n2m_board.py` default `--baud` 2000000). Request: SYNC, command, 24-bit address, length minus one, payload (WRITE only), CRC-16/CCITT-FALSE. Response: SYNC, status, command, address, length minus one, payload (READ only), CRC. Commands: PING, HOLD, RELEASE, WRITE, READ, SNAP_PIN, SNAP_READ, SNAP_RELEASE. Every request is idempotent, so a lost reply is retried (three retries by default) after draining the input; a reply whose echoed command or address differs, or a stale ack of an earlier write, is never accepted. I/O timeouts are 1 s per operation, never below 50 ms, and 0.5 s for the release on the way out. The protocol's RTL side is the UART loader behind the board UART link ([HAS](../../rtl/has.md), uart row). The same link also serves the common UART memory endpoint, which the board console uses to load a cartridge (`src/common/scripts/uart_mem/uart_mem_load.py --project 'n2m '`); `n2m_board.py load` keeps the loader protocol. The two holds are separate: after an endpoint load that failed and left the core held, a loader RELEASE does not run the core; an endpoint release (a successful load) or a system reset does.

## Frame snapshot

A snapshot record is the displayed 160x144 frame read from the frame store: an eight-byte header (magic `NM`, version 1, on-screen flag and bank, 32-bit frame ID) and four two-bit DMG shades per byte. `capture` releases any stale pin, pins the frame, reads the header and the record in chunks, reads the header again and releases. It fails if the pin is lost or the frame ID changes, and reports, without failing, a frame that went off screen. It writes `<out>.bin` and a `<out>.json` manifest. `decode` and `gif` work offline from those files.

## Joypad

`joypad` maps button names to the Set-2 keys the board decodes (arrows, X, Z, Space, Enter) and sends them through the `common` JTAG keyboard host ([its spec](../../../common/scripts/jtag/spec.md)) as press, hold and release, releasing in reverse order. It needs the exact `--cable` name. Without `--device` it names the device on that cable whose IDCODE is the selected board's (`031050DD` DE10-Lite, `020F70DD` DE2-115), with one read-only `quartus_stp` listing through the board console's [JTAG chain helpers](../../../../tools/board/spec.md#jtag-chain); a given `--device` must carry that IDCODE. Without `--cable`, or without `--yes`, it sends nothing. `program` builds its `quartus_pgm` command with the same helpers.

## Safety

Without `--yes`, `program` prints the exact `quartus_pgm` command, and `load` and `snapshot` write their planned request frames to `workdir/`; no port or cable is opened. Every board operation has a deadline, and a transport error, timeout or interrupt releases the console and the frame pin before the command exits.

## Origin

Ported from the captain's nand2anything repository with the design (issue #67), where these were one board tool; regrouped into this folder in issue #74.
