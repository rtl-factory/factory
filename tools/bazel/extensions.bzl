"""Repositories the factory's HDL rules need (wiki/tools/bazel/spec.md).

hdl_registry: the simulation targets of every src/<project>/cfg/targets.json and
the formal targets of every src/<project>/fv/<block>/<target>.sby and the lint
tops derived from every src/<project>/cfg/libraries.json, with their libraries
resolved into files by tools/bazel/hdl_libraries.py, as Starlark data the project
packages turn into Bazel targets.
verilator: the Verilator install the toolchain pinned (cfg/toolchain.json),
found on PATH or under ~/verilator, version-checked and fingerprinted.
oss_cad_suite: the OSS CAD Suite install the toolchain pinned, found under its
install directory, release-checked.
rv32_gcc: the RV32 GCC the toolchain pinned, downloaded and checksum-checked.
"""

def _registry_impl(rctx):
    root = rctx.workspace_root
    script = root.get_child("tools").get_child("bazel").get_child("hdl_libraries.py")
    python = rctx.which("python3")
    if not python:
        fail("python3 not found: the HDL registry is read with the host's python3")
    result = rctx.execute([python, "-I", script, "--root", root, "registry"], quiet = True)
    if result.return_code != 0:
        fail("reading the HDL registry failed:\n" + result.stderr)
    registry = json.decode(result.stdout)
    # Why: a changed registry, library list, .sby file or resolver must regenerate the targets.
    for path in registry["inputs"] + ["tools/bazel/hdl_libraries.py"]:
        rctx.watch(root.get_child(path))
    # Why: a new project, registry or .sby file shows up as a changed folder listing.
    for project in root.get_child("src").readdir(watch = "yes"):
        for folder in ["cfg", "fv"]:
            if project.get_child(folder).exists:
                project.get_child(folder).readdir(watch = "yes")
        if project.get_child("fv").exists:
            for block in project.get_child("fv").readdir(watch = "yes"):
                if block.is_dir:
                    block.readdir(watch = "yes")
    rctx.file("BUILD.bazel", "")
    rctx.file("targets.bzl", "SIM_TARGETS = json.decode(%r)\nFV_TARGETS = json.decode(%r)\nLINT_TARGETS = json.decode(%r)\n" %
                             (json.encode(registry["sim"]), json.encode(registry["formal"]), json.encode(registry["lint"])))

hdl_registry = repository_rule(implementation = _registry_impl)

# verilator_coverage is a Perl wrapper that runs the binary beside it.
COVERAGE_TOOLS = ["verilator_coverage", "verilator_coverage_bin_dbg"]

def _find_verilator(rctx):
    explicit = rctx.getenv("VERILATOR")
    if explicit:
        return rctx.path(explicit)
    on_path = rctx.which("verilator")
    if on_path:
        return on_path
    home = rctx.getenv("HOME")
    fallback = rctx.path(home + "/verilator/bin/verilator") if home else None
    if fallback and fallback.exists:
        return fallback
    fail("verilator not found: install the version pinned in cfg/toolchain.json " +
         "(wiki/tools/toolchain/spec.md), put it on PATH or set VERILATOR")

def _verilator_impl(rctx):
    pin = json.decode(rctx.read(rctx.attr.toolchain))["verilator"]["version"]
    exe = _find_verilator(rctx)
    # Why: a rebuilt or replaced binary at the same path must be checked again.
    rctx.watch(exe.realpath)
    bindir = exe.realpath.dirname
    if bindir.get_child("verilator_bin").exists:
        rctx.watch(bindir.get_child("verilator_bin"))
    version = rctx.execute([exe, "--version"]).stdout.strip()
    if not version.startswith("Verilator %s " % pin) and version != "Verilator " + pin:
        fail("%s is '%s'; cfg/toolchain.json pins Verilator %s" % (exe, version, pin))
    for tool in ["verilator", "verilator_bin", "verilator_includer"] + COVERAGE_TOOLS:
        if bindir.get_child(tool).exists:
            rctx.symlink(bindir.get_child(tool), "bin/" + tool)
    rctx.file("BUILD.bazel", """\
# Verilator {version} from {bindir}; its files are action inputs, so a different
# binary invalidates every cached model.
filegroup(
    name = "verilator",
    srcs = glob(["bin/*"], exclude = {coverage}),
    visibility = ["//visibility:public"],
)

# The coverage report tool, an input of tests only, so it never rebuilds a model.
filegroup(
    name = "verilator_coverage",
    srcs = glob({coverage}),
    visibility = ["//visibility:public"],
)
""".format(version = version, bindir = bindir, coverage = ["bin/" + t for t in COVERAGE_TOOLS]))

