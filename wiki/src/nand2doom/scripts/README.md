# nand2doom scripts

The `scripts` discipline of nand2doom: host-side code bound to this design. Each script builds for, drives or measures nand2doom, and would be useless without it ([repository layout](../../../bkm/layout.md#repository-layout)). Code: `src/nand2doom/scripts/<script>/`, with each script's tests in its `tests/` folder.

| Script | What it does |
|---|---|
| [sw_build](sw_build/README.md) | Builds the RV32IM validation image and the Doom image from pinned, digest-checked inputs |
| [board](board/README.md) | Builds and gates the Quartus image for the DE10-Lite or DE2-115, and analyzes timing at other system clocks |
| [host](host/README.md) | Talks to a running board over UART and JTAG: debug and image loading, one-command play, real-time controls, JTAG keys, coherent frame capture and offline decoding |
| [perf](perf/README.md) | Runs the native Doom performance harness and checks its frames, profiles retirement traces by ELF symbol, and reproduces the half-width render oracle |

Project-wide checks that belong to no one script (the target registry, the firmware keyboard map, the capture and clock helpers, the gameplay and input witnesses, the C signature helper) stay in `src/nand2doom/scripts/tests/`.

```sh
python3 -m pytest src/nand2doom/scripts
```
