---
status: draft
---

# Flow and CI

The path every change takes from intent to `main`, the CI levels that gate it, the runners that run them, how to build and check it with Bazel, and the GitHub settings that enforce it.

## Flow

### Steps

1. **Align.** A captain and the first mate agree on the outcome. Either may start: a captain by opening an alignment or grilling session, the first mate by proposing work within a goal the captain already set. Grill ([grilling](planning.md#grilling)) if the goal, scope or acceptance is unclear.
2. **Issue.** Open or update the GitHub issue with its template per [templates](issues.md#templates) and labels per [labels](issues.md#labels). The first mate may open issues and update epics on its own as work goes, within the aligned goal. Mark it `status:ready` once it is clear enough to dispatch.
3. **Branch.** Create `issue/<number>-<slug>` from `main`, or from the epic branch if the issue belongs to an epic.
4. **Worktree.** Check the branch out in `worktrees/issue-<number>/`.
5. **Change.** Make the change. When it touches a block, a script or a tool, change its code and its documents at the mirrored wiki path ([repository layout](layout.md#repository-layout)) together, and loop until they agree. Run L0 until it passes.
6. **Pull request.** Commit, push, and open a pull request whose description says `Closes #<number>`.
7. **Review.** The first mate dispatches a reviewer crewmate; it reviews against the issue, the spec and the BKMs and posts its findings as a pull request review. Fix the findings and return to step 5 until the reviewer passes the pull request.
8. **Approve.** Treat the reviewer's pass as approval, unless the pull request needs captain (see Merging); then wait for a captain to say it is ready, and record that as a pull request comment.
9. **Land.** Once approved and the required checks are green, the first mate merges it (see Merging).
10. **Clean up.** Run `python3 tools/worktree/worktree.py remove worktrees/issue-<number>` (with `--base origin/<epic branch>` for an issue of an epic): it expunges the worktree's Bazel output base, which also stops its Bazel server, then removes the worktree and deletes the issue branch once it has landed ([worktree spec](../tools/worktree/spec.md)). Then run `python3 tools/worktree/worktree.py orphans --remove` to delete output bases left by worktrees removed some other way.

### Rules

- Every change must start with an issue. Why: the issue is the record of what was decided and why (ADR 0003).
- A crewmate must only be dispatched on an issue labelled `status:ready`. Why: a vague issue produces vague work.
- One issue → one issue branch → one pull request. Why: each pull request maps to exactly one decision.
- A change to a block, a script or a tool must change its code and its spec in the same pull request. Why: a spec that lags the code misleads the next reader.
- A pull request into `main` must close at least one issue. Why: nothing lands without a decision behind it; the `issue-link` check enforces it.
- Related issues should be linked ("blocked by", "relates to"), not made into an epic. Why: an epic is for landing together, not for grouping.
- Remove a worktree only with the worktree tool, never with `git worktree remove` or `rm` alone. Why: each worktree has its own Bazel output base (gigabytes) and server, which git leaves behind; on 2026-11-24, 19 orphaned output bases and 6 idle servers filled the disk (#208).

### Merging

This section is the single source for who may merge what.

- Only the first mate lands pull requests. Why: one agent keeps track of what is in flight.
- **Full send** is the default: land once the reviewer has passed it and the required checks are green. Why: most work is done by agents, so captains spend attention only where they ask to (ADR 0007).
- A pull request **needs captain**, and must wait for a captain's explicit go-ahead, when its issue has `authority:needs-captain` or when it changes the factory's controls: `AGENTS.md`, `.claude/skills/`, `wiki/bkm/`, `wiki/project/` or `.github/`. Why: BKMs and settings are how captains steer agents; agents propose changes to them, captains accept them.
- Issue branch → `main` or epic branch: squash merge. Why: one issue, one commit.
- Epic branch → `main`: merge commit, never squash. Why: each issue stays its own commit on `main` (ADR 0008).

### Epics

Use an epic only when several issues cannot reach `main` one by one: each would break `main` until the others land.

1. Open the epic as a parent issue, with its issues as sub-issues linked by "blocked by" where order matters.
2. Create the epic branch `epic/<number>-<slug>` from `main`.
3. Run each sub-issue through steps 3–10 with the epic branch in place of `main`; pull requests into the epic branch run L0.
4. When a sub-issue's pull request lands in the epic branch, close the sub-issue with a comment linking the pull request. Why: GitHub only closes issues on merges into `main`, and an open sub-issue would keep its dependents blocked.
5. When every sub-issue is closed and the epic branch is healthy, open one pull request from the epic branch into `main` that closes the epic. It goes through review, approval and L1 like any other.

### Exit criteria

- The pull request is merged, every issue it closes is closed, and the worktree, its Bazel output base and the branch are gone.

## CI levels

Which checks run when. This section is the single source for the CI schedule. Levels are cumulative: running level N runs every level below it.

### Levels

| Level | Runs on | Purpose |
|---|---|---|
| L0 | on demand; every pull request into an epic branch; inside every L1 run | the fastest possible checks; run it locally while working |
| L1 | every pull request into `main` (pre-merge), and again after every merge to `main` (post-merge) | the minimal gate a pull request must pass |
| L2 | every hour on `main` | broader regression |
| L3 | every 6 hours on `main` | long regression |
| L4 | nightly on `main` | everything, including the longest runs |

These workflows run outside the levels; they are workflow rules, not regression checks:

- `issue-link` runs on every pull request.
- `label-sync` creates and updates labels from `.github/labels.json` on every merge to `main` that changes it ([labels](issues.md#labels)).
- `status-clear` removes `status:` labels from an issue when it closes, and sets `status:triage` when it reopens ([labels](issues.md#labels)).
- `pages` builds the wiki site (`mkdocs build --strict`) on every pull request that touches the wiki, and builds and deploys it on every merge to `main` that does ([wiki site](layout.md#wiki-site)).

### Jobs

| Level | Job | What it runs |
|---|---|---|
| L0 | `l0` (`.github/workflows/l0.yml`), on every pull request into `main` or an epic branch and every merge to `main` | one `bazel test --config=ci --config=l0 //...`: the comment lint (`//tools:comment_lint`), the wiki reference check (`//tools:wiki_refs`), the RTL lint gate (the coding-rule checker `//tools:rtl_lint` and Verilator `--lint-only -Wall` on every project top and common block, `//src/<project>:<top>.lint`), the unit tests of every project's scripts and of the tools, and every L0 simulation and formal target |
| L2, L3, L4 | `regression` (`.github/workflows/regression.yml`), on `main` on a schedule (UTC): L2 at :17 every hour, L3 at :37 every 6 hours, L4 at 23:07 nightly; or by hand with a chosen level | one `bazel test --config=ci --config=l<n> //...` on the self-hosted runner; a failing level opens one `priority:P0` issue, `<level> regression fails on main`, or comments on the open one |

L1 has no job yet: until it exists, a pull request into `main` runs only L0 in CI, and L1 targets run only inside the scheduled levels.

### Rules

- Put a check at the lowest level whose runtime fits that level. Why: the faster a failure shows up, the cheaper it is to fix.
- Keep L0 fast enough to run before every push. Why: L0 is the inner loop, for agents and humans alike.
- When L1–L4 fails on `main`, fix forward or revert at once, through an issue labelled `priority:P0`. Why: we merge fast because we recover fast (ADR 0005).
- Until a level has a CI job, treat it as passing and say so in the pull request. Why: the levels are defined before the runners exist; an honest note keeps that visible.

### Open

- Runtime budget per level is not yet set.
- L1 has no CI job yet; it will run on the self-hosted runner ([Runners](#runners)).
- A scheduled run waits in the queue while the self-hosted runner is offline (GitHub drops it after 24 hours); runs queued meanwhile collapse to at most two per level.

## Runners

Where CI jobs run, and how to set up a host that runs the factory's checks locally or as the self-hosted runner.

### Rules

- Run every job on the self-hosted runner, `runs-on: [self-hosted, rtl-factory]`. Why: it keeps the toolchain, Bazel's output base and the disk cache between runs, so a job reruns only what changed and installs nothing.
- Keep the repository's fork pull request approval at **Require approval for all external contributors**, and approve a fork's run only after reading every change it makes to `.github/`, `.bazelrc`, `MODULE.bazel`, `BUILD.bazel` files, `tools/` and any test or script it runs. Why: the repository is public, and on a `pull_request` event GitHub runs the workflow files of the pull request itself, so a fork can rewrite `runs-on` back to the self-hosted runner, which runs its code with the host user's files and credentials; the approval is the only lock.
- Run a job that a pull request from a fork can trigger on a GitHub-hosted runner, with `runs-on: ${{ github.event_name == 'pull_request' && github.event.pull_request.head.repo.full_name != github.repository && 'ubuntu-24.04' || fromJSON('["self-hosted","rtl-factory"]') }}`, and keep its tool installs behind `if: runner.environment == 'github-hosted'`. Why: an approved fork run then lands on a runner GitHub throws away; comparing the full name also catches a pull request whose fork was deleted.
- Never use `pull_request_target` or `workflow_run` to run a fork's code. Why: both run with the base repository's secrets and would put that code on whichever runner the job names.
- A step that needs a tool on the self-hosted runner checks it with `tools/toolchain/check.py` and installs only on a miss, unpacking beside the old install and swapping it in once complete; it never uses `sudo`. Why: the runner is a person's machine; a job must not change it beyond the runner user's home. A pull request that changes a pin swaps that install, and the next run on `main` swaps it back.
- Put no secrets in the self-hosted runner's environment beyond what its user already has, and keep the runner registered to this repository only. Why: every job on it runs as that user.

### Steps: set up a local host

1. Install Verilator at the version pinned in `cfg/toolchain.json` (on Arch, `pacman -S verilator` when the package matches the pin; otherwise build the tag per [Toolchain: spec, Install (Verilator, CI)](../tools/toolchain/spec.md)), plus `lz4`.
2. Install the OSS CAD Suite and, for board images, Quartus per [Toolchain: spec](../tools/toolchain/spec.md).
3. Install Bazelisk as `bazel` on `PATH`: download `bazelisk-linux-amd64` from `bazelbuild/bazelisk` releases into `~/.local/bin/bazelisk` and link `~/.local/bin/bazel` to it. It downloads the Bazel pinned in `.bazelversion`.
4. Nothing to install for the nand2doom C-program targets under `bazel test`: Bazel fetches the RV32 GCC pinned in `cfg/toolchain.json` ([toolchain spec](../tools/toolchain/spec.md#install-rv32-gcc)), and it wins over any `N2D_RISCV_*` line in `~/.bazelrc`. Only a direct (non-Bazel) run uses a host toolchain: `riscv64-unknown-elf-gcc` on `PATH`, or `N2D_RISCV_GCC=<path>`, whose sibling binutils (`objcopy`, `readelf`, `nm`, `objdump`) are found by prefix.
5. Run `python3 tools/toolchain/check.py` until it prints READY, then `bazel test --config=l0 //...`.

### Steps: install the self-hosted runner

On a host set up per the steps above, as the user that owns the toolchain:

1. Get a registration token (repo admin): `gh api -X POST repos/rtl-factory/factory/actions/runners/registration-token --jq .token`.
2. Download the latest `actions-runner-linux-x64-<version>.tar.gz` from `actions/runner` releases and unpack it into `~/actions-runners/rtl-factory/`.
3. Register it: `./config.sh --unattended --url https://github.com/rtl-factory/factory --token <token> --name <host> --labels rtl-factory --work _work`. Add `quartus` to the labels when Quartus passes the readiness check.
4. Check that `~/actions-runners/rtl-factory/.path` holds the `PATH` the checks need (it is the shell's `PATH` at registration, including `~/.local/bin` for Bazelisk).
5. Install it as a systemd user service that starts at boot without a login: write `~/.config/systemd/user/actions-runner-rtl-factory.service` with `WorkingDirectory=%h/actions-runners/rtl-factory`, `ExecStart=%h/actions-runners/rtl-factory/run.sh`, `KillMode=process`, `Restart=always`, `RestartSec=15`, `StartLimitIntervalSec=0` (under `[Unit]`) and `WantedBy=default.target`; run `loginctl enable-linger $USER` (once per host) and `systemctl --user enable --now actions-runner-rtl-factory`. Why a user service and not `svc.sh`: it needs no root, and the runner keeps the toolchain user's home. Why no start limit: a burst of quick failures, such as no network at boot, would otherwise leave the service failed for good.
6. Check that the runner is online: `gh api repos/rtl-factory/factory/actions/runners --jq '.runners[] | {name, status}'`. Its log is `journalctl --user -u actions-runner-rtl-factory`.

To remove a runner: `systemctl --user disable --now actions-runner-rtl-factory`, then `./config.sh remove --token <token>` with a token from `gh api -X POST repos/rtl-factory/factory/actions/runners/remove-token --jq .token`.

### Exit criteria

- The runner shows `online`; every workflow names `[self-hosted, rtl-factory]` or the fork expression above; the fork pull request approval is set to all external contributors.

## Build

How to build and check anything in the factory with Bazel: run a regression level, run one target, add a target, read a failure, keep the cache honest. How the build works inside is the [Bazel spec](../tools/bazel/spec.md); which level runs when is [CI levels](#ci-levels).

### Rules

- Run every check through Bazel: `bazel test --config=l0 //...` before every push, `--config=l<N>` for level N and the levels below it. Never run `tools/sim/sim.py test`, `tools/fv/fv.py run`, `pytest`, the comment lint or the RTL lint directly to prove a change. Why: Bazel runs what CI runs, in parallel, and reruns only what the change touched.
- Run one target by name: `bazel test //src/<project>:<target>` (simulation), `//src/<project>:<target>.formal` (formal), `<owner>.unit` in the package that holds the `tests/` folder, such as `//tools/sim:sim.unit` (unit tests), `//src/<project>:<top>.lint` (Verilator lint of one top), `//tools:comment_lint`, `//tools:rtl_lint`, `//tools:wiki_refs`. List them with `bazel query 'tests(//src/<project>:all)'`, `bazel query 'attr(tags, unit, tests(//...))'` or `bazel query 'attr(tags, lint, tests(//...))'`. Why: one target answers a question in seconds once its model is cached.
- Declare a simulation target only in `src/<project>/cfg/targets.json`, a formal target only as its `.sby` file with its `# level:` and `# timeout:` header, unit tests only as a `tests/` folder in a package that calls `unit_tests()`, and a lint top only by its library (every design root and common block is one; a vendor primitive's stand-in it needs goes in the `lint` object of `src/<project>/cfg/targets.json`); never write a Bazel target for them by hand. Why: the registry is the one list of targets, and Bazel reads it ([Bazel spec](../tools/bazel/spec.md)).
- Name a target's libraries, never its files: a simulation target its top (and `libraries`, `flavors` when it needs more), a formal target `# library:`, a board manifest `libraries`. After adding, renaming or moving an HDL file, or a new instance, package or include, run `python3 tools/bazel/hdl_libraries.py update` and commit the changed `libraries.json`. Keep `` `include `` files under `src/<project>/` with an `.svh` or `.vh` extension. Why: each design's files are declared once and derived from the HDL ([Bazel spec, Libraries](../tools/bazel/spec.md#libraries)); Bazel builds each model from those files only, so a stale list breaks the sandboxed build instead of leaving a stale cached result.
- A test reads its data through paths inside the repository and writes only to `$TEST_UNDECLARED_OUTPUTS_DIR` or `$TEST_TMPDIR` (or the runner's work folder). Two tests that may run at once never write the same file; write a unique temporary file and rename it into place. Why: a test that reads a file Bazel did not declare can pass from the cache after that file changed, and parallel tests that share a file fail at random.
- Never pass `--nocache_test_results`, delete the disk cache or run `bazel clean` to make a failure go away (expunging a finished worktree's output base at [clean-up](#steps) is not that); find the input the cache missed and declare it. Why: a cache that needs clearing is a missing input, and clearing it hides the bug for the next agent.
- Change a pin (`.bazelversion`, `MODULE.bazel`, `tools/sim/requirements*.txt`, `cfg/toolchain.json`) only through its own issue, and regenerate `tools/sim/requirements_lock.txt` with the command in its header. A `rv32_gcc` bump also re-derives the Doom input-witness seams (`SEAMS` in `src/nand2doom/dv/software/input_witness.py`) from the new image's disassembly. Why: a pin change reruns everything and can change every result.
- Put machine-only Bazel settings (a registry mirror, `--action_env=QUARTUS_SH`, cache paths) in `user.bazelrc` or `~/.bazelrc`, and Bazelisk's own settings (`BAZELISK_BASE_URL`) in the environment or `~/.bazeliskrc`; never in `.bazelrc`. Why: `.bazelrc` is what CI and every agent share, and Bazelisk reads no `.bazelrc`.
- Turn on the shared remote cache only with `--config=remote-cache` and the bucket and key in `user.bazelrc` or `~/.bazelrc`; never commit a bucket URL or key, and keep the key file readable by its user only. Why: the key can write the bucket, and a poisoned cache feeds every machine ([Bazel spec, Remote cache](../tools/bazel/spec.md#remote-cache)).
- State in a pull request's Evidence the Bazel command you ran, its `Executed N out of M tests` line and its failures. Why: the reviewer checks the same command, and the line shows what was cached.

### Steps: check a change

1. Run `bazel test --config=l0 //...`. Bazel prints one line per failing test and the path of its `test.log`.
2. For each failure, read its `test.log` (`bazel-testlogs/<package>/<target>/test.log`); the runner prints the failing target's logs into it. The full logs, `results.xml`, waveforms and formal traces are in `bazel-testlogs/<package>/<target>/test.outputs/`; a target tagged `wrapped` (one that builds a program first) writes them under `workdir/bazel/<target>/` instead.
3. Fix per [debugging](debug.md), then rerun only the failing targets by name until they pass.
4. Rerun `bazel test --config=l0 //...` once at the end; every unchanged simulation and formal target reports cached; wrapped targets, unit tests and the lint always rerun.

### Steps: debug one simulation target

1. Rerun it with another seed: `bazel test //src/<project>:<target> --test_env=SIM_SEED=<n>`. The seed is printed in the test's log.
2. Get waveforms: `bazel test //src/<project>:<target> --//tools/bazel:waves`. This rebuilds the target's model with tracing; the waveform is under `test.outputs/` (`workdir/bazel/<target>/` for a wrapped target).
3. When the model fails to build, read the build log in Bazel's error output, or rebuild it alone with `bazel build //src/<project>:<target>.model` (targets that share a model name the first of them; a wrapped target builds its own model and has no `.model` target).

### Steps: add a target

1. Simulation: add the entry to `src/<project>/cfg/targets.json` per the [simulation runner spec](../tools/sim/spec.md), with its `level`. Formal: add `src/<project>/fv/<block>/<target>.sby` per [formal](fv.md). Unit tests: add a `tests/` folder beside the script.
2. Check the new test is listed with the level you meant: `bazel query 'attr(tags, L0, tests(//src/<project>:all))'`.
3. Run it by name, then L0.

### Exit criteria

- `bazel test --config=l0 //...` passes, and the pull request's Evidence names the command and its `Executed` line.
- No new target is written by hand in a `BUILD.bazel` file, and no check was run around Bazel.

## GitHub settings

The repository settings that enforce the flow. A repo admin applies them; agents cannot.

### Steps

1. **Ruleset for `main`.** Settings → Rules → Rulesets → New branch ruleset; target the default branch; enforcement **Active**; no bypass list:
   - **Restrict deletions** and **Block force pushes**.
   - **Require a pull request before merging**, with required approvals **0** and allowed merge methods **Squash** and **Merge** (not Rebase). Why: approval is a flow step, not a GitHub review (ADR 0007); merge methods follow ADR 0008.
   - **Require status checks to pass**: `issue-link`, and the L1 job once it exists. Require branches to be up to date.
2. **Ruleset for epic branches.** Target `epic/*`; enforcement **Active**; require a pull request, allowed merge method **Squash** only, and require `issue-link` and `l0` (the L0 job, [CI levels](#ci-levels)).
3. **Labels.** Delete labels that `.github/labels.json` no longer lists (renamed `area:syn` → `area:fpga`: move issues to the new label first). Run the `label-sync` workflow once (Actions → label-sync → Run workflow). It reruns on every change to `.github/labels.json` merged into `main`.
4. **Wiki site.** Settings → Pages → Build and deployment → Source: **GitHub Actions**. Then run the `pages` workflow once (Actions → pages → Run workflow); it reruns on every merge to `main` that touches the wiki. The site is at `https://rtl-factory.github.io/factory/`.
5. **Dashboard.** Create a GitHub Project for the repo with views per [labels](issues.md#labels).
6. **Actions.** Settings → Actions → General → Fork pull request workflows from outside collaborators: **Require approval for all external contributors**. Register the self-hosted runner per [Runners](#runners).

`.github/CODEOWNERS` lists the repo admins so GitHub requests their review; their review is not required.

### Exit criteria

- Both rulesets are active, the labels exist, the wiki site is live, and the Project board shows open issues.
