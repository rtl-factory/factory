# Bazel build: spec

How the build meets its [requirements](prd.md). How agents use it day to day is in the [build](../../bkm/flow.md#build) BKM; this page is how it works.

## Layout

| File | What it holds |
|---|---|
| `.bazelversion` | The Bazel version; Bazelisk downloads it |
| `MODULE.bazel` | Dependencies (rules_python, bazel_skylib), the Python toolchain, the locked Python packages (`tools/sim/requirements_lock.txt`) and the factory's own repositories |
| `.bazelrc` | Strict action environment, the local disk cache, one config per regression level, the `ci` config; machine-only settings go in `user.bazelrc` (ignored by git) |
| `tools/bazel/extensions.bzl` | The `hdl_registry` and `verilator` repositories |
| `tools/bazel/hdl.bzl` | `hdl_project()`, the `verilator_model` rule, the test macros and `unit_tests()` |
| `tools/bazel/pytest_main.py` | The entry point of every unit-test target |
| `tools/bazel/quartus_image.py` | The board image action: runs a project's Quartus script and keeps its outputs |
| `tools/bazel/hdl_libraries.py` | The HDL libraries: derives each project's `cfg/libraries.json` from its HDL, resolves a target's libraries into its ordered file list, and prints the registry Bazel reads |
| `src/<project>/BUILD.bazel` | One call, `hdl_project(name = "<project>")` |

## Repositories

- `@hdl_registry` runs `tools/bazel/hdl_libraries.py registry` with the host's `python3`. It reads every `src/<project>/cfg/targets.json` and exposes them as `SIM_TARGETS`, a dictionary from project to its targets with their libraries resolved into `sources` and `headers`, every `src/<project>/fv/<block>/<target>.sby` as `FV_TARGETS`: its header's level and timeout, and the files of its library, and the lint tops (`hdl_libraries.py lint_tops`, with the `lint` objects of the registries) as `LINT_TARGETS`: each top's sources and headers. Bazel watches the files and folders it read, so editing a registry, a library list or a `.sby` file, or adding one, re-reads it. The formats are the runners' ([simulation](../sim/spec.md), [formal](../fv/spec.md)); nothing else lists targets.
- `@oss_cad_suite` checks the `VERSION` stamp in the suite's install directory (`$OSS_CAD_SUITE`, else `install_dir` in `cfg/toolchain.json`) against the pin and links its `bin/` folder; the stamp is an input of every formal test. Without the pinned suite it provides an `sby` that prints the fix and fails, so only the formal tests fail.
- `@verilator` finds the Verilator install (`$VERILATOR`, then `PATH`, then `~/verilator/bin`), checks `verilator --version` against `cfg/toolchain.json`, and links its executables into the repository. They are inputs of every model action, so a different binary rebuilds every model.

## Targets of a project

`hdl_project(name)` in `src/<name>/BUILD.bazel` makes, for each target of the project's registry:

| Target | What it is |
|---|---|
| `//src/<name>:<target>` | The test, tagged with the target's level (`L0`…`L4`), or `manual` for a manual target, which `//...` skips |
| `//src/<name>:<target>.spec` | The target's registry entry as JSON: the one input the runner reads |
| `//src/<name>:<first>.model` | The Verilator model, named after the first target (in name order) that builds it |
| `//src/<name>:<target>.formal` | A formal target's test, tagged with its level |
| `//src/<name>:<top>.lint` | The Verilator lint of one of the project's lint tops, tagged `L0` and `lint` ([lint spec](../lint/spec.md#verilator-lint)) |

It also makes `:python` (the project's Python, with every non-HDL file as data), `:headers` (its `.svh` and `.vh` files), `:all_files`, and exports every file, so another project's target can name it as a source.

Two cocotb targets share a model when their top, sources, parameters and public depth are equal. The model action reads `<first>.model.json`, which holds only those build fields, so editing a test-only field (`max_cycles`, `testcase`, the description) reruns the tests but rebuilds no model. A native target gets its own model, since its binary is named after it.

## Model action

`verilator_model` runs `tools/sim/sim.py build-model --spec <spec> --out <dir>` in the sandbox, with only the target's sources, both projects' headers, the spec and Verilator as inputs, `SIM_ROOT=.` so the runner reads the sandbox rather than the checkout, a fixed `PATH`, and an empty `OBJCACHE`: a Verilator configured where `ccache` was on `PATH` names it in its `verilated.mk` (`OBJCACHE ?= ccache`), and the model build must not depend on it (`sim.py` sets the same default for builds outside Bazel). The runner builds the model as it always has (cocotb's Verilator flow, or `verilator --binary` for a native target) and deletes everything but the executable, so a cached model holds no object files. Each action reserves two CPUs; Bazel runs as many as fit.

## Test

A test runs `sim.py run-model --spec <spec> --model <dir>` with every project's Python, the tools' Python (`//tools:python`), cocotb, cocotb-coverage and the coverage helper (`//tools/sim:sim_coverage`): tests import across projects and tools, so each sees all of it, and any Python change reruns every test (but no model). The runner runs the target's test against the built model under the target's time budget and applies the same verdicts as `sim.py test`, including the expected fatal of a negative target. Logs, `results.xml`, waveforms and coverage go to the test's outputs (`bazel-testlogs/<package>/<target>/test.outputs/`); on failure the logs are also printed into `test.log`.

The Bazel timeout is the shortest of short, moderate, long and eternal that is at least 30 s over the target's `timeout_seconds`, so the runner's own timeout fires first and names the target. A wrapped target is always `eternal`, and `.bazelrc` raises `eternal` to 10 hours, above the runner's longest budget for a wrapped target (`timeout_seconds` plus the 900 s compile allowance, times four: 9 hours for the game qualification).

- Seed: `--test_env=SIM_SEED=<n>`; otherwise the target's `default_seed`.
- Waveforms: `--//tools/bazel:waves` rebuilds the models with tracing and passes `--waves`.
- Code coverage: `--//tools/bazel:coverage` rebuilds the models with Verilator's `--coverage` and passes `--coverage`; each test's coverage files and summary land in its outputs ([sim spec, Coverage](../sim/spec.md#coverage)). The coverage tools (`verilator_coverage` and its binary) are `@verilator//:verilator_coverage`, an input of tests only, so they never rebuild a model. Functional coverage needs no flag: a test that declares it always reports it.

## Formal test

A formal test runs `tools/fv/fv.py run <target>` with `FV_ROOT` set to its runfiles, so SymbiYosys reads the `.sby` file and the sources it copies from Bazel's declared inputs, and `SBY` naming the suite's `sby`. Every task's folder, verdict file, log and traces go to the test's outputs. The Bazel timeout follows the header's `timeout` as for simulation.

## Unit tests

`unit_tests()` (called in every project package, `//tools`, `//tools/sim` and `//tools/fv`) makes one test per `tests/` folder of its package, named after the folder's owner (`//src/nand2mario:scripts_board.unit`, `//tools:lint.unit`, `//tools/sim:sim.unit`): `tools/bazel/pytest_main.py` runs pytest on that folder with every project's and the tools' Python and the locked packages. Unit tests are tagged `L0`, `unit`, `no-sandbox` and `external`: they read the checkout (registries, wiki pages, programs), some write its `workdir/` or download pinned inputs, and the whole set takes seconds, so they always run rather than being cached. A new `tests/` folder in a package that calls `unit_tests()` becomes a test with no edit; a new package with tests must call it. The strict action PATH has no Verilator, so each unit test gets Bazel's `@verilator` and `pytest_main.py` puts it first on PATH (`UNIT_PATH_PREPEND`).

`//tools:comment_lint` runs the comment lint as an L0 test, tagged `external` and `no-sandbox` because it lists the checkout's tracked files with git. It reads the base branch's baseline from `workdir/lint/base-baseline.json` when that file exists; the L0 job writes it from the pull request's base branch before `bazel test`. `//tools:wiki_refs` runs the wiki reference check the same way, with the same tags. `//tools:rtl_lint` runs the RTL rule checker the same way, against `workdir/lint/base-rtl-baseline.json`; the Verilator half of the RTL lint is the sandboxed, cached `<top>.lint` tests of the projects.

## Wrapped targets

A target with a `wrapper` prepares its program image before it runs, so its test runs `sim.py --workdir workdir/bazel/<target> test <target>` unchanged, tagged `external` (always run), `no-sandbox`, `no-cache` and `wrapped`: it builds its own model and writes under the checkout's `workdir/`, and always reruns. `--//tools/bazel:waves` reaches it as `SIM_WAVES=1` and `--//tools/bazel:coverage` as `SIM_COVERAGE=1`, so its waveform and coverage land under `workdir/bazel/<target>/`. This holds until program builds become Bazel actions.

## Libraries

Every HDL file is a library, declared once in its project's `src/<project>/cfg/libraries.json` and named after it: a module or package file by its file name, a header by its name without the extension. An entry holds the file (`srcs`, or `headers` for an include file) and `deps`, the libraries it needs: the modules it instantiates, the packages it names (`pkg::`) and the headers it includes. A module with several implementations is one library with `variants` (`default`, `intel`, `asic`; the file without a suffix or with `_portable` is `default`), each with its own file and needs. A need named only under `` `ifdef X `` or `` `ifndef X `` is written `{"if": ["X"], "deps": [...]}` or `{"unless": ["X"], "deps": [...]}`; a branch of `` `elsif `` or `` `else `` also holds the negation of the earlier branches, and an include guard (`` `ifndef X `` followed by `` `define X ``) is not a condition. A module named in more than one file must have a default file, and two files that would be the same library and variant fail the generator. Design code (`rtl/`, `fpga/`) never needs a `dv/` library: a vendor primitive's stand-in is named by the target that wants it.

The files are generated, never edited: `python3 tools/bazel/hdl_libraries.py update` rewrites them from the HDL, and the L0 test `//tools/bazel:bazel.unit` fails while one is stale. The parse is textual and spans lines: an instance is a name followed by `#(` or by an instance name and `(`, a package by `name::`, an interface port by `name.modport port`, and a header by its `` `include `` path from the repository root. A name counts only when some HDL file defines it as a module, package or interface.

A target names its libraries, never its files:

- A simulation target in `targets.json` uses `libraries` (default: the library of its `top`), `flavors` (the variants to prefer, such as `["intel"]` for a test of the Intel backend) and its `defines`.
- A formal target's `.sby` header names `# library: <harness>`, and optionally `# flavors:` and `# defines:`; its script reads `{sources}`, and the formal runner writes the `[files]` section ([formal runner spec](../fv/spec.md)).
- A board manifest (`src/<project>/cfg/*_quartus.json`) names `libraries` and `flavors: ["intel"]`; the board script resolves them into the sources and headers it writes into the Quartus project.

Resolving lists every library after the libraries it needs, each once, so packages come before their users; the headers are listed separately. `python3 tools/bazel/hdl_libraries.py files <library>... [--flavor F] [--define D] [--headers]` prints a library's list as a `.f` file for any other tool.

## Board images

`quartus_image()` in a project package makes `//src/<project>:<board>_image` (nand2mario: `de10_lite_image`, `de2_115_image`, `de2_115_library_image`; nand2doom: `de10_lite_image`, `de2_115_image`), built with `bazel build` on a host with the pinned Quartus. The action runs `tools/bazel/quartus_image.py`, which runs the [Quartus build tool](../quartus/README.md) (`tools/quartus/quartus.py --manifest M [--board B] [--profile P]`, from the target's `manifest`, `board` and `profile`) in the checkout, unchanged: the tool stages `workdir/quartus/<project>/`, compiles, runs its timing audit and its report gate, and fails the build when the gate fails. The SOF (`output_files/<project>.sof`) and its identity files beside it, the gate's evidence, the logs and the reports are then copied into `bazel-bin/src/<project>/<board>_image/`; a tool run that passes but leaves no SOF fails the build. A build lock left by a cancelled or killed build (Bazel kills the whole process tree) is removed on the next build once its process is gone; a SIGTERM sent to the wrapper alone reaches the tool as SIGINT, so it removes its own lock. The action is local and unsandboxed (Quartus is a licensed per-host install and the tool builds in the checkout), but cached: its inputs are every file of the project and of `common` (except Python bytecode), the tool's files and `cfg/toolchain.json`, so a rebuild with no change runs no Quartus. Quartus is found through `QUARTUS_SH` in the shell (pass it with `--action_env=QUARTUS_SH` in `user.bazelrc`), else the pinned install directory. Image targets are tagged `manual` and `quartus`, so `//...` never builds them.

## Levels and CI

`--config=l<N>` filters tests by tag to levels L0…LN (`.bazelrc`). The L0 job fetches the base branch's comment and RTL baselines and runs `bazel test --config=ci --config=l0 //...`. On the self-hosted runner ([Runners](../../bkm/flow.md#runners)) the toolchain, Bazel and the disk cache stay on the host between runs. On a GitHub-hosted runner (pull requests from forks) it first installs Verilator and the OSS CAD Suite, sets up Bazel with `bazel-contrib/setup-bazel` (which keeps Bazelisk and the repository cache), restores the disk cache from the GitHub Actions cache under a key per commit, falling back to the newest earlier one, saves it again after the run, and adds `--config=ci-hosted` to cap that cache at what the Actions cache holds.

## Hermeticity

What a cached result does not see: the host C++ compiler and `make` that Verilator calls, files a test reads through a path it resolved out of the runfiles, and everything a wrapped target does. The fixed action `PATH` keeps the first stable; a test must read its data through its own package path, never the checkout.
