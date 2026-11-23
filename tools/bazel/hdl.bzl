"""Simulation targets as Bazel targets (wiki/tools/bazel/spec.md).

Each project package calls hdl_project(). It turns every target of the project's
src/<project>/cfg/targets.json into:

  <target>          a test, tagged with its regression level (L0..L4, or manual)
  <target>.spec     the target's registry entry, as the runner reads it
  <model>.model     the Verilator model, one action per distinct build; targets
                    that differ only in their test share it, and Bazel caches it
  <model>.model.spec  the model's build fields only (<model>.model.json), so a
                    test-only registry change does not rebuild the model

and every formal target of its src/<project>/fv/<block>/<target>.sby into a test
<target>.formal, whose inputs are the .sby file and the files it copies.

unit_tests() makes one L0 test per tests/ folder of a package: pytest on that folder.

quartus_image() makes a board image target: the Quartus build tool (tools/quartus/) run
on the project's manifest as a local action, with its SOF, gate evidence and reports
as outputs.

A wrapped target (one whose wrapper prepares a program image first) runs through
the runner's own test command instead, without a sandbox and never cached,
until its program builds become Bazel actions.
"""

load("@bazel_skylib//rules:write_file.bzl", "write_file")
load("@hdl_registry//:targets.bzl", "FV_TARGETS", "SIM_TARGETS")
load("@pypi//:requirements.bzl", "requirement")
load("@rules_python//python:py_library.bzl", "py_library")
load("@rules_python//python:py_test.bzl", "py_test")

LEVELS = ["L0", "L1", "L2", "L3", "L4"]
PROJECTS = sorted(SIM_TARGETS.keys())

# The packages every simulation test imports: cocotb, its functional-coverage library
# and the coverage helper (tools/sim/sim_coverage.py).
SIM_PYTHON = [requirement("cocotb"), requirement("cocotb-coverage"), "//tools/sim:sim_coverage"]

# Why: a project's HDL is compiled from its sources; everything else in the tree
# (Python, programs, images, configs) is what a test may read at run time.
HDL_FILES = ["**/*.sv", "**/*.v", "**/*.svh", "**/*.vh"]
HEADERS = ["**/*.svh", "**/*.vh"]

# The registry fields a model build reads (tools/sim/sim.py build_model); the rest
# only matter to the test, so editing them rebuilds no model.
MODEL_FIELDS = ["defines", "kind", "parameters", "public_depth", "sources", "top", "trace_fst"]

def _two_cpus(_os, _inputs):
    return {"cpu": 2, "memory": 2048}

def _model_impl(ctx):
    out = ctx.actions.declare_directory(ctx.label.name)
    args = ctx.actions.args()
    args.add("build-model")
    args.add("--spec", ctx.file.spec)
    args.add("--out", out.path)
    if ctx.attr.waves:
        args.add("--waves")
    if ctx.attr.coverage:
        args.add("--coverage")
    verilator_bin = ctx.files._verilator[0].dirname
    ctx.actions.run(
        executable = ctx.executable._sim,
        arguments = [args],
        inputs = depset(ctx.files.srcs + ctx.files.headers + [ctx.file.spec] + ctx.files._verilator),
        outputs = [out],
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "SIM_BUILD_JOBS": "2",
            "SIM_PATH_PREPEND": verilator_bin,
            "SIM_ROOT": ".",
        },
        mnemonic = "VerilatorModel",
        progress_message = "Verilating %{label}",
        resource_set = _two_cpus,
    )
    return [DefaultInfo(files = depset([out]), runfiles = ctx.runfiles([out]))]

verilator_model = rule(
    implementation = _model_impl,
    doc = "A target's Verilator model, built by tools/sim/sim.py build-model.",
    attrs = {
        "coverage": attr.bool(doc = "Build with Verilator's --coverage."),
        "headers": attr.label_list(allow_files = True, doc = "Include files the sources may `include."),
        "spec": attr.label(allow_single_file = [".json"], mandatory = True),
        "srcs": attr.label_list(allow_files = True, mandatory = True),
        "waves": attr.bool(),
        "_sim": attr.label(default = "//tools/sim:sim", executable = True, cfg = "exec"),
        "_verilator": attr.label(default = "@verilator//:verilator", allow_files = True),
    },
)

def _source_label(path):
    """src/<project>/<rest> as //src/<project>:<rest>."""
    parts = path.split("/")
    if len(parts) < 3 or parts[0] != "src":
        fail("simulation source %s is not under src/<project>/" % path)
    return "//src/%s:%s" % (parts[1], "/".join(parts[2:]))

def _timeout(seconds):
    """The shortest Bazel timeout that leaves the runner's own budget room to fire first."""
    for name, limit in [("short", 60), ("moderate", 300), ("long", 900)]:
        if seconds + 30 <= limit:
            return name
    return "eternal"

def _tags(target):
    level = target.get("level")
    if level == "manual":
        return ["manual"]
    if level not in LEVELS:
        fail("level %s is not one of %s or manual" % (level, LEVELS))
    return [level]

