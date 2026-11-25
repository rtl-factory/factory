"""Repositories the factory's HDL rules need (wiki/tools/bazel/spec.md).

hdl_registry: the simulation targets of every src/<project>/cfg/targets.json and
the formal targets of every src/<project>/fv/<block>/<target>.sby and the lint
tops derived from every src/<project>/cfg/libraries.json, with their libraries
resolved into files by tools/bazel/hdl_libraries.py, as Starlark data the project
packages turn into Bazel targets.
verilator: the Verilator the toolchain pinned (cfg/toolchain.json), the one inside the
OSS CAD Suite install, version-checked and fingerprinted.
oss_cad_suite: the OSS CAD Suite install the toolchain pinned, found under its
install directory, release-checked.
rv32_gcc: the RV32 GCC the toolchain pinned, downloaded and checksum-checked.
host_abi: the host's OS, glibc and C++ compiler as one string, the exec property that
keeps models built on different hosts apart in a shared cache.
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

def _suite_root(rctx, toolchain):
    home = rctx.getenv("HOME") or ""
    return rctx.path(rctx.getenv("OSS_CAD_SUITE") or toolchain["oss_cad_suite"]["install_dir"].replace("~", home, 1))

def _find_verilator(rctx, suite):
    explicit = rctx.getenv("VERILATOR")
    if explicit:
        return rctx.path(explicit)
    # Why: never PATH, so every host runs the suite's files and cache keys match across hosts.
    exe = suite.get_child("bin").get_child("verilator")
    if exe.exists:
        return exe
    fail("verilator not found in %s: install the pinned OSS CAD Suite, which carries the pinned " % suite +
         "Verilator (wiki/tools/toolchain/spec.md), or set VERILATOR")

def _verilator_impl(rctx):
    toolchain = json.decode(rctx.read(rctx.attr.toolchain))
    pin = toolchain["verilator"]
    suite = _suite_root(rctx, toolchain)
    exe = _find_verilator(rctx, suite)
    bindir = exe.realpath.dirname
    stamp = bindir.dirname.get_child("VERSION")
    # Why: the suite's bin/ holds wrappers that run libexec/; its release stamp names the
    # binaries behind them, and a reinstall at the same path must be checked again.
    for watched in [exe.realpath, stamp]:
        rctx.watch(watched)
    version = rctx.execute([exe, "--version"]).stdout.strip()
    want = "Verilator %s " % pin["version"]
    if not version.startswith(want) or ("rev %s" % pin["revision"]) not in version:
        fail("%s is '%s'; cfg/toolchain.json pins Verilator %s rev %s" % (exe, version, pin["version"], pin["revision"]))
    for tool in ["verilator", "verilator_bin", "verilator_includer"] + COVERAGE_TOOLS:
        if bindir.get_child(tool).exists:
            rctx.symlink(bindir.get_child(tool), "bin/" + tool)
    if stamp.exists:
        rctx.symlink(stamp, "VERSION")
    else:
        rctx.file("VERSION", version + "\n")
    rctx.file("BUILD.bazel", """\
# Verilator {version} from {bindir}; its files and its suite's release stamp are action
# inputs, so another release invalidates every cached model.
filegroup(
    name = "verilator",
    srcs = glob(["bin/*"], exclude = {coverage}) + ["VERSION"],
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
    environ = ["VERILATOR", "OSS_CAD_SUITE", "HOME"],
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

# The PATH every model action runs with (hdl.bzl _model_impl), so the compiler probed
# is the one Verilator's makefile calls.
MODEL_PATH = "/usr/local/bin:/usr/bin:/bin"

def _probe(rctx, argv):
    result = rctx.execute(argv, environment = {"PATH": MODEL_PATH})
    return result.stdout.strip() if result.return_code == 0 else "unknown"

def _os_release(rctx):
    path = rctx.path("/etc/os-release")
    if not path.exists:
        return "unknown"
    fields = {}
    for line in rctx.read(path).splitlines():
        key, _, value = line.partition("=")
        fields[key] = value.strip('"')
    return "%s-%s" % (fields.get("ID", "unknown"), fields.get("VERSION_ID", "rolling"))

def _host_abi_impl(rctx):
    # Why: the host compiler and C++ runtime are not action inputs, and a model built
    # against a newer glibc or libstdc++ fails to load on an older host; the string is
    # part of every model's action key, so a shared cache never crosses hosts.
    glibc = _probe(rctx, ["getconf", "GNU_LIBC_VERSION"]).replace("glibc ", "")
    cxx = _probe(rctx, ["g++", "-dumpfullversion"])
    abi = "%s_glibc-%s_gcc-%s" % (_os_release(rctx), glibc, cxx)
    # Why: watch the g++ the probe ran, the first on MODEL_PATH, so upgrading it refetches.
    watched = ["/etc/os-release"]
    for folder in MODEL_PATH.split(":"):
        if rctx.path(folder + "/g++").exists:
            watched.append(folder + "/g++")
            break
    for tool in watched:
        if rctx.path(tool).exists:
            rctx.watch(rctx.path(tool).realpath)
    rctx.file("BUILD.bazel", "")
    rctx.file("abi.bzl", "HOST_ABI = %r\n" % abi)

host_abi_repository = repository_rule(implementation = _host_abi_impl)

def _hdl_impl(mctx):
    hdl_registry(name = "hdl_registry")
    verilator_repository(name = "verilator")
    oss_cad_suite_repository(name = "oss_cad_suite")
    rv32_gcc_repository(name = "rv32_gcc")
    host_abi_repository(name = "host_abi")
    return mctx.extension_metadata(reproducible = False)

hdl = module_extension(implementation = _hdl_impl)
