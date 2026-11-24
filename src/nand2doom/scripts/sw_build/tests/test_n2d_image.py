from contextlib import redirect_stdout
import functools
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[5]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.nand2doom.scripts.sw_build import n2d_image  # noqa: E402
from tools.bazel import scratch  # noqa: E402


class N2dImageToolTests(unittest.TestCase):
    @functools.cached_property
    def root(self) -> Path:
        return scratch.checkout(self)

    def test_compiler_contract_is_explicit(self) -> None:
        commands = n2d_image.image_commands(
            REPO_ROOT,
            REPO_ROOT / "workdir/software/unit-image",
            "riscv64-unknown-elf-gcc",
            "riscv64-unknown-elf-objcopy",
        )
        flags = tuple(token for command in commands for token in command)
        for expected in (
            "-march=rv32im",
            "-mabi=ilp32",
            "-ffreestanding",
            "-fno-builtin",
            "-nostdlib",
            "-nostartfiles",
        ):
            self.assertIn(expected, flags)

    def test_plan_is_non_mutating(self) -> None:
        output = self.root / "workdir/software/plan-only"
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            status = n2d_image.main(["--output", str(output), "--plan"], root=self.root)
        self.assertEqual(0, status)
        self.assertIn("architecture: rv32im", stdout.getvalue())
        self.assertIn("abi: ilp32", stdout.getvalue())
        self.assertFalse(output.exists())

    def test_output_must_stay_in_workdir(self) -> None:
        with self.assertRaisesRegex(n2d_image.ImageBuildError, "escapes"):
            n2d_image._confined_output(REPO_ROOT / "outside", REPO_ROOT)

    def test_output_may_be_in_the_bazel_test_scratch(self) -> None:
        with tempfile.TemporaryDirectory() as scratch, \
                mock.patch.dict(os.environ, {"TEST_TMPDIR": scratch}):
            inside = Path(scratch) / "image"
            self.assertEqual(inside.resolve(), n2d_image._confined_output(inside, REPO_ROOT))
            with self.assertRaisesRegex(n2d_image.ImageBuildError, "escapes"):
                n2d_image._confined_output(REPO_ROOT / "outside", REPO_ROOT)

    def test_clean_cross_output_builds_are_identical(self) -> None:
        output_a = self.root / "workdir/image-a"
        output_b = self.root / "workdir/image-b"
        manifest_a = n2d_image.build_image(output_a, self.root)
        manifest_b = n2d_image.build_image(output_b, self.root)
        self.assertEqual(manifest_a, manifest_b)
        self.assertEqual(
            (output_a / "n2d_validation.map").read_bytes(),
            (output_b / "n2d_validation.map").read_bytes(),
        )
        self.assertIn(
            "OUTPUT(${OUTPUT}/n2d_validation.elf",
            (output_a / "n2d_validation.map").read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
