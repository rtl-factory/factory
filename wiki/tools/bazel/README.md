# Bazel build

The factory's build system: every simulation target is a Bazel test, every Verilator model a cached build action. Code: `tools/bazel/`, with the module at the repository root (`MODULE.bazel`, `.bazelrc`, `.bazelversion`) and one `BUILD.bazel` per project.

- [Requirements](prd.md)
- [Spec](spec.md)
