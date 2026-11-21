---
status: draft
upstream: host routing, SOF handoff and Quartus report gates adapted from amichai-bd/nand2anything wiki/bkm/bkm_host.md, wiki/bkm/bkm_fpga.md and src/nand2doom/tools/quartus.py @ aeebe92 (private, adapted at the owner's direction; see ../SOURCES.md)
---

# FPGA

How a design is built with Quartus for the DE2-115 and DE10-Lite, gated, handed to the board host and programmed. The tools and their install are in `tools/toolchain/`.

## Rules

- Keep each board's facts in one config file per board (device, top level, clock, pins, I/O standards, Quartus source list, reviewed warnings, expected RAM inference) in `src/<project>/cfg/`, and its Quartus settings, constraints and board top in `src/<project>/fpga/<board>/`. Why: the build gates read them, and a board difference lives in one place.
- Keep board wrappers apart from reusable RTL. Why: reusable RTL is verified with Verilator, which never sees vendor primitives.
- Never simulate with Quartus or its vendor libraries; wrap a vendor primitive behind a synthesizable model. Why: simulation runs on Verilator only (ADR 0009).
- The build **must** fail on any Quartus warning not listed as reviewed in the board's config, on RAM inferred other than as the config expects, and on timing not met at every timing corner. Why: a warning nobody reviewed, a RAM turned into logic, or one failing corner is a bug that shows up only on the board.
- Build on any Linux host that passes the readiness check; program a board only on the host it is wired to (the board host). Why: builds need compute; programming needs the cable.
- Hand a programming file to the board host with its SHA-256, the commit and input manifest it was built from, the Quartus version, and its timing and warning evidence; the board host verifies the hash and target before programming. Why: the board must run exactly what was built and reviewed.
- Program a board only on an issue that authorizes it, one job at a time per board. Why: two jobs on one board corrupt each other's evidence.
- Report the build, timing, programming and what was observed on the board as separate evidence; a build host never claims board evidence. Why: each is a different fact with a different proof.
- Never restart a fit that is progressing in order to move it to another host. Why: the restart costs more than waiting.
- Describe each board top in its board spec, `wiki/src/<project>/fpga/<board>/board-spec.md` ([standard documents](layout.md#standard-documents)), and refresh its budget section from the build evidence whenever the board top, its config or its constraints change. Why: the board's facts and its last known-good numbers are in one place a reviewer can read.

## Steps

1. Check the host: the readiness check passes.
2. Build headless from the board's config into a scratch output directory, never inside source folders.
3. Apply the gates: reviewed warnings, expected RAM inference, timing at every corner. Fix the design or the config (with review), never the gate.
4. Record the evidence in the pull request: fit results (resource use), timing per corner, warnings summary, Quartus version, programming-file hash.
5. On an authorizing issue, hand the programming file to the board host, verify it, program, and observe the board.

## Exit criteria

- The build passes every gate, its evidence is in the pull request, and any board result is reported as board evidence from the board host.
