"""Resolve the quartus_stp JTAG device name of the nand2doom board by IDCODE.

quartus_stp names a device after the part list it associates with the IDCODE,
and that list differs between Quartus installs: the DE2-115 is
`@1: EP4CE115 (0x020F70DD)` on one host and
`@1: 10CL120(Y|Z)/EP3C120/.. (0x020F70DD)` on another. The name is therefore
read from the cable's quartus_stp device listing and chosen by the IDCODE
suffix; exactly one match is required. Spec:
wiki/src/nand2doom/scripts/host/spec.md#device-resolution.
"""
from __future__ import annotations

from pathlib import Path
import re
import subprocess

DE2_115_IDCODE = 0x020F70DD
LIST_TIMEOUT = 30
SCRIPT = Path(__file__).resolve().with_name("n2d_jtag_devices.tcl")
_IDCODE = re.compile(r"\(0x([0-9A-Fa-f]{8})\)\s*$")


def run_tool(argv, timeout):
    result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, errors="replace", timeout=timeout, check=False)
    return result.returncode, result.stdout


def parse_devices(text: str) -> list[str]:
    """Device names from the `N2D_DEVICE ` lines of n2d_jtag_devices.tcl output."""
    return [line[len("N2D_DEVICE "):].strip() for line in text.splitlines()
            if line.startswith("N2D_DEVICE ") and line[len("N2D_DEVICE "):].strip()]


def device_idcode(name: str) -> int | None:
    """The `(0xXXXXXXXX)` IDCODE suffix of a quartus_stp device name, or None."""
    match = _IDCODE.search(name)
    return int(match[1], 16) if match else None


def select_device(names, idcode: int = DE2_115_IDCODE) -> str:
    """The one listed name whose IDCODE suffix is `idcode`; ValueError otherwise."""
    matches = [name for name in names if device_idcode(name) == idcode]
    if len(matches) != 1:
        listed = "; ".join(names) or "no devices"
        raise ValueError(f"expected exactly one JTAG device with IDCODE 0x{idcode:08X}, found "
                         f"{len(matches)} (listed: {listed}); pass the exact name with "
                         "--device/--jtag-device")
    return matches[0]


def tool_path(quartus_bin, name):
    return str(Path(quartus_bin) / name) if quartus_bin else name


def list_devices(cable: str, quartus_bin=None, *, runner=run_tool) -> list[str]:
    """One bounded quartus_stp listing of the devices on `cable` (read-only)."""
    argv = [tool_path(quartus_bin, "quartus_stp"), "-t", str(SCRIPT), cable]
    try:
        rc, text = runner(argv, LIST_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"quartus_stp device listing on {cable} timed out after {LIST_TIMEOUT} s") from exc
    if rc:
        detail = next((l for l in text.splitlines() if l.startswith("N2D_ERROR ")), text.strip()[-200:])
        raise RuntimeError(f"quartus_stp device listing on {cable} exited {rc}: {detail}")
    return parse_devices(text)


def resolve(cable: str, quartus_bin=None, idcode: int = DE2_115_IDCODE, *, runner=run_tool) -> str:
    """The exact quartus_stp name of the `idcode` device on `cable`."""
    return select_device(list_devices(cable, quartus_bin, runner=runner), idcode)
