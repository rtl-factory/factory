"""Find a board on the Quartus JTAG chain and program it.

Board-independent Quartus facts shared by the board console and the project
board scripts: the plain `jtagconfig` listing names each cable and the IDCODEs
on its chain; `quartus_stp` names a device after the part list it associates
with the IDCODE, and that list differs between Quartus installs (the DE2-115 is
`@1: EP4CE115 (0x020F70DD)` on one host and
`@1: 10CL120(Y|Z)/EP3C120/.. (0x020F70DD)` on another), so a device name is
read from the cable's listing and chosen by IDCODE; and `quartus_pgm` loads a
SOF into the FPGA's SRAM. Exactly one match is required everywhere: nothing is
guessed. Spec: wiki/tools/board/spec.md#jtag-chain.
"""
from __future__ import annotations

from pathlib import Path
import re
import subprocess

LIST_TIMEOUT = 30
SCRIPT = Path(__file__).resolve().with_name("jtag_devices.tcl")
_IDCODE = re.compile(r"\(0x([0-9A-Fa-f]{8})\)\s*$")


def run_tool(argv, timeout):
    result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, errors="replace", timeout=timeout, check=False)
    return result.returncode, result.stdout


def tool_path(quartus_bin, name):
    return str(Path(quartus_bin) / name) if quartus_bin else name


def idcode_text(idcode) -> str:
    """An IDCODE as eight upper-case hex digits, from an int or a hex string."""
    value = int(idcode, 16) if isinstance(idcode, str) else int(idcode)
    return f"{value:08X}"


def parse_jtagconfig(text):
    """[(cable, [(idcode, name), ...]), ...] from plain jtagconfig output."""
    chains = []
    for line in text.splitlines():
        cable = re.fullmatch(r"\s*[0-9]+\)\s+(.+?)\s*", line)
        if cable:
            chains.append((cable[1], []))
            continue
        device = re.fullmatch(r"\s+([0-9A-Fa-f]{8})\s+(.*?)\s*", line)
        if device and chains:
            chains[-1][1].append((device[1].upper(), device[2]))
    return chains


def select_cable(chains, idcode) -> str:
    """The one cable whose chain is exactly the board FPGA; ValueError otherwise."""
    idcode = idcode_text(idcode)
    if len(chains) != 1:
        names = ", ".join(c for c, _ in chains) or "none"
        raise ValueError(f"expected exactly one JTAG cable, found {len(chains)} ({names}); "
                         "connect only the board's USB-Blaster")
    cable, devices = chains[0]
    if [d for d, _ in devices] != [idcode]:
        seen = ", ".join(d for d, _ in devices) or "no device"
        raise ValueError(f"{cable}: expected the one device {idcode}, found {seen}; "
                         "check the board power, the USB-Blaster and the selected board")
    return cable


def find_cable(idcode, quartus_bin=None, *, runner=run_tool) -> str:
    """One bounded `jtagconfig` run, then select_cable."""
    try:
        rc, text = runner([tool_path(quartus_bin, "jtagconfig")], LIST_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"jtagconfig timed out after {LIST_TIMEOUT} s") from exc
    if rc:
        raise RuntimeError(f"jtagconfig exited {rc}: {text.strip()[:200]}")
    return select_cable(parse_jtagconfig(text), idcode)


def parse_devices(text: str) -> list[str]:
    """Device names from the `N2A_DEVICE ` lines of jtag_devices.tcl output."""
    return [line[len("N2A_DEVICE "):].strip() for line in text.splitlines()
            if line.startswith("N2A_DEVICE ") and line[len("N2A_DEVICE "):].strip()]


def device_idcode(name: str) -> int | None:
    """The `(0xXXXXXXXX)` IDCODE suffix of a quartus_stp device name, or None."""
    match = _IDCODE.search(name)
    return int(match[1], 16) if match else None


def select_device(names, idcode) -> str:
    """The one listed name whose IDCODE suffix is `idcode`; ValueError otherwise."""
    wanted = int(idcode_text(idcode), 16)
    matches = [name for name in names if device_idcode(name) == wanted]
    if len(matches) != 1:
        listed = "; ".join(names) or "no devices"
        raise ValueError(f"expected exactly one JTAG device with IDCODE 0x{wanted:08X}, found "
                         f"{len(matches)} (listed: {listed}); pass the exact name with "
                         "--device/--jtag-device")
    return matches[0]


def list_devices(cable: str, quartus_bin=None, *, runner=run_tool) -> list[str]:
    """One bounded quartus_stp listing of the devices on `cable` (read-only)."""
    argv = [tool_path(quartus_bin, "quartus_stp"), "-t", str(SCRIPT), cable]
    try:
        rc, text = runner(argv, LIST_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"quartus_stp device listing on {cable} timed out after {LIST_TIMEOUT} s") from exc
    if rc:
        detail = next((l for l in text.splitlines() if l.startswith("N2A_ERROR ")), text.strip()[-200:])
        raise RuntimeError(f"quartus_stp device listing on {cable} exited {rc}: {detail}")
    return parse_devices(text)


def resolve(cable: str, quartus_bin=None, idcode=0, *, runner=run_tool) -> str:
    """The exact quartus_stp name of the `idcode` device on `cable`."""
    return select_device(list_devices(cable, quartus_bin, runner=runner), idcode)


def program_command(sof, cable: str, quartus_bin=None) -> list[str]:
    """The vendor SRAM-programming command for one SOF through one cable."""
    return [tool_path(quartus_bin, "quartus_pgm"), "-c", cable, "-m", "jtag", "-o", f"p;{sof}@1"]


def program_succeeded(returncode: int, text: str) -> bool:
    """quartus_pgm can exit 0 without programming; its success line is required too."""
    return returncode == 0 and "Programmer was successful" in text
