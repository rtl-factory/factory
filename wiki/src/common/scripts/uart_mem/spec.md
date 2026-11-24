# UART memory host: spec

How the host works, against its [requirements](prd.md). Code in `src/common/scripts/uart_mem/`: `uart_mem.py` (the library), `uart_mem_load.py` (the load script) and `tests/` (a hardware-free contract against an independent fake endpoint). It talks to the `common` UART memory endpoint (`src/common/rtl/uart_mem/`), whose own spec is not ported yet; its simulation contract is `src/common/dv/uart_mem/` (targets `n2a_uart_mem_contract` and `n2a_uart_mem_load_readback`).

## Commands

| Command | Code | Effect |
|---|---|---|
| identify | `0x01` | Returns the project id, endpoint version, window map and capabilities (word 2 bit 2: Digest), so the host can refuse a mismatched image |
| hold / release | `0x02` / `0x03` | Holds the CPU (in reset or a maintained halt) or releases it; memory and scanout keep running |
| entry | `0x04` | Sets the entry state while held |
| write | `0x10` | Writes a block of memory; loading uses it while the CPU is held |
| read | `0x11` | Reads a block of memory, held or live, by arbitrating for bus cycles |
| digest | `0x12` | Returns the CRC-32 (zlib's, 4 bytes little-endian) of an aligned range of 4 to 65,532 bytes, read under the read rules (window, held or live, pacing); unlike read it is not bounded by the maximum payload |

A request starts with `0xA8`, a response with `0x8A`; both carry the protocol version (`0x01`), a sequence number and a CRC-8 over the header, and a payload carries a CRC-32. A response status other than `0x00` names the failure (version, command, checksum, alignment, range, length, not held, timeout, memory, overrun) and raises `StatusError`.

## Parts

| Part | Role |
|---|---|
| `Transport` | Any object with `write(data)` and `read(size, deadline) -> bytes`; `read` returns at most `size` bytes as soon as any have arrived, or nothing at the deadline, so a short response never waits out the command timeout |
| `Client` | One endpoint session: sequence numbers, framing, deadlines (`timeout`, default 1 s per command), and the project-byte callback `on_project_bytes`. Any failure sets `poisoned`, and every later call raises |
| `load(client, segments, entry, project_id)` | The load transaction below |
| `read_window(client, address, length)` | One live read (a screen readback) while the CPU keeps running |

## Load transaction

1. Identify, and check the project id. Check every segment (word-aligned, not empty, inside a writable window) before anything is sent.
2. Hold the CPU once.
3. Write each segment, then verify it: by Digest when the endpoint advertises it (one 4-byte answer per 65,532 bytes, so a load costs one pass over the wire, not two), else by reading it back. With `skip_unchanged`, a segment that does not hold the entry and whose stored digest already matches is not written (`load` returns `skipped` or `written` for each segment). A failure here raises `LoadError` and sends no entry or release, so the CPU stays held.
4. After every segment has verified, set the entry state, then release the CPU once. If the entry or release reply is lost, the CPU state is unknown and must be inspected.

## Load script

`uart_mem_load.py IMAGE --port P [--baud B] [--project ID] [--address A] [--entry E] [--skip-unchanged] [--yes]` loads a project image with the load transaction above, over pyserial 8-N-1 with no flow control. IMAGE is a raw image, loaded at `--address` (default: the base of the first writable window), or a `.json` segment manifest: `entry_address` (or `entry`) and `segments`, each with `address` and `file` (or `path`) relative to the manifest's folder, and optionally `byte_length` and `sha256`, which are checked, with every byte read before the port opens. nand2doom's image manifest is one. The entry is `--entry`, else the manifest's, else the endpoint's advertised entry. `--skip-unchanged` skips a segment that does not hold the entry and whose stored digest already matches, and the script prints each segment's outcome. `--baud` defaults to `uart_mem.BOARD_BAUD`, the factory [board rate](../../rtl/uart/mas.md#board-rate). `--project` is four ASCII characters (`'n2m '`) or 0x-prefixed hex, and a mismatch stops the load before the hold. Without `--yes` it prints a JSON plan, with the segments, and opens no port. A failed load exits 1 and leaves the CPU held. It is the one loader of the [board console](../../../../tools/board/spec.md#board-declarations): nand2mario loads a cartridge with it, nand2doom its image manifest with `--skip-unchanged`. nand2doom's `n2d_play.py` stream load stays for development, as the project protocol beside the endpoint.

## Tests

`python3 -m pytest src/common/scripts/uart_mem/tests` covers framing, identity, chunking, the load transaction (one hold, verify before entry, one release, failures leave the CPU held), Digest verify and its 65,532-byte split, unchanged-segment skip (never the entry segment; nothing skipped without Digest), readback and shared-UART project bytes; `test_uart_mem_load.py` covers the load script's plan, defaults, project check, held failure, segment manifests (every segment at its address with its entry, the unchanged data segment skipped on a reload, bad manifests refused before any traffic), and that its serial transport returns a waiting response without sitting out the deadline. In simulation, benches run this library itself through `src/common/dv/uart_mem/sim_host.py` ([DV plan](../../dv/uart_mem/dv-plan.md)); `n2m_system_uart_mem` (L0) loads and boots nand2mario with `load` at the 2 Mbaud board rate.
