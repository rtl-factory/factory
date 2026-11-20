"""Hardware-free format, exact-image, and CLI boundary checks."""

from contextlib import redirect_stderr, redirect_stdout
from io import BytesIO, StringIO
import json
from pathlib import Path
import random
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from src.nand2doom.scripts.host import n2d_frame_decode as decoder


def palette(shift=0):
    return b"".join(struct.pack("<I", (((index >> 4) + shift) & 15) << 8
                               | (index & 15) << 4 | ((index + shift * 5) & 15))
                    for index in range(256))


def frame(shift=0):
    return bytes((x + 3*y + 17*shift) & 255 for y in range(200) for x in range(320)), palette(shift)


class DecodeTests(unittest.TestCase):
    def test_channels_and_little_endian(self):
        raw = struct.pack("<I", 0x1a5) * 256
        self.assertEqual(decoder.decode_frame(bytes(64000), raw), bytes((17, 170, 85)) * 64000)
        with self.assertRaises(ValueError):
            decoder.decode_frame(bytes(64000), struct.pack(">I", 0x1a5) * 256)

    def test_all_indices_row_major_boundaries(self):
        indices, raw = frame()
        actual = decoder.decode_frame(indices, raw)
        for pixel in [0, 255, 256, 319, 320, 63999] + list(range(256)):
            index = indices[pixel]
            self.assertEqual(actual[pixel*3:pixel*3+3],
                             bytes(((index >> 4) * 17, (index & 15) * 17, (index & 15) * 17)))
        self.assertEqual(len(actual), 192000)

    def test_input_lengths_and_types(self):
        for length in [0, 63999, 64001]:
            with self.subTest(indices=length), self.assertRaises(ValueError):
                decoder.decode_frame(bytes(length), palette())
        for length in [0, 1023, 1025]:
            with self.subTest(palette=length), self.assertRaises(ValueError):
                decoder.decode_frame(bytes(64000), bytes(length))
        for bad in [bytearray(64000), "x" * 64000, None]:
            with self.subTest(type=type(bad)), self.assertRaises(ValueError):
                decoder.decode_frame(bad, palette())

    def test_high_palette_bits_at_first_and_last_entry(self):
        for entry, word in [(0, 0x1000), (255, 0x80000000)]:
            raw = bytearray(palette())
            struct.pack_into("<I", raw, entry * 4, word)
            with self.assertRaises(ValueError):
                decoder.decode_frame(bytes(64000), bytes(raw))

    def test_duration_shape_and_bounds(self):
        for durations in [[], [10, 20], [0], [-10], [15], [60010], [True], [10.0], ["10"], None]:
            with self.subTest(durations=durations), self.assertRaises(ValueError):
                decoder.encode_frames([frame()], durations)
        for frames in [[], [frame()] * 17, [(bytes(64000),)], [None], iter([frame()])]:
            with self.subTest(frames=type(frames)), self.assertRaises(ValueError):
                decoder.encode_frames(frames, [10])

    def test_different_palettes_exact_png_gif_duration_and_order(self):
        frames = [frame(index) for index in range(3)]
        encoded = decoder.encode_frames(frames, [400, 600, 800])
        for index, png in enumerate(encoded.pngs):
            with Image.open(BytesIO(png)) as decoded:
                self.assertEqual(decoded.size, (320, 200))
                self.assertEqual(decoded.convert("RGB").tobytes(), decoder.decode_frame(*frames[index]))
        with Image.open(BytesIO(encoded.gif)) as decoded:
            self.assertEqual(decoded.n_frames, 3)
            for index in range(3):
                decoded.seek(index)
                self.assertEqual(decoded.info["duration"], [400, 600, 800][index])
                self.assertEqual(decoded.convert("RGB").tobytes(), decoder.decode_frame(*frames[index]))

    def test_identical_and_black_frames_are_not_coalesced(self):
        black = (bytes(64000), bytes(1024))
        frames = [black, black, frame(1), frame(1), black]
        durations = [10, 60000, 20, 30, 40]
        result = decoder.encode_frames(frames, durations)
        with Image.open(BytesIO(result.gif)) as decoded:
            self.assertEqual(decoded.n_frames, 5)
            for index, duration in enumerate(durations):
                decoded.seek(index)
                self.assertEqual(decoded.info["duration"], duration)
                self.assertEqual(decoded.convert("RGB").tobytes(), decoder.decode_frame(*frames[index]))

    def test_deterministic_and_bounded(self):
        frames = [frame(0), frame(1)]
        first = decoder.encode_frames(frames, [500, 500])
        self.assertEqual(first, decoder.encode_frames(frames, [500, 500]))
        self.assertLess(len(first.gif), decoder.MAX_GIF_BYTES)
        self.assertTrue(all(len(data) <= decoder.MAX_PNG_BYTES for data in first.pngs))
        for constant in ["MAX_GIF_BYTES", "MAX_PNG_BYTES"]:
            with patch.object(decoder, constant, 1), self.assertRaises(ValueError):
                decoder.encode_frames(frames, [500, 500])

    def test_maximum_frames_and_single_frame(self):
        for count in [1, 16]:
            result = decoder.encode_frames([frame()] * count, [10] * count)
            with Image.open(BytesIO(result.gif)) as decoded:
                self.assertEqual(decoded.n_frames, count)

    def test_seeded_high_entropy_frame_bounds(self):
        rng = random.Random(69)
        indices = rng.randbytes(64000)
        raw = b"".join(struct.pack("<I", rng.randrange(4096)) for _ in range(256))
        result = decoder.encode_frames([(indices, raw), (indices, palette())], [10, 60000])
        self.assertTrue(all(len(data) < decoder.MAX_PNG_BYTES for data in result.pngs))
        self.assertLess(len(result.gif), decoder.MAX_GIF_BYTES)


