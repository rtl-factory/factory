"""CPU software placement facts; no generated donor package dependency."""
import json
from pathlib import Path
_FACTS = json.loads((Path(__file__).resolve().parents[3] / "cfg/cpu-software.json").read_text())
globals().update(_FACTS)
PROFILE_IDS = {'dmg-direct-v1': 1, 'dmg-loader-v1': 2}
IMAGE_BYTES = {name: 32768 for name in PROFILE_IDS}
CARTRIDGE_BYTES = {name: bytes(3) for name in PROFILE_IDS}