def _model_key(name, target):
    """Targets with equal keys build the same model."""
    if target.get("kind") == "native":
        return name  # Why: a native binary is named after its target
    return json.encode([
        target["top"],
        target["sources"],
        target.get("parameters", {}),
        target.get("public_depth"),
    ])

def hdl_project(name):
    """Every simulation target of src/<name>/cfg/targets.json, plus the project's files.

    Args:
      name: the project, as in src/<name>/.
    """
    native.exports_files(
        native.glob(["**"], exclude = ["BUILD.bazel"]),
        visibility = ["//visibility:public"],
    )
    native.filegroup(
        name = "headers",
        srcs = native.glob(HEADERS, allow_empty = True),
        visibility = ["//visibility:public"],
    )
    native.filegroup(
        name = "all_files",
        # Why: bytecode that running a script leaves behind is no input; it would rerun
        # every action that reads the project.
        srcs = native.glob(["**"], exclude = ["BUILD.bazel", "**/__pycache__/**", "**/*.pyc"]),
        visibility = ["//visibility:public"],
    )
    py_library(
        name = "python",
        srcs = native.glob(["**/*.py"], allow_empty = True),
        data = native.glob(["**"], exclude = HDL_FILES + ["**/*.py", "BUILD.bazel"]),
        imports = ["../.."],
        visibility = ["//visibility:public"],
    )

    # Why: tests import across projects and tools (a common bench driving a project's
    # fixture, a project test using tools.board), so every test gets all the Python.
    python = ["//src/%s:python" % p for p in PROJECTS] + ["//tools:python"]
    headers = [":headers"] if name == "common" else [":headers", "//src/common:headers"]
    _formal_tests(name)
    models = {}
    for target_name, target in sorted(SIM_TARGETS.get(name, {}).items()):
        tags = _tags(target)
        timeout = _timeout(target.get("timeout_seconds", 600))
        if target.get("wrapper"):
            _wrapped_test(target_name, name, tags, timeout, python)
            continue
        write_file(
            name = target_name + ".spec",
            out = target_name + ".spec.json",
            content = [json.encode({"name": target_name, "target": target})],
        )
        key = _model_key(target_name, target)
        if key not in models:
            models[key] = target_name + ".model"
            write_file(
                name = models[key] + ".spec",
                out = models[key] + ".json",
                content = [json.encode({
                    "name": target_name,
                    "target": {k: v for k, v in target.items() if k in MODEL_FIELDS},
                })],
            )
            verilator_model(
                name = models[key],
                spec = models[key] + ".spec",
                srcs = [_source_label(s) for s in target["sources"]],
                headers = headers,
                waves = select({
                    "//tools/bazel:waves_on": True,
                    "//conditions:default": False,
                }),
                coverage = select({
                    "//tools/bazel:coverage_on": True,
                    "//conditions:default": False,
                }),
            )
        model = ":" + models[key]
        py_test(
            name = target_name,
            srcs = ["//tools/sim:sim.py"],
            main = "//tools/sim:sim.py",
            args = [
                "run-model",
                "--spec",
                "$(rootpath :%s.spec)" % target_name,
                "--model",
                "$(rootpath %s)" % model,
            ] + select({
                "//tools/bazel:waves_on": ["--waves"],
                "//conditions:default": [],
            }) + select({
                "//tools/bazel:coverage_on": ["--coverage"],
                "//conditions:default": [],
            }),
            data = [model, ":%s.spec" % target_name, "@verilator//:verilator", "@verilator//:verilator_coverage"],
            deps = python + SIM_PYTHON,
            env = {
                "SIM_PATH_PREPEND": "$(rootpaths @verilator//:verilator)",
                "SIM_ROOT": ".",
            },
            tags = tags,
            timeout = timeout,
        )

def _formal_tests(project):
    for target_name, target in sorted(FV_TARGETS.get(project, {}).items()):
        py_test(
            name = target_name + ".formal",
            srcs = ["//tools/fv:fv.py"],
            main = "//tools/fv:fv.py",
            args = ["run", target_name],
            # Why: the runner resolves the target's library itself, from the same lists.
            data = [_source_label(f) for f in [target["sby"]] + target["files"]] +
                   ["//src/%s:cfg/libraries.json" % p for p in PROJECTS] +
                   ["//tools/bazel:hdl_libraries.py", "@oss_cad_suite//:bin/sby", "@oss_cad_suite//:oss_cad_suite"],
            env = {
                "FV_ROOT": ".",
                "SBY": "$(rootpath @oss_cad_suite//:bin/sby)",
            },
            tags = _tags(target),
            timeout = _timeout(target["timeout_seconds"]),
        )

