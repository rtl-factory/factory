"""Compile the same production clock probe for host and RV32IM verification."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

from src.nand2doom.scripts.n2d_image import _confined_output, _tool, _version
from .clock_vectors import CYCLES_PER_MS, MASK, RUNTIME_CASES, old_clock_fails, traces

ROOT = Path(__file__).resolve().parents[4]
SOURCE = ROOT / "src/nand2doom/dv/software"
PRODUCTION = ROOT / "src/nand2doom/sw/doom"


def write_vectors(output: Path) -> list[int]:
    old_clock_fails()
    rows, samples, expected = [], [], []
    for trace in traces():
        assert trace.elapsed[0] == 0
        assert all(0 <= b - a <= MASK
                   for a, b in zip(trace.elapsed, trace.elapsed[1:]))
        begin = len(samples)
        samples.extend((trace.origin + age) & MASK for age in trace.elapsed)
        ms, remainder = divmod(trace.initial_cycles, CYCLES_PER_MS)
        rows.append(f"{{{trace.origin}u, {ms}u, {remainder}u, {begin}u, {len(samples)}u}}")
        expected.extend(value for pair in trace.expected for value in pair)
    runtime_rows = []
    for origin, age, step, duration in RUNTIME_CASES:
        runtime_rows.append(f"{{{origin}u, {age}u, {step}u, {duration}u}}")
        chunks, remaining = [], duration
        while remaining > 85899:
            chunks.append(85899)
            remaining -= 85899
        chunks.append(remaining)
        intervals = [(chunk * CYCLES_PER_MS + step - 1) // step for chunk in chunks]
        elapsed = (sum(intervals) + len(chunks) - 1) * step
        expected.extend((0, age // CYCLES_PER_MS,
                         ((age + elapsed) // CYCLES_PER_MS) & MASK,
                         sum(intervals) + len(chunks)))
    header = (
        f"#define TRACE_COUNT {len(rows)}u\n"
        f"#define RUNTIME_COUNT {len(runtime_rows)}u\n"
        f"#define RESULT_WORDS {len(expected)}u\n"
        "static const struct { uint32_t origin, milliseconds, remainder, begin, end; } traces[] = {\n"
        + ",\n".join(rows) + "\n};\nstatic const uint32_t samples[] = {\n"
        + ",".join(f"{tick}u" for tick in samples) + "\n};\n"
        "static const struct { uint32_t origin, age, step, duration; } runtime[] = {\n"
        + ",\n".join(runtime_rows) + "\n};\n"
    )
    (output / "vectors.h").write_text(header)
    return expected


def compile_probe(output: Path, *, host: bool = False) -> tuple[Path, list[int]]:
    output = _confined_output(output, ROOT)
    output.mkdir(parents=True, exist_ok=True)
    expected = write_vectors(output)
    gcc = _tool("CC" if host else "N2D_RISCV_GCC",
                "cc" if host else "riscv64-unknown-elf-gcc")
    artifact = output / ("clock_probe.so" if host else "clock_probe.elf")
    flags = ["-Os", "-ffreestanding", "-fno-builtin", "-Wall", "-Wextra", "-Werror",
             "-DN2D_CLOCK_TRACE", "-I", str(PRODUCTION), "-I", str(output)]
    if host:
        flags += ["-DN2D_CLOCK_HOST", "-shared", "-fPIC"]
    else:
        flags += ["-march=rv32im", "-mabi=ilp32", "-fno-pic", "-fno-pie",
                  "-msmall-data-limit=0", "-mno-relax", "-nostdlib", "-nostartfiles",
                  "-Wl,--build-id=none", "-Wl,--fatal-warnings",
                  f"-Wl,-T,{SOURCE / 'clock.ld'}",
                  f"-Wl,-Map,{output / 'clock_probe.map'}", str(SOURCE / "clock_start.S")]
    command = [gcc, *flags, str(SOURCE / "clock_probe.c"),
               str(PRODUCTION / "clock.c"), "-o", str(artifact)]
    subprocess.run(command, check=True, timeout=30)
    if not host:
        binary = output / "clock_probe.bin"
        subprocess.run([_tool("N2D_RISCV_OBJCOPY", "riscv64-unknown-elf-objcopy"),
                        "-O", "binary", str(artifact), str(binary)], check=True, timeout=30)
        artifact = binary
    (output / "manifest.json").write_text(json.dumps({
        "compiler": _version(gcc), "command": command,
        "artifact": artifact.name,
        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "expected": expected,
    }, indent=2) + "\n")
    return artifact, expected