verilator_repository = repository_rule(
    implementation = _verilator_impl,
    attrs = {"toolchain": attr.label(default = "//cfg:toolchain.json")},
    environ = ["PATH", "VERILATOR", "HOME"],
)

def _oss_cad_suite_impl(rctx):
    pin = json.decode(rctx.read(rctx.attr.toolchain))["oss_cad_suite"]
    home = rctx.getenv("HOME") or ""
    install = rctx.getenv("OSS_CAD_SUITE") or pin["install_dir"].replace("~", home, 1)
    root = rctx.path(install)
    stamp = root.get_child("VERSION")
    # Why: installing the suite after a first fetch must refetch, or the stand-in sby stays.
    rctx.watch(stamp)
    want = pin["version"].replace("-", "")
    if stamp.exists and rctx.read(stamp).strip() == want:
        # Why: the suite's wrappers find their install through their own folder, so the
        # whole bin/ folder is linked, not single files.
        rctx.symlink(root.get_child("bin"), "bin")
        rctx.symlink(stamp, "VERSION")
    else:
        # Why: a host without the suite still builds and runs every simulation; only its
        # formal tests fail, each with the fix.
        message = "OSS CAD Suite %s is not installed in %s: install it per wiki/tools/toolchain/spec.md" % (
            pin["version"],
            root,
        )
        rctx.file("bin/sby", "#!/bin/sh\necho '%s' >&2\nexit 1\n" % message, executable = True)
        rctx.file("VERSION", "missing\n")
    rctx.file("BUILD.bazel", """\
# OSS CAD Suite {version} from {root}; its release stamp is an input of every
# formal test, so another release reruns them.
exports_files(["bin/sby"], visibility = ["//visibility:public"])

filegroup(
    name = "oss_cad_suite",
    srcs = ["VERSION", "bin/sby"],
    visibility = ["//visibility:public"],
)
""".format(version = pin["version"], root = root))

oss_cad_suite_repository = repository_rule(
    implementation = _oss_cad_suite_impl,
    attrs = {"toolchain": attr.label(default = "//cfg:toolchain.json")},
    environ = ["OSS_CAD_SUITE", "HOME"],
)

def _rv32_gcc_impl(rctx):
    pin = json.decode(rctx.read(rctx.attr.toolchain))["rv32_gcc"]
    rctx.download_and_extract(
        url = pin["url"],
        sha256 = pin["sha256"],
        stripPrefix = pin["strip_prefix"],
    )
    rctx.file("BUILD.bazel", """\
# RV32 GCC {version} ({url}); every file is an input of the C-program tests, so
# another pin reruns them.
filegroup(
    name = "all",
    srcs = glob(["**"], exclude = ["BUILD.bazel"]),
    visibility = ["//visibility:public"],
)

filegroup(
    name = "gcc",
    srcs = ["bin/{prefix}gcc"],
    visibility = ["//visibility:public"],
)
""".format(version = pin["version"], url = pin["url"], prefix = pin["prefix"]))

rv32_gcc_repository = repository_rule(
    implementation = _rv32_gcc_impl,
    attrs = {"toolchain": attr.label(default = "//cfg:toolchain.json")},
)

def _hdl_impl(mctx):
    hdl_registry(name = "hdl_registry")
    verilator_repository(name = "verilator")
    oss_cad_suite_repository(name = "oss_cad_suite")
    rv32_gcc_repository(name = "rv32_gcc")
    return mctx.extension_metadata(reproducible = False)

hdl = module_extension(implementation = _hdl_impl)
