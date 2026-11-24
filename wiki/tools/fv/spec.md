# Formal runner: spec

How the runner works, against its [requirements](prd.md). The tool is `tools/fv/fv.py`; it needs the OSS CAD Suite pinned in `cfg/toolchain.json` ([toolchain](../toolchain/spec.md)). How to choose targets and write properties is in the [formal verification BKM](../../bkm/fv.md).

## Targets

A target is one SymbiYosys file, `src/<project>/fv/<block>/<target>.sby`, next to its harness. Its first comment lines hold the runner's fields:

| Field | Meaning |
|---|---|
| `# level: L0` | Regression level, `L0` to `L4` ([CI levels](../../bkm/flow.md#ci-levels)), or `manual` (run only by name) |
| `# timeout: 600` | Time budget in seconds for all the file's tasks together; 600 when absent, and never more |
| `# library: <harness>` | The library the target proves, usually its harness; `# flavors:` and `# defines:` select variants and conditional needs ([Bazel spec, Libraries](../bazel/spec.md#libraries)) |

The `[tasks]` section lists the tasks; by convention `prove` (`mode prove`, engine `abc pdr`) and `cover` (`mode cover`, engine `smtbmc yices`, with a depth). The file has no `[files]` section and its script names the library's sources as `{sources}`: the runner writes `<workdir>/<target>.sby` with `{sources}` replaced by the library's ordered sources and a `[files]` section that copies each source and header to its repository path (`src/...`), so ``` `include ``` paths stay root-relative, and runs that. The script reads them with yosys-slang:

```
read_slang --no-synthesis-define -I . {sources} --top <harness>
prep -top <harness>
flatten
techmap
opt -fast
setundef -undriven -anyseq
setundef -anyseq
```

`--no-synthesis-define` keeps the RTL's simulation-only assertions, so they are proven with the harness. `setundef -anyseq` turns the `x` of out-of-range selects into free values, which can only add behaviours, never hide one; the AIGER flow behind `abc pdr` refuses `x` bits otherwise.

The harness is a module that instantiates the block with its inputs as free inputs, assumes reset on the first cycle, and holds the assumptions (`assume`), properties (`assert`) and covers (`cover`) as immediate statements in clocked processes, with its own registers for history.

## Command line

```
python3 tools/fv/fv.py list
python3 tools/fv/fv.py run <target>...
python3 tools/fv/fv.py run --project nand2doom [--level L0]
python3 tools/fv/fv.py run --all
```

`--workdir DIR` (default `workdir/fv/`, ignored by git) sets the scratch directory. Each target runs `sby -f --prefix DIR/<target> <file>` from the file's folder in its own process group, so a target past its budget is stopped whole. Task `t` writes `DIR/<target>_t/`, with `PASS`, `FAIL`, `ERROR` or `UNKNOWN` as the verdict file and the traces under `engine_0/`; the combined log is `DIR/<target>.log`.

`sby` is taken from `$SBY` when set, else the pinned suite's `bin/sby`, else `PATH`. The suite's wrapper scripts set their own `PATH` inside, so the suite's `bin/` is never put on the session `PATH`, where its Verilator and Python would shadow the pinned ones.

## Front-end limits

yosys-slang, the SystemVerilog front end in the suite, is what parses the repository's packages, structs and `import` (the built-in Yosys reader does not). Two limits shape the harnesses and the RTL:

- Concurrent assertions with sequence operators (`|=>`, `##`) are not supported; harnesses write history registers and immediate assertions instead.
- Sampled-value functions (`$stable`, `$past`, `$rose`, `$fell`) are rejected inside a process with an asynchronous reset. Only the reset synchronizer and the JTAG scan domain reset asynchronously ([RTL coding](../../bkm/rtl.md#rules-reset-and-clocks)).

## Under Bazel

Bazel runs every target as the test `//src/<project>:<target>.formal` ([Bazel spec](../bazel/spec.md)): `fv.py run <target>` with `FV_ROOT` (replaces the repository root, so the run reads Bazel's declared inputs) and `SBY` (the suite's `sby`, resolved to its real path so the suite's wrapper finds its install) set, writing into `$TEST_UNDECLARED_OUTPUTS_DIR` when it is set, and printing a failing target's log so it lands in the test log.

## Origin

New in this factory (#104). SymbiYosys is the formal tool chosen in ADR 0009.
