"""Production firmware arithmetic executed by the real RV32IM core."""
import hashlib
import json
import os
from pathlib import Path

import cocotb
from cocotb.clock import Clock

from src.nand2doom.dv.core.test_n2d_rv32im_core import _run, _word
from .clock_build import compile_probe


@cocotb.test(timeout_time=20, timeout_unit="ms")
async def compiled_doom_clock_contract(dut):
    artifact, expected = compile_probe(Path.cwd() / "clock_probe")
    image = artifact.read_bytes()
    image += bytes(-len(image) % 4)
    words = tuple(int.from_bytes(image[i:i + 4], "little") for i in range(0, len(image), 4))
    memory = image.ljust(0x10000, b"\0")
    cocotb.start_soon(Clock(dut.clk_i, 40, unit="ns").start())
    result = await _run(dut, words, initial_memory=memory, stop_trap=True,
                        max_cycles=int(os.environ["DV_MAX_CYCLES"]))
    assert result.observations[-1].cause == 3, "probe did not reach terminal ebreak"
    assert all(not item.trap for item in result.observations[:-1])
    observed = [_word(result.memory, 0x8000 + 4 * index) for index in range(len(expected))]
    for index, (want, got) in enumerate(zip(expected, observed, strict=True)):
        assert want == got, f"clock output[{index}] expected={want} observed={got}"
    (artifact.parent / "observed.json").write_text(json.dumps(observed) + "\n")
    cocotb.log.info("CLOCK PASS seed=%s max_cycles=%s outputs=%d retirements=%d sha256=%s",
                    os.environ["DV_SEED"], os.environ["DV_MAX_CYCLES"], len(observed),
                    len(result.observations), hashlib.sha256(artifact.read_bytes()).hexdigest())