class CliTests(unittest.TestCase):
    def setUp(self):
        workdir = decoder.REPO_ROOT / "workdir"
        workdir.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="frame-decode-test-", dir=workdir)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.index = self.root / "indices.bin"
        self.colors = self.root / "palette.bin"
        self.index.write_bytes(frame()[0])
        self.colors.write_bytes(palette())

    def args(self, output="workdir/render"):
        return ["--frame", str(self.index), str(self.colors), "--duration-ms", "500", "--output", output]

    def run_cli(self, args):
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = decoder.main(args, repo_root=self.root)
        return result, stdout.getvalue(), stderr.getvalue()

    def test_dry_run_no_files(self):
        result, stdout, stderr = self.run_cli(self.args() + ["--dry-run"])
        self.assertEqual((result, stderr), (0, ""))
        plan = json.loads(stdout)
        self.assertEqual(plan["frames"][0]["duration_ms"], 500)
        self.assertIn("not verified", plan["evidence"])
        self.assertFalse((self.root / "workdir").exists())

    def test_cli_outputs_and_refuses_overwrite(self):
        self.assertEqual(self.run_cli(self.args())[0], 0)
        output = self.root / "workdir/render"
        self.assertEqual({p.name for p in output.iterdir()}, {"frame-000.png", "sampled.gif", "decode.json"})
        manifest = json.loads((output / "decode.json").read_text())
        self.assertEqual(manifest["gif_sha256"], decoder._sha((output / "sampled.gif").read_bytes()))
        self.assertEqual(self.run_cli(self.args())[0], 2)

    def test_bad_inputs_create_nothing(self):
        for path, value in [(self.index, bytes(63999)), (self.index, bytes(64001)),
                            (self.colors, bytes(1023)), (self.colors, struct.pack("<I", 4096) * 256)]:
            self.index.write_bytes(frame()[0])
            self.colors.write_bytes(palette())
            path.write_bytes(value)
            with self.subTest(path=path, length=len(value)):
                self.assertEqual(self.run_cli(self.args())[0], 2)
                self.assertFalse((self.root / "workdir").exists())

    def test_rejects_unsafe_outputs_and_symlink_parents(self):
        for name in ["/tmp/escape", "../escape", "workdir/../escape", "workdir", "src/output", "C:/escape", "workdir\\escape"]:
            with self.subTest(name=name):
                self.assertEqual(self.run_cli(self.args(name))[0], 2)
        (self.root / "workdir").mkdir()
        (self.root / "workdir/link").symlink_to(self.root, target_is_directory=True)
        self.assertEqual(self.run_cli(self.args("workdir/link/escape"))[0], 2)
        (self.root / "workdir/broken").symlink_to(self.root / "missing")
        self.assertEqual(self.run_cli(self.args("workdir/broken"))[0], 2)
        self.assertFalse((self.root / "escape").exists())

    def test_rejects_symlink_workdir(self):
        (self.root / "workdir").symlink_to(self.root, target_is_directory=True)
        self.assertEqual(self.run_cli(self.args())[0], 2)
        self.assertFalse((self.root / "render").exists())

    def test_excess_count_before_any_input_read(self):
        args = ["--frame", str(self.index), str(self.colors)] * 17
        args += ["--duration-ms"] + ["10"] * 17 + ["--output", "workdir/render"]
        with patch.object(decoder, "_read_exact", side_effect=AssertionError("unexpected input read")):
            self.assertEqual(self.run_cli(args)[0], 2)

    def test_nondirectory_output_ancestors_fail_dry_run_and_encode(self):
        for case, output, blocker in [
                ("top", "workdir/render", "workdir"),
                ("nested", "workdir/file-parent/render", "workdir/file-parent")]:
            root = self.root / case
            target = root / blocker
            target.parent.mkdir(parents=True)
            target.write_bytes(b"preserve existing data")
            for suffix in [[], ["--dry-run"]]:
                with self.subTest(case=case, suffix=suffix), redirect_stderr(StringIO()):
                    self.assertEqual(decoder.main(self.args(output) + suffix, repo_root=root), 2)
                self.assertEqual(target.read_bytes(), b"preserve existing data")
                self.assertEqual(list(root.rglob("*.png")), [])

    def test_help_and_dry_run_without_site_packages(self):
        script = Path(decoder.__file__)
        for args in [["--help"], self.args("workdir/frame-decode-unused-plan") + ["--dry-run"]]:
            result = subprocess.run([sys.executable, "-S", "-B", str(script), *args],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_pillow_does_not_publish(self):
        with patch.dict(sys.modules, {"PIL": None}):
            result, _, error = self.run_cli(self.args())
        self.assertEqual(result, 2)
        self.assertIn("requires Pillow", error)
        self.assertFalse((self.root / "workdir").exists())

    def test_read_rejects_nonregular_files(self):
        with self.assertRaises(ValueError):
            decoder._read_exact(self.root, 64000)


if __name__ == "__main__":
    unittest.main()
