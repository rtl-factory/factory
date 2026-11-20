from __future__ import annotations

import os
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess


def compile_signature_program() -> tuple[tuple[int, ...], str]:
    """Compile the owned freestanding C source and return little-endian words."""
    words, provenance, _ = compile_c_program("c_signature", "rv32im_signature")
    return words, provenance


def compile_c_program(
    output_name: str,
    program: str,
    include_dirs: tuple[Path, ...] = (),
    *,
    start: str = "rv32im_signature_start",
    optimization: str = "-O1",
    libgcc: bool = False,
) -> tuple[tuple[int, ...], str, Path]:
    """Compile programs/<program>.c with programs/<start>.S and the shared
    linker script (optionally linking libgcc); return little-endian words,
    provenance and the ELF path."""
    compiler = shutil.which(
        os.environ.get("N2D_RISCV_GCC", "riscv64-unknown-elf-gcc")
    )
    objcopy = shutil.which(
        os.environ.get("N2D_RISCV_OBJCOPY", "riscv64-unknown-elf-objcopy")
    )
    if compiler is None or objcopy is None:
        raise RuntimeError(
            "RV32IM C acceptance requires riscv64-unknown-elf-gcc and "
            "riscv64-unknown-elf-objcopy (or N2D_RISCV_GCC/N2D_RISCV_OBJCOPY)"
        )

    source_dir = Path(__file__).resolve().parent / "programs"
    output_dir = Path.cwd() / output_name
    output_dir.mkdir(parents=True, exist_ok=False)
    elf_path = output_dir / f"{program}.elf"
    binary_path = output_dir / f"{program}.bin"
    map_path = output_dir / f"{program}.map"
    command = [
        compiler,
        "-march=rv32im",
        "-mabi=ilp32",
        optimization,
        "-ffreestanding",
        "-fno-builtin",
        "-fno-pic",
        "-mno-relax",
        "-nostdlib",
        "-nostartfiles",
        *(f"-I{path}" for path in include_dirs),
        f"-Wl,-T,{source_dir / 'rv32im_signature.ld'}",
        "-Wl,--build-id=none",
        f"-Wl,-Map,{map_path}",
        str(source_dir / f"{start}.S"),
        str(source_dir / f"{program}.c"),
        *(("-lgcc",) if libgcc else ()),
        "-o",
        str(elf_path),
    ]
    tools = {name: dict(path=path, sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest())
             for name, path in (('gcc', compiler), ('objcopy', objcopy))}
    (output_dir/'tools.json').write_text(json.dumps(tools, indent=2)+'\n')
    for name, argv in (('compile', command),
                       ('objcopy', [objcopy, '-O', 'binary', str(elf_path), str(binary_path)])):
        result = subprocess.run(argv, capture_output=True, text=True, timeout=20)
        (output_dir/(name+'.json')).write_text(json.dumps(dict(
            argv=argv, returncode=result.returncode, stdout=result.stdout,
            stderr=result.stderr), indent=2)+'\n')
        if result.returncode or result.stderr or re.search(r'\b(?:warning|error):', result.stdout, re.I):
            raise RuntimeError(f'C image {name} failed or emitted diagnostics; see {output_dir}')
    version = subprocess.run(
        [compiler, "--version"], check=True, capture_output=True, text=True, timeout=5
    ).stdout.splitlines()[0]
    image = binary_path.read_bytes()
    if not image or len(image) % 4:
        raise AssertionError(f"C image size must be nonzero and word aligned: {len(image)}")
    words = tuple(
        int.from_bytes(image[index : index + 4], "little")
        for index in range(0, len(image), 4)
    )
    return words, f"{version}; {' '.join(command)}", elf_path
