#!/usr/bin/env python3
"""Build the deterministic nand2doom freestanding RV32IM validation image.

Spec: wiki/src/nand2doom/scripts/sw_build/spec.md#validation-image.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys


ARCH = "rv32im"
ABI = "ilp32"
ENTRY_ADDRESS = 0
MEMORY_MAP_REVISION = "n2d-platform-v1"
COMPLETION_ADDRESS = 0x00004000
COMPLETION_MARKER = 0xD00D0035


class ImageBuildError(Exception):
    """An actionable image-build failure."""


def repository_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _requested_tool(environment_name: str, default: str) -> str:
    """The tool named by environment_name, else a sibling of N2D_RISCV_GCC, else default.

    Why: a host whose RV32 toolchain is off Bazel's PATH sets only N2D_RISCV_GCC
    (wiki/bkm/flow.md, Runners); each binutil sits beside it with the same prefix.
    """
    requested = os.environ.get(environment_name)
    if requested:
        return requested
    gcc = os.environ.get("N2D_RISCV_GCC", "")
    if environment_name.startswith("N2D_RISCV_") and environment_name != "N2D_RISCV_GCC" \
            and gcc.endswith("gcc"):
        return gcc[:-len("gcc")] + environment_name[len("N2D_RISCV_"):].lower()
    return default


def _tool(environment_name: str, default: str) -> str:
    requested = _requested_tool(environment_name, default)
    resolved = shutil.which(requested)
    if resolved is None:
        raise ImageBuildError(
            f"required tool '{requested}' was not found; set {environment_name} "
            f"or add the RV32-capable GNU toolchain to PATH"
        )
    return resolved


def _scratch_roots(root: Path) -> list[Path]:
    """The repository workdir, plus the scratch directories of the Bazel test running now."""
    workdir = root / "workdir"
    if workdir.is_symlink():
        raise ImageBuildError("repository workdir must not be a symbolic link")
    bazel = [Path(os.environ[name]) for name in ("TEST_UNDECLARED_OUTPUTS_DIR", "TEST_TMPDIR")
             if os.environ.get(name)]
    return [workdir] + bazel


def scratch_path(relative: str, root: Path | None = None, *, keep: bool = True) -> Path:
    """Where a test or build writes relative: under bazel test, the undeclared outputs
    for evidence to keep (keep=True) or the test's temporary folder for build trees;
    otherwise the repository workdir.

    Why: Bazel's sandbox mounts the checkout read-only (wiki/bkm/flow.md, Build), and
    undeclared outputs are kept in bazel-testlogs and the disk cache.
    """
    outputs = os.environ.get("TEST_UNDECLARED_OUTPUTS_DIR" if keep else "TEST_TMPDIR")
    if outputs:
        return Path(outputs) / relative
    return (root or repository_root()) / "workdir" / relative


def _confined_output(output: Path, root: Path) -> Path:
    resolved = output.resolve()
    for workdir in _scratch_roots(root):
        try:
            relative = resolved.relative_to(workdir.resolve())
            break
        except ValueError:
            continue
    else:
        raise ImageBuildError(
            f"output directory escapes repository workdir: {output}"
        )
    component = workdir
    for part in relative.parts:
        component /= part
        if component.is_symlink():
            raise ImageBuildError(
                f"output directory contains a symbolic-link component: {component}"
            )
    return resolved


def compiler_flags(root: Path, output: Path) -> list[str]:
    return [
        "-march=rv32im",
        "-mabi=ilp32",
        "-O2",
        "-g0",
        "-ffreestanding",
        "-fno-builtin",
        "-fno-pic",
        "-fno-stack-protector",
        "-msmall-data-limit=0",
        "-mno-relax",
    ]


def _output_spelling(output: Path, root: Path) -> Path:
    """The output as the commands name it: repository-relative, or absolute when it
    lies in the Bazel test's scratch outside the checkout (_confined_output)."""
    resolved = output.resolve()
    try:
        return resolved.relative_to(root.resolve())
    except ValueError:
        return resolved


def image_commands(
    root: Path, output: Path, gcc: str, objcopy: str
) -> tuple[tuple[str, ...], ...]:
    source = Path("src/nand2doom/sw/runtime")
    output_path = _output_spelling(output, root)
    common = compiler_flags(root, output)
    objects = tuple(
        output_path / name for name in ("crt0.o", "runtime.o", "validation.o")
    )
    compile_commands = tuple(
        (
            gcc,
            *common,
            "-c",
            str(source / source_name),
            "-o",
            str(object_path),
        )
        for source_name, object_path in zip(
            ("crt0.S", "runtime.c", "validation.c"), objects, strict=True
        )
    )
    link_command = (
        gcc,
        "-march=rv32im",
        "-mabi=ilp32",
        "-nostdlib",
        "-nostartfiles",
        "-mno-relax",
        f"-Wl,-T,{source / 'n2d_runtime.ld'}",
        "-Wl,--build-id=none",
        "-Wl,--gc-sections",
        f"-Wl,-Map,{output_path / 'n2d_validation.map'}",
        *(str(path) for path in objects),
        "-o",
        str(output_path / "n2d_validation.elf"),
    )
    objcopy_command = (
        objcopy,
        "-O",
        "binary",
        str(output_path / "n2d_validation.elf"),
        str(output_path / "n2d_validation.bin"),
    )
    return (*compile_commands, link_command, objcopy_command)


