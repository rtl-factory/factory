# nand2doom scripts

Host-side scripts bound to nand2doom, one folder per script group, each with its tests in `tests/`: `sw_build/`, `board/`, `host/`, `perf/`. Their PRDs and specs are in the wiki at `wiki/src/nand2doom/scripts/` ([site](https://rtl-factory.github.io/factory/src/nand2doom/scripts/)). `tests/` here holds project-wide checks that belong to no one script. Run them all with `python3 -m pytest src/nand2doom/scripts`.
