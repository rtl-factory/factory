#!/usr/bin/env python3
"""Reproduce/check the independent half oracle from hash-pinned full baseline pixels.

Expected bytes come only from --baseline, verified against render_oracle.json.
--candidate checks its raw report and all six projected images. No pin updater.

Spec: wiki/src/nand2doom/scripts/perf/spec.md#render-projection.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import zlib

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))
from src.nand2doom.scripts.perf import n2d_perf  # noqa: E402

PPM_HEADER = b"P6\n640 480\n255\n"
LABELS = ("scene", "frame-001", "frame-002")


def project_fb(data: bytes) -> bytes:
    if len(data) != 320 * 200:
        raise ValueError("baseline framebuffer must contain 320x200 bytes")
    return bytes(data[i & ~1] for i in range(len(data)))


def project_vga(data: bytes) -> tuple[bytes, bytes]:
    if not data.startswith(PPM_HEADER) or len(data) != len(PPM_HEADER) + 640 * 480 * 3:
        raise ValueError("baseline VGA must be canonical 640x480 RGB888 P6")
    pixels = data[len(PPM_HEADER):]
    if any(c % 17 for c in pixels):
        raise ValueError("baseline VGA channels must be expanded RGB444")
    projected = bytearray(len(pixels))
    raster = bytearray(800 * 525 * 2)  # Ref: a VGA 640x480 frame is 800x525 scan clocks; blanking stays black
    for y in range(480):
        for x in range(640):
            source = 3 * (y * 640 + (x // 4) * 4 + x % 2)
            target = 3 * (y * 640 + x)
            r, g, b = pixels[source:source + 3]
            projected[target:target + 3] = bytes((r, g, b))
            color = ((r // 17) << 8) | ((g // 17) << 4) | (b // 17)
            offset = 2 * (y * 800 + x)
            raster[offset:offset + 2] = color.to_bytes(2, "big")
    return PPM_HEADER + projected, bytes(raster)


def reproduce(baseline: Path) -> dict[str, bytes]:
    full, half = n2d_perf.render_oracle(), n2d_perf.render_oracle("half")
    if half["projection"]["method"] != "even-column-v1" or half["projection"]["candidate_outputs_used"] is not False:
        raise ValueError("unsupported independent projection provenance")
    inputs = {}
    for group in ("framebuffer_sha256", "vga_ppm_sha256"):
        for name, digest in full[group].items():
            data = (baseline / name).read_bytes()
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError(f"baseline pixel hash differs: {name}")
            inputs[name] = data
    outputs, crcs = {}, {}
    for label in LABELS:
        fb_name, ppm_name = f"doom-{label}-fb.bin", f"doom-{label}-vga.ppm"
        fb = project_fb(inputs[fb_name])
        ppm, raster = project_vga(inputs[ppm_name])
        outputs.update({fb_name: fb, ppm_name: ppm})
        crcs[label] = (f"0x{zlib.crc32(fb):08x}", f"0x{zlib.crc32(raster):08x}")
    actual = dict(scene=crcs["scene"][0], scene_vga=crcs["scene"][1],
                  gameplay=[crcs[k][0] for k in LABELS[1:]],
                  gameplay_vga=[crcs[k][1] for k in LABELS[1:]])
    if actual != half["crcs"]:
        raise ValueError("reproduced CRCs differ from independent half fixture")
    for group in ("framebuffer_sha256", "vga_ppm_sha256"):
        for name, digest in half[group].items():
            if hashlib.sha256(outputs[name]).hexdigest() != digest:
                raise ValueError(f"reproduced pixel hash differs: {name}")
    return outputs


def check_candidate(candidate: Path, projected: dict[str, bytes]) -> list[str]:
    errors = n2d_perf.check_render(n2d_perf.parse_report((candidate / "report.txt").read_text()),
                                   candidate, n2d_perf.render_oracle("half"))
    for name, data in projected.items():
        path = candidate / name
        if not path.is_file() or path.read_bytes() != data:
            errors.append(f"independent projection bytes differ: {name}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--output", type=Path, help="optional fresh directory below workdir/")
    parser.add_argument("--candidate", type=Path)
    args = parser.parse_args(argv)
    try:
        outputs = reproduce(args.baseline)
        if args.output:
            output = args.output.resolve()
            workdir = (REPO_ROOT / "workdir").resolve()
            if workdir not in output.parents:
                raise ValueError("projection output must be below workdir/")
            output.mkdir(parents=True, exist_ok=False)
            for name, data in outputs.items():
                (output / name).write_bytes(data)
        errors = check_candidate(args.candidate, outputs) if args.candidate else []
        print(json.dumps({"status": "FAIL" if errors else "PASS", "errors": errors,
                          "projected_sha256": {k: hashlib.sha256(v).hexdigest() for k, v in outputs.items()}}, indent=2))
        return int(bool(errors))
    except (ValueError, OSError, KeyError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
