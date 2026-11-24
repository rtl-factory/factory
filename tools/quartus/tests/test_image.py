"""Image identity: the receipt binds a passing SOF to its commit, inputs and
evidence, and verify_image refuses every missing, changed or foreign piece."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.quartus import gate, image, manifest  # noqa: E402
from tools.quartus.tests import fixture  # noqa: E402

SOURCE = {"source_sha": "a" * 40, "source_dirty": False, "quartus_version": "Version 25.1.0 Lite Edition"}


class ImageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = fixture.make_checkout(Path(self.tmp.name))
        self.output = self.root / "workdir" / "quartus" / "demo"
        self.config = manifest.load(self.root, fixture.MANIFEST)
        fixture.write_build(self.root, self.output, self.config)
        gate.verify_reports(self.output, self.config)
        self.identity = image.write_identity(self.output, self.config, SOURCE)
        self.sof = self.output / "output_files" / "demo_image.sof"
        self.files = image.companions(self.sof)

    def test_receipt_records_the_image_and_its_companions(self):
        receipt = json.loads(self.files["receipt"].read_text())
        self.assertEqual(receipt, self.identity)
        self.assertEqual(set(receipt), image.RECEIPT_FIELDS)
        self.assertEqual(receipt["sha256"], image.digest(self.sof))
        self.assertEqual(receipt["inputs_sha256"], image.digest(self.output / "inputs.json"))
        self.assertEqual(receipt["evidence_sha256"], image.digest(self.output / "evidence.json"))
        self.assertEqual((receipt["source_sha"], receipt["board"]), ("a" * 40, "demo_board"))

    def test_the_four_files_verify_away_from_their_build(self):
        moved = self.root / "elsewhere"
        moved.mkdir()
        for path in (self.sof, *self.files.values()):
            (moved / path.name).write_bytes(path.read_bytes())
        self.assertEqual(image.verify_image(moved / self.sof.name, self.config), self.identity)

    def test_refusals(self):
        def receipt(**changes):
            data = {**self.identity, **changes}
            self.files["receipt"].write_text(json.dumps(data))

        cases = [
            ("before programming", lambda: self.files["receipt"].unlink()),
            ("malformed SOF identity receipt", lambda: self.files["receipt"].write_text("{")),
            ("incomplete or malformed", lambda: receipt(source_dirty="no")),
            ("incomplete or malformed", lambda: receipt(source_sha="HEAD")),
            ("incomplete or malformed", lambda: receipt(extra=1)),
            ("does not match the selected board", lambda: receipt(device="EP4CE115F29C7")),
            ("hash mismatch or missing transferred file: demo_image.sof",
             lambda: self.sof.write_bytes(b"other image\n")),
            ("missing transferred file: demo_image.evidence.json", lambda: self.files["evidence"].unlink()),
            ("missing transferred file: demo_image.inputs.json",
             lambda: self.files["inputs"].write_text(self.files["inputs"].read_text() + " ")),
        ]
        for message, corrupt in cases:
            with self.subTest(message):
                image.write_identity(self.output, self.config, SOURCE)
                corrupt()
                with self.assertRaisesRegex(ValueError, message):
                    image.verify_image(self.sof, self.config)

    def test_an_image_of_another_manifest_must_be_rebuilt(self):
        changed = dict(self.config, parameters={"BIG": 1})
        with self.assertRaisesRegex(ValueError, "rebuild the image"):
            image.verify_image(self.sof, changed)

    def test_rehashed_but_failing_evidence_is_refused(self):
        evidence = json.loads(self.files["evidence"].read_text())
        evidence["timing"][0]["slack_ns"] = -1.0
        self.files["evidence"].write_text(json.dumps(evidence))
        receipt = dict(self.identity, evidence_sha256=image.digest(self.files["evidence"]))
        self.files["receipt"].write_text(json.dumps(receipt))
        with self.assertRaisesRegex(ValueError, "invalid gate evidence"):
            image.verify_image(self.sof, self.config)


if __name__ == "__main__":
    unittest.main()
