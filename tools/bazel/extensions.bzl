"""Repositories the factory's HDL rules need (wiki/tools/bazel/spec.md).

hdl_registry: the simulation targets of every src/<project>/cfg/targets.json, as
Starlark data the project packages turn into Bazel targets.
verilator: the Verilator install the toolchain pinned (cfg/toolchain.json),
found on PATH or under ~/verilator, version-checked and fingerprinted.
"""

def _registry_impl(rctx):
    src = rctx.workspace_root.get_child("src")
    sim = {}
    for project in sorted(src.readdir(), key = lambda p: p.basename):
        registry = project.get_child("cfg").get_child("targets.json")
        if registry.exists:
            sim[project.basename] = json.decode(rctx.read(registry))["targets"]
    rctx.file("BUILD.bazel", "")
    rctx.file("targets.bzl", "SIM_TARGETS = json.decode(%r)\n" % json.encode(sim))

hdl_registry = repository_rule(implementation = _registry_impl)

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
    version = rctx.execute([exe, "--version"]).stdout.strip()
    if not version.startswith("Verilator %s " % pin) and version != "Verilator " + pin:
        fail("%s is '%s'; cfg/toolchain.json pins Verilator %s" % (exe, version, pin))
    bindir = exe.realpath.dirname
    for tool in ["verilator", "verilator_bin", "verilator_includer"]:
        if bindir.get_child(tool).exists:
            rctx.symlink(bindir.get_child(tool), "bin/" + tool)
    rctx.file("BUILD.bazel", """\
# Verilator {version} from {bindir}; its files are action inputs, so a different
# binary invalidates every cached model.
filegroup(
    name = "verilator",
    srcs = glob(["bin/*"]),
    visibility = ["//visibility:public"],
)
""".format(version = version, bindir = bindir))

verilator_repository = repository_rule(
    implementation = _verilator_impl,
    attrs = {"toolchain": attr.label(default = "//cfg:toolchain.json")},
    environ = ["PATH", "VERILATOR", "HOME"],
)

def _hdl_impl(mctx):
    hdl_registry(name = "hdl_registry")
    verilator_repository(name = "verilator")
    return mctx.extension_metadata(reproducible = False)

hdl = module_extension(implementation = _hdl_impl)
