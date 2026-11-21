"""Build a small probe using the exact production capture state machine."""
import hashlib
import json
from pathlib import Path
import subprocess
from src.nand2doom.scripts.sw_build.n2d_image import _confined_output, _tool, _version

ROOT = Path(__file__).resolve().parents[4]
SOURCE = ROOT / 'src/nand2doom/dv/software'
PRODUCTION = ROOT / 'src/nand2doom/sw/doom'


def expected_results():
    rows = [(0,0,0,0), (0,0,0,0), (3,1,0,0)]
    for _ in range(2):
        rows += [(0,0,1,0), (0,0,2,0), (3,4,2,0), (0,0,2,0)]
    rows += [(3,3,0,0), (3,2,0,0), (3,3,0,0)]
    rows += [(0,0,0,0)] * 4 + [(0,0,1,0)] * 2
    values = [v for row in rows for v in row]
    values += [512,2,0,0x4332444e, 0,0,1,0, 3,4,1,0]
    values += [0,0,0,0, 3,4,0,0, 3,4,1,0, 3,4,1,0, 0,0,1,0, 0,0,2,0]
    values += [0,0,1,0]  # Why: models the eligible request the scene service injects, acquired before READY, the ordering the capture must handle
    values += [len(values)]
    return values


def compile_probe(output, *, host=False):
    output = _confined_output(output, ROOT)
    output.mkdir(parents=True, exist_ok=True)
    inputs=output/'inputs'
    inputs.mkdir(exist_ok=True)
    source_hashes={}
    for path in (SOURCE/'capture_probe.c', SOURCE/'clock.ld', SOURCE/'clock_start.S',
                 PRODUCTION/'capture.c', PRODUCTION/'capture.h'):
        data=path.read_bytes()
        (inputs/path.name).write_bytes(data)
        source_hashes[str(path.relative_to(ROOT))]=hashlib.sha256(data).hexdigest()
    gcc = _tool('CC' if host else 'N2D_RISCV_GCC', 'cc' if host else 'riscv64-unknown-elf-gcc')
    artifact = output / ('capture_probe.so' if host else 'capture_probe.elf')
    flags = ['-Os','-ffreestanding','-fno-builtin','-Wall','-Wextra','-Werror',
             '-I',str(inputs)]
    if host:
        flags += ['-DN2D_CAPTURE_HOST','-shared','-fPIC']
    else:
        flags += ['-march=rv32im','-mabi=ilp32','-fno-pic','-fno-pie',
                  '-msmall-data-limit=0','-mno-relax','-nostdlib','-nostartfiles',
                  '-Wl,--build-id=none','-Wl,--fatal-warnings',
                  f'-Wl,-T,{inputs / "clock.ld"}',str(inputs / 'clock_start.S')]
    command=[gcc,*flags,str(inputs/'capture_probe.c'),str(inputs/'capture.c'),'-o',str(artifact)]
    subprocess.run(command,check=True,timeout=30)
    if not host:
        binary=output/'capture_probe.bin'
        subprocess.run([_tool('N2D_RISCV_OBJCOPY','riscv64-unknown-elf-objcopy'),
                        '-O','binary',str(artifact),str(binary)],check=True,timeout=30)
        artifact=binary
    (output/'manifest.json').write_text(json.dumps(dict(command=command,compiler=_version(gcc),
        source_sha256=source_hashes,sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
        expected=expected_results()),indent=2)+'\n')
    return artifact, expected_results()
