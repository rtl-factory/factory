#!/usr/bin/env python3
"""Offline indexed-framebuffer decoder; never opens serial or accesses hardware."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
from io import BytesIO
import json
from pathlib import Path
import stat
import struct
import sys

# Fixed wire-image format owned by wiki/src/nand2doom/rtl/video/mas.md.
WIDTH, HEIGHT = 320, 200
INDEX_BYTES = WIDTH * HEIGHT
PALETTE_BYTES = 256 * 4
MAX_FRAMES = 16
MAX_PNG_BYTES = 256 * 1024
MAX_GIF_BYTES = 4 * 1024 * 1024
REPO_ROOT = Path(__file__).resolve().parents[3]


def _palette(palette: bytes) -> bytes:
    if not isinstance(palette, bytes) or len(palette) != PALETTE_BYTES:
        raise ValueError("palette must be exactly 1024 bytes: 256 little-endian words")
    colors = bytearray()
    for (word,) in struct.iter_unpack("<I", palette):
        if word > 0xfff:
            raise ValueError("palette word has bits outside RGB444")
        colors.extend((((word >> 8) & 15) * 17, ((word >> 4) & 15) * 17,
                       (word & 15) * 17))
    return bytes(colors)


def decode_frame(indices: bytes, palette: bytes) -> bytes:
    """Return 320x200 row-major RGB888 bytes without importing image libraries."""
    if not isinstance(indices, bytes) or len(indices) != INDEX_BYTES:
        raise ValueError("indices must be exactly 64000 bytes (320x200 row-major)")
    colors = _palette(palette)
    return b"".join(colors[index * 3:index * 3 + 3] for index in indices)


def _validate(frames, durations_ms):
    if not isinstance(frames, (list, tuple)) or not 1 <= len(frames) <= MAX_FRAMES:
        raise ValueError("frames must be a list/tuple of 1..16 (indices, palette) pairs")
    if not isinstance(durations_ms, (list, tuple)) or len(durations_ms) != len(frames):
        raise ValueError("one explicit sampled duration is required per frame")
    for duration in durations_ms:
        if type(duration) is not int or not 10 <= duration <= 60000 or duration % 10:
            raise ValueError("durations must be integer multiples of 10 ms in 10..60000")
    pixels = []
    for frame in frames:
        if not isinstance(frame, (list, tuple)) or len(frame) != 2:
            raise ValueError("each frame must be an (indices, palette) pair")
        pixels.append(decode_frame(*frame))
    return pixels


@dataclass(frozen=True)
class EncodedFrames:
    pngs: tuple[bytes, ...]
    gif: bytes


def encode_frames(frames, durations_ms) -> EncodedFrames:
    """Encode exact PNG/GIF bytes, preserving even identical adjacent frames.

    Durations describe sampled playback, not the device's measured frame rate.
    The caller supplies coherent frame/palette pairs; this function cannot prove
    their hardware provenance. At most 16 frames are accepted.
    """
    expected = _validate(frames, durations_ms)
    try:
        from PIL import Image, GifImagePlugin
    except ImportError as error:
        raise ValueError("encoding requires Pillow; no dependency is installed automatically") from error

    pngs = []
    gif = bytearray()
    for index, ((indices, palette), duration) in enumerate(zip(frames, durations_ms)):
        image = Image.frombytes("P", (WIDTH, HEIGHT), indices)
        image.putpalette(_palette(palette))
        png = BytesIO()
        # PNG save attaches encoder configuration; keep it off the GIF object.
        image.copy().save(png, format="PNG", optimize=False, compress_level=9)
        data = png.getvalue()
        if len(data) > MAX_PNG_BYTES:
            raise ValueError("PNG exceeds bounded output size")
        pngs.append(data)
        if index == 0:
            header, _ = GifImagePlugin.getheader(image, info={"loop": 0, "optimize": False})
            gif.extend(b"".join(header))
        # Public low-level GIF helpers avoid Image.save's identical-frame
        # coalescing. Full rectangles plus local palettes preserve every sample.
        gif.extend(b"".join(GifImagePlugin.getdata(
            image, duration=duration, disposal=1, include_color_table=True)))
        if len(gif) + 1 > MAX_GIF_BYTES:
            raise ValueError("GIF exceeds bounded output size")
    gif.extend(b";")
    result = EncodedFrames(tuple(pngs), bytes(gif))

    # Fail closed if an encoder version changes palette, order, duration or
    # coalescing behavior. This checks encoding, not physical capture validity.
    for data, pixels in zip(result.pngs, expected):
        with Image.open(BytesIO(data)) as decoded:
            if decoded.size != (WIDTH, HEIGHT) or decoded.convert("RGB").tobytes() != pixels:
                raise ValueError("PNG roundtrip did not preserve RGB pixels")
    with Image.open(BytesIO(result.gif)) as decoded:
        if decoded.n_frames != len(frames) or decoded.info.get("loop") != 0:
            raise ValueError("GIF did not preserve frame count/loop")
        for index, pixels in enumerate(expected):
            decoded.seek(index)
            if (decoded.size != (WIDTH, HEIGHT)
                    or decoded.convert("RGB").tobytes() != pixels
                    or decoded.info.get("duration") != durations_ms[index]):
                raise ValueError("GIF roundtrip did not preserve RGB pixels/duration")
    return result


def _output_path(name: str, root: Path) -> Path:
    relative = Path(name)
    if (relative.is_absolute() or "\\" in name or ":" in name
            or ".." in relative.parts or len(relative.parts) < 2
            or relative.parts[0] != "workdir"):
        raise ValueError("output must be a new relative directory below repository workdir/")
    current = root.resolve()
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError("output path must not contain symlinks")
        if current.exists() and not current.is_dir():
            raise ValueError("output path has an existing non-directory component")
    if current.exists():
        raise ValueError("output already exists; select a fresh directory")
    return current


def _read_exact(path: Path, length: int) -> bytes:
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("input must be a regular file")
    with path.open("rb") as stream:
        data = stream.read(length + 1)
    if len(data) != length:
        raise ValueError(f"{path}: expected exactly {length} bytes")
    return data


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main(argv=None, *, repo_root: Path = REPO_ROOT) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frame", nargs=2, action="append", required=True,
                        metavar=("INDICES", "PALETTE"), help="repeat in sampled order")
    parser.add_argument("--duration-ms", nargs="+", type=int, required=True,
                        help="one explicit sampled duration per frame; multiples of 10")
    parser.add_argument("--output", required=True, help="new workdir/ directory, relative to repo")
    parser.add_argument("--dry-run", action="store_true", help="validate inputs/plan, no output or Pillow")
    args = parser.parse_args(argv)
    try:
        target = _output_path(args.output, repo_root)
        if not 1 <= len(args.frame) <= MAX_FRAMES:
            raise ValueError("expected 1..16 input frames")
        frames = [(_read_exact(Path(indices), INDEX_BYTES),
                   _read_exact(Path(palette), PALETTE_BYTES))
                  for indices, palette in args.frame]
        _validate(frames, args.duration_ms)
        manifest = {
            "schema_version": 1, "width": WIDTH, "height": HEIGHT,
            "evidence": "offline reconstruction; hardware provenance and coherence not verified",
            "playback": "sampled durations; not measured game frame rate",
            "frames": [dict(indices_sha256=_sha(indices), palette_sha256=_sha(palette),
                            duration_ms=duration, png=f"frame-{index:03d}.png")
                       for index, ((indices, palette), duration) in enumerate(zip(frames, args.duration_ms))],
            "animation": "sampled.gif",
        }
        if args.dry_run:
            print(json.dumps(manifest, sort_keys=True, indent=2))
            return 0
        encoded = encode_frames(frames, args.duration_ms)
        manifest["png_sha256"] = [_sha(png) for png in encoded.pngs]
        manifest["gif_sha256"] = _sha(encoded.gif)
        # Recheck after encoding. Never reuse a pre-existing artifact directory.
        target = _output_path(args.output, repo_root)
        target.mkdir(parents=True, exist_ok=False)
        for index, data in enumerate(encoded.pngs):
            with (target / f"frame-{index:03d}.png").open("xb") as stream:
                stream.write(data)
        with (target / "sampled.gif").open("xb") as stream:
            stream.write(encoded.gif)
        # Written last: absence means incomplete output. No automatic cleanup
        # or overwriting if filesystem I/O fails partway through publication.
        with (target / "decode.json").open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
        print(f"decoded {len(frames)} sampled frames: {target}")
        return 0
    except (ValueError, OSError) as error:
        print(f"frame decode error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