def _version(executable: str) -> str:
    result = subprocess.run(
        [executable, "--version"], check=True, capture_output=True, text=True
    )
    return result.stdout.splitlines()[0]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalize_map(map_file: Path, output: Path, root: Path) -> None:
    """Replace the caller-selected output prefix with one stable map token."""

    output_prefix = _output_spelling(output, root).as_posix()
    text = map_file.read_text(encoding="utf-8")
    normalized = text.replace(output_prefix, "${OUTPUT}")
    map_file.write_text(normalized, encoding="utf-8")


def build_image(output: Path, root: Path | None = None) -> dict[str, object]:
    root = (root or repository_root()).resolve()
    output = _confined_output(output, root)
    gcc = _tool("N2D_RISCV_GCC", "riscv64-unknown-elf-gcc")
    objcopy = _tool("N2D_RISCV_OBJCOPY", "riscv64-unknown-elf-objcopy")
    readelf = _tool("N2D_RISCV_READELF", "riscv64-unknown-elf-readelf")
    output.mkdir(parents=True, exist_ok=True)

    elf = output / "n2d_validation.elf"
    image = output / "n2d_validation.bin"
    map_file = output / "n2d_validation.map"
    for path in (elf, image, map_file, output / "manifest.json"):
        if path.exists():
            if path.is_symlink() or not path.is_file():
                raise ImageBuildError(f"generated path is not a regular file: {path}")
            path.unlink()

    commands = image_commands(root, output, gcc, objcopy)
    for command in commands:
        subprocess.run(command, cwd=root, check=True)
    _normalize_map(map_file, output, root)
    attributes = subprocess.run(
        [readelf, "-h", str(elf)], cwd=root, check=True, capture_output=True, text=True
    ).stdout
    if re.search(r"^\s*Class:\s+ELF32\s*$", attributes, re.MULTILINE) is None:
        raise ImageBuildError("compiler did not produce an ELF32 image")
    if re.search(r"^\s*Machine:\s+RISC-V\s*$", attributes, re.MULTILINE) is None:
        raise ImageBuildError("compiler did not produce a RISC-V image")

    data = image.read_bytes()
    if not data:
        raise ImageBuildError("generated image is empty")
    if len(data) % 4:
        data += bytes(4 - (len(data) % 4))
        image.write_bytes(data)

    manifest: dict[str, object] = {
        "schema_version": 1,
        "architecture": ARCH,
        "abi": ABI,
        "endianness": "little",
        "entry_address": ENTRY_ADDRESS,
        "memory_map_revision": MEMORY_MAP_REVISION,
        "completion": {
            "address": COMPLETION_ADDRESS,
            "marker": COMPLETION_MARKER,
        },
        "segments": [
            {
                "address": 0,
                "file": image.name,
                "byte_length": len(data),
                "sha256": _sha256(image),
            }
        ],
        "artifacts": {
            elf.name: _sha256(elf),
            map_file.name: _sha256(map_file),
        },
        "toolchain": {
            "compiler": _version(gcc),
            "objcopy": _version(objcopy),
        },
        "compiler_contract": [
            "-march=rv32im",
            "-mabi=ilp32",
            "-ffreestanding",
            "-nostdlib",
            "-nostartfiles",
        ],
    }
    manifest_path = output / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a deterministic freestanding nand2doom RV32IM image."
    )
    parser.add_argument(
        "--output",
        default="workdir/software/n2d_validation",
        help="output directory below this checkout's workdir/",
    )
    parser.add_argument(
        "--plan", action="store_true", help="print commands without creating output"
    )
    return parser


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    """The command line; root is the checkout whose workdir/ takes the output (default: this one)."""
    args = create_parser().parse_args(argv)
    root = repository_root() if root is None else root
    output = Path(args.output)
    if not output.is_absolute():
        output = root / output
    try:
        output = _confined_output(output, root)
        gcc = _requested_tool("N2D_RISCV_GCC", "riscv64-unknown-elf-gcc")
        objcopy = _requested_tool("N2D_RISCV_OBJCOPY", "riscv64-unknown-elf-objcopy")
        print(f"architecture: {ARCH}")
        print(f"abi: {ABI}")
        print(f"output: {output.relative_to(root)}")
        for command in image_commands(root, output, gcc, objcopy):
            print(f"$ {shlex.join(command)}")
        if args.plan:
            return 0
        manifest = build_image(output, root)
        print(
            "image: bytes={byte_length} sha256={sha256}".format(
                **manifest["segments"][0]  # type: ignore[index]
            )
        )
        print(f"manifest: {(output / 'manifest.json').relative_to(root)}")
        return 0
    except (ImageBuildError, OSError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
