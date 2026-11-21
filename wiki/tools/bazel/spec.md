# Bazel build: spec

How the build meets its [requirements](prd.md). How agents use it day to day is in the build BKM (issue #154); this page is how it works.

## Layout

| File | What it holds |
|---|---|
| `.bazelversion` | The Bazel version; Bazelisk downloads it |
| `MODULE.bazel` | Dependencies (rules_python, bazel_skylib), the Python toolchain, the locked Python packages (`tools/sim/requirements_lock.txt`) and the factory's own repositories |
| `.bazelrc` | Strict action environment, the local disk cache, one config per regression level, the `ci` config; machine-only settings go in `user.bazelrc` (ignored by git) |
| `tools/bazel/extensions.bzl` | The `hdl_registry` and `verilator` repositories |
| `tools/bazel/hdl.bzl` | `hdl_project()`, the `verilator_model` rule and the test macro |
| `src/<project>/BUILD.bazel` | One call, `hdl_project(name = "<project>")` |

## Repositories

- `@hdl_registry` reads every `src/<project>/cfg/targets.json` and exposes them as `SIM_TARGETS`, a dictionary from project to its targets. Bazel watches the files it read, so editing a registry re-reads it. The registry format is the simulation runner's ([spec](../sim/spec.md)); nothing else lists targets.
- `@verilator` finds the Verilator install (`$VERILATOR`, then `PATH`, then `~/verilator/bin`), checks `verilator --version` against `cfg/toolchain.json`, and links its executables into the repository. They are inputs of every model action, so a different binary rebuilds every model.

## Targets of a project

`hdl_project(name)` in `src/<name>/BUILD.bazel` makes, for each target of the project's registry:

| Target | What it is |
|---|---|
| `//src/<name>:<target>` | The test, tagged with the target's level (`L0`…`L4`), or `manual` for a manual target, which `//...` skips |
| `//src/<name>:<target>.spec` | The target's registry entry as JSON: the one input the runner reads |
| `//src/<name>:<first>.model` | The Verilator model, named after the first target (in name order) that builds it |

It also makes `:python` (the project's Python, with every non-HDL file as data), `:headers` (its `.svh` and `.vh` files), `:all_files`, and exports every file, so another project's target can name it as a source.

Two cocotb targets share a model when their top, sources, parameters and public depth are equal. A native target gets its own model, since its binary is named after it.

## Model action

`verilator_model` runs `tools/sim/sim.py build-model --spec <spec> --out <dir>` in the sandbox, with only the target's sources, both projects' headers, the spec and Verilator as inputs, `SIM_ROOT=.` so the runner reads the sandbox rather than the checkout, and a fixed `PATH`. The runner builds the model as it always has (cocotb's Verilator flow, or `verilator --binary` for a native target) and deletes everything but the executable, so a cached model holds no object files. Each action reserves two CPUs; Bazel runs as many as fit.

## Test

A test runs `sim.py run-model --spec <spec> --model <dir>` with the project's and `common`'s Python and the cocotb package. The runner runs the target's test against the built model under the target's time budget and applies the same verdicts as `sim.py test`, including the expected fatal of a negative target. Logs, `results.xml` and waveforms go to the test's outputs (`bazel-testlogs/<package>/<target>/test.outputs/`); on failure the logs are also printed into `test.log`.

The Bazel timeout is the shortest of short, moderate, long and eternal that is at least 30 s over the target's `timeout_seconds`, so the runner's own timeout fires first and names the target.

- Seed: `--test_env=SIM_SEED=<n>`; otherwise the target's `default_seed`.
- Waveforms: `--//tools/bazel:waves` rebuilds the models with tracing and passes `--waves`.

## Wrapped targets

A target with a `wrapper` prepares its program image before it runs, so its test runs `sim.py --workdir workdir/bazel/<target> test <target>` unchanged, tagged `no-sandbox`, `no-cache` and `wrapped`: it builds its own model and writes under the checkout's `workdir/`, and always reruns. This holds until program builds become Bazel actions.

## Levels and CI

`--config=l<N>` filters tests by tag to levels L0…LN (`.bazelrc`). The L0 job sets up Bazel with `bazel-contrib/setup-bazel`, which keeps the disk cache and the repository cache in the GitHub Actions cache, and runs `bazel test --config=ci --config=l0 //src/...`.

## Hermeticity

What a cached result does not see: the host C++ compiler and `make` that Verilator calls, files a test reads through a path it resolved out of the runfiles, and everything a wrapped target does. The fixed action `PATH` keeps the first stable; a test must read its data through its own package path, never the checkout.