def _wrapped_test(target_name, project, tags, timeout, python):
    py_test(
        name = target_name,
        srcs = ["//tools/sim:sim.py"],
        main = "//tools/sim:sim.py",
        args = ["--workdir", "workdir/bazel/" + target_name, "test", target_name],
        data = [
            ":all_files",
            "//src/common:all_files",
            "//tools/sim:all_files",
            "//tools/bazel:hdl_libraries.py",
            "@verilator//:verilator",
            "@verilator//:verilator_coverage",
        ],
        deps = python + SIM_PYTHON + [requirement("pillow")],
        env = {"SIM_PATH_PREPEND": "$(rootpaths @verilator//:verilator)"} | select({
            "//tools/bazel:waves_on": {"SIM_WAVES": "1"},
            "//conditions:default": {},
        }) | select({
            "//tools/bazel:coverage_on": {"SIM_COVERAGE": "1"},
            "//conditions:default": {},
        }),
        # Why: the wrapper builds its program and writes under the checkout's workdir/,
        # which a sandbox would forbid and a cache would not see, so it always reruns.
        tags = tags + ["external", "no-sandbox", "no-cache", "wrapped"],
        timeout = "eternal",
    )

def unit_tests(deps = []):
    """One pytest test, `<folder>.unit`, per tests/ folder in this package.

    Args:
      deps: Python libraries the tests import besides every project's and the tools'.
    """
    for folder in native.glob(["**/tests"], exclude_directories = 0, allow_empty = True):
        if not folder.endswith("tests"):
            continue
        owner = folder[:-len("tests")].rstrip("/")
        py_test(
            name = (owner.replace("/", "_") or native.package_name().split("/")[-1]) + ".unit",
            srcs = ["//tools/bazel:pytest_main.py"],
            main = "//tools/bazel:pytest_main.py",
            args = [native.package_name() + "/" + folder],
            data = native.glob([folder + "/**"]) + ["@verilator//:verilator"],
            env = {"UNIT_PATH_PREPEND": "$(rootpaths @verilator//:verilator)"},
            deps = deps + ["//src/%s:python" % p for p in PROJECTS] + ["//tools:python"] + SIM_PYTHON + [
                requirement("pillow"),
                requirement("pytest"),
            ],
            # Why: unit tests read the checkout (registries, wiki pages, programs) and some
            # write its workdir/ or download pinned inputs, so they run outside the sandbox
            # and always run; they take seconds.
            tags = ["L0", "external", "no-sandbox", "unit"],
            timeout = "long",
        )


def _quartus_image_impl(ctx):
    out = ctx.actions.declare_directory(ctx.label.name)
    args = ctx.actions.args()
    args.add("--script", ctx.file._tool)
    args.add("--project", ctx.attr.project)
    args.add("--out", out.path)
    args.add("--")
    args.add_all(ctx.attr.args)
    ctx.actions.run(
        executable = ctx.executable._runner,
        arguments = [args],
        inputs = depset(ctx.files.srcs + ctx.files._tool_files + [ctx.file._tool, ctx.file._toolchain]),
        outputs = [out],
        # Why: Quartus is a licensed, per-host install outside Bazel, and the tool
        # builds in the checkout's workdir/; QUARTUS_SH comes from --action_env (or the pin).
        execution_requirements = {"local": "1", "no-remote": "1", "no-sandbox": "1"},
        use_default_shell_env = True,
        mnemonic = "Quartus",
        progress_message = "Building the board image %{label}",
    )
    return [DefaultInfo(files = depset([out]))]

_quartus_image = rule(
    implementation = _quartus_image_impl,
    attrs = {
        "args": attr.string_list(doc = "Arguments of the Quartus build tool: the manifest, board and profile."),
        "project": attr.string(mandatory = True, doc = "The Quartus project: workdir/quartus/<project>/."),
        "srcs": attr.label_list(allow_files = True, doc = "Every file the build reads."),
        "_runner": attr.label(default = "//tools/bazel:quartus_image", executable = True, cfg = "exec"),
        "_tool": attr.label(default = "//tools:quartus/quartus.py", allow_single_file = True),
        "_tool_files": attr.label(default = "//tools:quartus_files"),
        "_toolchain": attr.label(default = "//cfg:toolchain.json", allow_single_file = True),
    },
)

def quartus_image(name, project, manifest, board = None, profile = None):
    """A board image, built with `bazel build`; never part of //... (needs Quartus).

    Args:
      name: the target, by convention <board>_image.
      project: the Quartus project name the manifest resolves to.
      manifest: the project manifest, relative to the checkout.
      board: a board override of the manifest, when the image is not the manifest's own board.
      profile: a profile that extends the manifest.
    """
    args = ["--manifest", manifest]
    if board:
        args += ["--board", board]
    if profile:
        args += ["--profile", profile]
    _quartus_image(
        name = name,
        project = project,
        args = args,
        # Why: the tool resolves the manifest's libraries from every project's list.
        srcs = [":all_files", "//src/common:all_files", "//tools/bazel:hdl_libraries.py"] +
               ["//src/%s:cfg/libraries.json" % p for p in PROJECTS],
        tags = ["manual", "quartus"],
    )
