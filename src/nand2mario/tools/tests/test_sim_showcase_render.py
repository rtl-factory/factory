"""sim_showcase_render: synthetic qualification frames round-trip into 2x PNGs,
pixel-exact GIFs with merged duplicate samples, and hashed provenance."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from src.nand2mario.tools import sim_showcase_render as render

ROOT = Path(__file__).resolve().parents[4]
SHADES = (0, 85, 170, 255)


def frame(seed):
    im = Image.new("L", (160, 144))
    im.putdata([SHADES[(x + y + seed) % 4] for y in range(144) for x in range(160)])
    return im


class SimShowcaseRenderTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT / "workdir/n2m/unit-tests"
        parent.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.tmp.cleanup)
        self.res = Path(self.tmp.name) / "results"
        self.out = Path(self.tmp.name) / "out"
        for gi, (game, *_rest) in enumerate(render.GAMES):
            d = self.res / game
            d.mkdir(parents=True)
            (d / "game-result.json").write_text(json.dumps({"image_sha256": f"{gi:064x}"}))
            seeds = [0, 0, 1, 2, 3]  # title == start: must merge
            for step, seed in zip(render.STEPS, seeds):
                frame(seed + gi).save(d / f"{step}.png")
        render.main(["--results", str(self.res), "--output", str(self.out),
                     "--source-commit", "abc1234"])
        self.prov = json.loads((self.out / "provenance.json").read_text())

    def test_png_scaled_exactly(self):
        game = render.GAMES[0][0]
        src = Image.open(self.res / game / "play.png")
        out = Image.open(self.out / f"{game}-play.png")
        self.assertEqual(out.size, (320, 288))
        self.assertEqual(out.tobytes(), src.resize((320, 288), Image.NEAREST).tobytes())

    def test_gif_merges_duplicates_and_matches(self):
        for game, *_ in render.GAMES:
            gif = Image.open(self.out / f"{game}.gif")
            self.assertEqual(gif.n_frames, 4)
            gif.seek(0)
            self.assertEqual(gif.info["duration"], 2000)
            gif.seek(3)
            ref = Image.open(self.out / f"{game}-right.png")
            self.assertEqual(gif.convert("L").tobytes(), ref.tobytes())

    def test_provenance_hashes_and_label(self):
        self.assertIn("not a physical board", self.prov["label"])
        self.assertEqual(self.prov["source_commit"], "abc1234")
        for g in self.prov["games"]:
            self.assertEqual(g["license"], "MIT")
            for f in g["frames"]:
                data = (self.out / f["png"]).read_bytes()
                self.assertEqual(f["sha256"], hashlib.sha256(data).hexdigest())

    def test_missing_results_rejected(self):
        with self.assertRaises(FileNotFoundError):
            render.main(["--results", str(self.res / "nope"), "--output",
                         str(self.out), "--source-commit", "x"])


if __name__ == "__main__":
    unittest.main()
