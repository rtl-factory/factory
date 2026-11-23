"""The coverage summary of a simulation run, without a simulator (tools/sim/sim_coverage.py)."""
from pathlib import Path
import sys
import tempfile
import unittest
import unittest.mock

import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools/sim"))
import sim_coverage  # noqa: E402


def point(kind: str, module: str, line: int, count: int) -> str:
    fields = {"f": f"src/demo/{module}.sv", "l": str(line), "t": kind, "page": f"v_{kind}/{module}"}
    return "C '" + "".join(f"\x01{key}\x02{value}" for key, value in fields.items()) + f"' {count}\n"


class CodeCoverageTests(unittest.TestCase):
    def test_counts_points_hit_per_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            dat = Path(tmp) / "coverage.dat"
            dat.write_text("# SystemC::Coverage-3\n" + point("line", "a", 1, 5) + point("line", "a", 2, 0) +
                           point("toggle", "a", 3, 1) + point("branch", "b", 4, 0))
            self.assertEqual(sim_coverage.code_points(dat),
                             {"branch": (0, 1), "line": (1, 2), "toggle": (1, 1)})

    def test_summary_reports_code_and_writes_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            test_dir = Path(tmp)
            (test_dir / "coverage.dat").write_text(point("line", "a", 1, 1) + point("line", "a", 2, 0))
            with unittest.mock.patch("shutil.which", return_value=None):
                lines = sim_coverage.summarize(test_dir, ROOT)
            self.assertEqual(lines[0], "code: line 50.0% (1/2)")
            self.assertIn("verilator_coverage not on PATH", lines[1])
            self.assertEqual((test_dir / "coverage.txt").read_text().splitlines(), lines)

    def test_missing_code_coverage_is_reported_only_when_asked_for(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(sim_coverage.summarize(Path(tmp), ROOT), [])
            self.assertIn("no coverage.dat", sim_coverage.summarize(Path(tmp), ROOT, expect_code=True)[0])


class FunctionalCoverageTests(unittest.TestCase):
    def test_summary_lists_top_level_groups_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            test_dir = Path(tmp)
            (test_dir / "functional_coverage.yml").write_text(yaml.safe_dump({
                "bench": {"coverage": 3, "size": 4},
                "bench.mode": {"coverage": 3, "size": 4, "bins:_hits": {"a": 1}},
            }))
            self.assertEqual(sim_coverage.summarize(test_dir, ROOT), ["functional: bench 75.0% (3/4)"])

    def test_write_functional_round_trips_through_the_summary(self):
        from cocotb_coverage.coverage import CoverPoint, coverage_db

        @CoverPoint("unit_bench.parity", xf=lambda value: value % 2, bins=[0, 1])
        def sample(value):
            """One sampled value."""

        sample(2)
        with tempfile.TemporaryDirectory() as tmp, unittest.mock.patch.dict(
                "os.environ", {sim_coverage.DIR_ENV: tmp}):
            path = sim_coverage.write_functional()
            self.assertEqual(path, Path(tmp) / "functional_coverage.yml")
            self.assertEqual(sim_coverage.functional_groups(path)["unit_bench"], (1, 2))
        coverage_db.clear()


if __name__ == "__main__":
    unittest.main()
