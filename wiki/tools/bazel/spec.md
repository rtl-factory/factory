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
| `src/<project>/BUILD.bazel` | One call, `hdl_project(name = "<project>")` |

## Repositories

- `@hdl_registry` reads every `src/<project>/cfg/targets.json` and exposes them as `SIM_TARGETS`, a dictionary from project to its targets, and every `src/<project>/fv/<block>/<target>.sby` as `FV_TARGETS`: its header's level and timeout, and the repository paths its `[files]` section copies. Bazel watches the files and folders it read, so editing a registry or adding a `.sby` file re-reads it. The formats are the runners' ([simulation](../sim/spec.md), [formal](../fv/spec.md)); nothing else lists targets.
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

It also makes `:python` (the project's Python, with every non-HDL file as data), `:headers` (its `.svh` and `.vh` files), `:all_files`, and exports every file, so another project's target can name it as a source.

Two cocotb targets share a model when their top, sources, parameters and public depth are equal. The model action reads `<first>.model.json`, which holds only those build fields, so editing a test-only field (`max_cycles`, `testcase`, the description) reruns the tests but rebuilds no model. A native target gets its own model, since its binary is named after it.

## Model action

`verilator_model` runs `tools/sim/sim.py build-model --spec <spec> --out <dir>` in the sandbox, with only the target's sources, both projects' headers, the spec and Verilator as inputs, `SIM_ROOT=.` so the runner reads the sandbox rather than the checkout, and a fixed `PATH`. The runner builds the model as it always has (cocotb's Verilator flow, or `verilator --binary` for a native target) and deletes everything but the executable, so a cached model holds no object files. Each action reserves two CPUs; Bazel runs as many as fit.

## Test

A test runs `sim.py run-model --spec <spec> --model <dir>` with every project's Python, the tools' Python (`//tools:python`) and the cocotb package: tests import across projects and tools, so each sees all of it, and any Python change reruns every test (but no model). The runner runs the target's test against the built model under the target's time budget and applies the same verdicts as `sim.py test`, including the expected fatal of a negative target. Logs, `results.xml` and waveforms go to the test's outputs (`bazel-testlogs/<package>/<target>/test.outputs/`); on failure the logs are also printed into `test.log`.

The Bazel timeout is the shortest of short, moderate, long and eternal that is at least 30 s over the target's `timeout_seconds`, so the runner's own timeout fires first and names the target. A wrapped target is always `eternal`, and `.bazelrc` raises `eternal` to 10 hours, above the runner's longest budget for a wrapped target (`timeout_seconds` plus the 900 s compile allowance, times four: 9 hours for the game qualification).

- Seed: `--test_env=SIM_SEED=<n>`; otherwise the target's `default_seed`.
- Waveforms: `--//tools/bazel:waves` rebuilds the models with tracing and passes `--waves`.

## Formal test

A formal test runs `tools/fv/fv.py run <target>` with `FV_ROOT` set to its runfiles, so SymbiYosys reads the `.sby` file and the sources it copies from Bazel's declared inputs, and `SBY` naming the suite's `sby`. Every task's folder, verdict file, log and traces go to the test's outputs. The Bazel timeout follows the header's `timeout` as for simulation.

## Unit tests

`unit_tests()` (called in every project package, `//tools`, `//tools/sim` and `//tools/fv`) makes one test per `tests/` folder of its package, named after the folder's owner (`//src/nand2mario:scripts_board.unit`, `//tools:lint.unit`, `//tools/sim:sim.unit`): `tools/bazel/pytest_main.py` runs pytest on that folder with every project's and the tools' Python and the locked packages. Unit tests are tagged `L0`, `unit`, `no-sandbox` and `external`: they read the checkout (registries, wiki pages, programs), some write its `workdir/` or download pinned inputs, and the whole set takes seconds, so they always run rather than being cached. A new `tests/` folder in a package that calls `unit_tests()` becomes a test with no edit; a new package with tests must call it. The strict action PATH has no Verilator, so each unit test gets Bazel's `@verilator` and `pytest_main.py` puts it first on PATH (`UNIT_PATH_PREPEND`).

`//tools:comment_lint` runs the comment lint as an L0 test, tagged `external` and `no-sandbox` because it lists the checkout's tracked files with git. It reads the base branch's baseline from `workdir/lint/base-baseline.json` when that file exists; the L0 job writes it from the pull request's base branch before `bazel test`.

## Wrapped targets

A target with a `wrapper` prepares its program image before it runs, so its test runs `sim.py --workdir workdir/bazel/<target> test <target>` unchanged, tagged `external` (always run), `no-sandbox`, `no-cache` and `wrapped`: it builds its own model and writes under the checkout's `workdir/`, and always reruns. This holds until program builds become Bazel actions.

## Board images

`quartus_image()` in a project package makes `//src/<project>:<board>_image` (nand2mario: `de10_lite_image`, `de2_115_image`, `de2_115_library_image`; nand2doom: `de10_lite_image`, `de2_115_image`), built with `bazel build` on a host with the pinned Quartus. The action runs `tools/bazel/quartus_image.py`, which runs the project's build script (`n2m_quartus.py`, `quartus.py`) with the board's arguments in the checkout, unchanged: the script stages `workdir/quartus/<project>/`, compiles, runs its timing audit and its report gate, and fails the build when the gate fails. The SOF (`output_files/<project>.sof`) and its identity files beside it, the gate's evidence, the logs and the reports are then copied into `bazel-bin/src/<project>/<board>_image/`; a script that passes but leaves no SOF fails the build. A build lock left by a cancelled or killed build (Bazel kills the whole process tree) is removed on the next build once its process is gone; a SIGTERM sent to the wrapper alone reaches the script as SIGINT, so it removes its own lock. The action is local and unsandboxed (Quartus is a licensed per-host install and the scripts build in the checkout), but cached: its inputs are every file of the project and of `common` (except Python bytecode) plus `cfg/toolchain.json`, so a rebuild with no change runs no Quartus. Quartus is found through `QUARTUS_SH` in the shell (pass it with `--action_env=QUARTUS_SH` in `user.bazelrc`), else the pinned install directory. Image targets are tagged `manual` and `quartus`, so `//...` never builds them.

## Levels and CI

`--config=l<N>` filters tests by tag to levels L0…LN (`.bazelrc`). The L0 job fetches the base branch's comment baseline, installs Verilator and the OSS CAD Suite, sets up Bazel with `bazel-contrib/setup-bazel` (which keeps Bazelisk and the repository cache), restores the disk cache from the GitHub Actions cache under a key per commit, falling back to the newest earlier one, and saves it again after the run, and runs `bazel test --config=ci --config=l0 //...`.

## Hermeticity

What a cached result does not see: the host C++ compiler and `make` that Verilator calls, files a test reads through a path it resolved out of the runfiles, and everything a wrapped target does. The fixed action `PATH` keeps the first stable; a test must read its data through its own package path, never the checkout.
