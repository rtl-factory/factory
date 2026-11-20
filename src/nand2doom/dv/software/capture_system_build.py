"""Small real-MMIO RV32IM capture firmware for the public-port system test."""
import hashlib
import json
from pathlib import Path
import subprocess
from src.nand2doom.scripts.sw_build.n2d_image import _confined_output, _tool, _version
from .capture_build import ROOT, SOURCE, PRODUCTION


def build(output):
    output=_confined_output(output,ROOT);output.mkdir(parents=True,exist_ok=True)
    inputs=output/'inputs'
    inputs.mkdir(exist_ok=True)
    source_hashes={}
    for path in (SOURCE/'clock.ld', SOURCE/'clock_start.S', SOURCE/'capture_system.c',
                 PRODUCTION/'capture.c', PRODUCTION/'capture.h',
                 PRODUCTION/'clock.c', PRODUCTION/'clock.h'):
        data=path.read_bytes()
        (inputs/path.name).write_bytes(data)
        source_hashes[str(path.relative_to(ROOT))]=hashlib.sha256(data).hexdigest()
    gcc=_tool('N2D_RISCV_GCC','riscv64-unknown-elf-gcc')
    elf=output/'capture_system.elf';binary=output/'capture_system.bin'
    command=[gcc,'-Os','-march=rv32im','-mabi=ilp32','-ffreestanding','-fno-builtin',
        '-fno-pic','-fno-pie','-msmall-data-limit=0','-mno-relax','-nostdlib','-nostartfiles',
        '-Wall','-Wextra','-Werror','-Wl,--build-id=none','-Wl,--fatal-warnings',
        f'-Wl,-T,{inputs / "clock.ld"}','-I',str(inputs),str(inputs/'clock_start.S'),
        str(inputs/'capture_system.c'),str(inputs/'capture.c'),str(inputs/'clock.c'),
        '-o',str(elf)]
    subprocess.run(command,check=True,timeout=30)
    subprocess.run([_tool('N2D_RISCV_OBJCOPY','riscv64-unknown-elf-objcopy'),
                    '-O','binary',str(elf),str(binary)],check=True,timeout=30)
    symbols=subprocess.run([_tool('N2D_RISCV_NM','riscv64-unknown-elf-nm'),
        '-S','--defined-only',str(elf)],check=True,capture_output=True,text=True,timeout=30).stdout
    rows=[line.split() for line in symbols.splitlines() if line.endswith(' n2d_capture')]
    assert len(rows)==1 and int(rows[0][1],16)==1120 and rows[0][2]=='B'
    address=int(rows[0][0],16)
    (output/'manifest.json').write_text(json.dumps(dict(command=command,compiler=_version(gcc),
        address=address,source_sha256=source_hashes,
        sha256=hashlib.sha256(binary.read_bytes()).hexdigest()),indent=2)+'\n')
    return binary.read_bytes(),address
