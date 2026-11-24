"""Tests of the board image action's entry point (wiki/tools/bazel/spec.md, Board images)."""
import os
from pathlib import Path
import subprocess
import sys

CHECKOUT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
import quartus_image as q  # noqa: E402


def test_the_built_action_runs_the_checkout_tool_with_its_own_imports(tmp_path):
    """The wrapper's runfiles (an implicit tools/__init__.py) never hide the checkout's tools.quartus."""
    runner = Path(os.environ["RUNFILES_DIR"]) / "_main" / "tools" / "bazel" / "quartus_image"
    result = subprocess.run(
        [str(runner), "--script", str(CHECKOUT / "tools" / "quartus" / "quartus.py"),
         "--project", "no_such_project", "--out", str(tmp_path / "out"), "--", "--help"],
        cwd=CHECKOUT, capture_output=True, text=True, timeout=60)
    assert "Traceback" not in result.stderr, result.stderr
    assert "usage: quartus.py" in result.stdout


def test_the_tool_gets_no_runfiles_environment():
    environ = {"PATH": "/usr/bin", "PYTHONSAFEPATH": "1", "RUNFILES_DIR": "/r",
               "RUNFILES_MANIFEST_FILE": "/r/MANIFEST", "RUNFILES_REPO_MAPPING": "/r/m",
               "PYTHONPATH": "/r/_main", "QUARTUS_SH": "/q"}
    command, env = q.tool_command(Path("/c/tools/quartus/quartus.py"), ["--plan"], environ)
    assert env == {"PATH": "/usr/bin", "QUARTUS_SH": "/q"}
    assert command[1:] == ["-B", "/c/tools/quartus/quartus.py", "--plan"]
