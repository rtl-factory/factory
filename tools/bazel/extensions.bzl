"""Repositories the factory's HDL rules need (wiki/tools/bazel/spec.md).

hdl_registry: the simulation targets of every src/<project>/cfg/targets.json and
the formal targets of every src/<project>/fv/<block>/<target>.sby, as Starlark
data the project packages turn into Bazel targets.
verilator: the Verilator install the toolchain pinned (cfg/toolchain.json),
found on PATH or under ~/verilator, version-checked and fingerprinted.
oss_cad_suite: the OSS CAD Suite install the toolchain pinned, found under its
install directory, release-checked.
"""

def _normalize(path):
    parts = []
    for part in path.split("/"):
        if part == "..":
            parts.pop()
        elif part and part != ".":
            parts.append(part)
    return "/".join(parts)

def _parse_sby(text, folder):
    """The runner's header fields and the repository paths the [files] section copies."""
    fields = {"level": None, "timeout_seconds": 600}
    files = []
    section = None
    for line in text.splitlines():
        stripped = line.strip()
        if section == None and stripped.startswith("#"):
            key, _, value = stripped.lstrip("#").partition(":")
            if key.strip() == "level":
                fields["level"] = value.strip()
            elif key.strip() == "timeout":
                fields["timeout_seconds"] = int(value.strip())
        elif stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].strip()
        elif section == "files" and stripped and not stripped.startswith("#"):
            words = stripped.replace("\t", " ").split(" ")
            files.append(_normalize(folder + "/" + words[-1]))
    return dict(fields, files = files)

def _registry_impl(rctx):
    src = rctx.workspace_root.get_child("src")
    sim, formal = {}, {}
    for project in sorted(src.readdir(), key = lambda p: p.basename):
        name = project.basename
        registry = project.get_child("cfg").get_child("targets.json")
        if registry.exists:
            sim[name] = json.decode(rctx.read(registry))["targets"]
        fv = project.get_child("fv")
        if not fv.exists:
            continue
        for block in sorted(fv.readdir(), key = lambda p: p.basename):
            if not block.is_dir:
                continue
            for sby in sorted(block.readdir(), key = lambda p: p.basename):
                if not sby.basename.endswith(".sby"):
                    continue
                folder = "src/%s/fv/%s" % (name, block.basename)
                target = _parse_sby(rctx.read(sby), folder)
                target["sby"] = folder + "/" + sby.basename
                formal.setdefault(name, {})[sby.basename[:-len(".sby")]] = target
    rctx.file("BUILD.bazel", "")
    rctx.file("targets.bzl", "SIM_TARGETS = json.decode(%r)\nFV_TARGETS = json.decode(%r)\n" %
                             (json.encode(sim), json.encode(formal)))

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
    # Why: a rebuilt or replaced binary at the same path must be checked again.
    rctx.watch(exe.realpath)
    bindir = exe.realpath.dirname
    if bindir.get_child("verilator_bin").exists:
        rctx.watch(bindir.get_child("verilator_bin"))
    version = rctx.execute([exe, "--version"]).stdout.strip()
    if not version.startswith("Verilator %s " % pin) and version != "Verilator " + pin:
        fail("%s is '%s'; cfg/toolchain.json pins Verilator %s" % (exe, version, pin))
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

def _oss_cad_suite_impl(rctx):
    pin = json.decode(rctx.read(rctx.attr.toolchain))["oss_cad_suite"]
    home = rctx.getenv("HOME") or ""
    install = rctx.getenv("OSS_CAD_SUITE") or pin["install_dir"].replace("~", home, 1)
    root = rctx.path(install)
    stamp = root.get_child("VERSION")
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

def _hdl_impl(mctx):
    hdl_registry(name = "hdl_registry")
    verilator_repository(name = "verilator")
    oss_cad_suite_repository(name = "oss_cad_suite")
    return mctx.extension_metadata(reproducible = False)

hdl = module_extension(implementation = _hdl_impl)
