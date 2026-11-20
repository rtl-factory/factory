"""Independent projection geometry, raster packing and fail-closed inputs."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.nand2doom.scripts import n2d_perf, n2d_render_projection as projection


class ProjectionTests(unittest.TestCase):
    def test_framebuffer_even_columns_and_rows(self):
        data = bytes((x + y) % 256 for y in range(200) for x in range(320))
        actual = projection.project_fb(data)
        for y in (0, 1, 199):
            for x in (0, 1, 2, 319):
                self.assertEqual(actual[y * 320 + x], ((x & ~1) + y) % 256)
        with self.assertRaises(ValueError):
            projection.project_fb(data[:-1])

    def test_vga_pair_projection_rgb444_and_black_blanking(self):
        row = b"".join(bytes(((x % 16) * 17, 17, 34)) for x in range(640))
        image, raster = projection.project_vga(projection.PPM_HEADER + row * 480)
        pixels = image[len(projection.PPM_HEADER):]
        for y in (0, 1, 479):
            for x in (0, 1, 2, 3, 4, 639):
                source_x = x // 4 * 4 + x % 2
                color = ((source_x % 16) << 8) | 0x12
                self.assertEqual(pixels[3 * (y * 640 + x):3 * (y * 640 + x) + 3], row[3 * source_x:3 * source_x + 3])
                self.assertEqual(raster[2 * (y * 800 + x):2 * (y * 800 + x) + 2], color.to_bytes(2, "big"))
            self.assertEqual(raster[2 * (y * 800 + 640):2 * ((y + 1) * 800)], bytes(320))
        self.assertEqual(raster[480 * 800 * 2:], bytes(45 * 800 * 2))
        for bad in (b"P3\n" + row * 480, projection.PPM_HEADER + row[:-1],
                    projection.PPM_HEADER + b"\x01" + (row * 480)[1:]):
            with self.assertRaises(ValueError):
                projection.project_vga(bad)

    def test_baseline_hash_and_fixture_provenance_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline = Path(tmp)
            fixture = n2d_perf.render_oracle()
            first = next(iter(fixture["framebuffer_sha256"]))
            (baseline / first).write_bytes(b"candidate-derived")
            with self.assertRaisesRegex(ValueError, "baseline pixel hash"):
                projection.reproduce(baseline)
            half = copy.deepcopy(n2d_perf.render_oracle("half"))
            half["projection"]["candidate_outputs_used"] = True
            with patch.object(n2d_perf, "render_oracle", side_effect=[fixture, half]), self.assertRaisesRegex(ValueError, "provenance"):
                projection.reproduce(baseline)

    def test_candidate_byte_corruption_and_absence(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp)
            (candidate / "report.txt").write_text("")
            (candidate / "pixel").write_bytes(b"wrong")
            with patch.object(n2d_perf, "check_render", side_effect=lambda *args: []):
                self.assertTrue(projection.check_candidate(candidate, {"pixel": b"reference"}))
                (candidate / "pixel").unlink()
                self.assertTrue(projection.check_candidate(candidate, {"pixel": b"reference"}))
                (candidate / "pixel").write_bytes(b"reference")
                self.assertEqual(projection.check_candidate(candidate, {"pixel": b"reference"}), [])


if __name__ == "__main__":
    unittest.main()
