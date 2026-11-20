# factory

A hardware development factory: humans decide, agents execute. Every change starts with a GitHub issue. Agents start at `AGENTS.md`.

## Inspirations

Our BKMs (Best Known Methods) build on these agent-skill packs. We adapt their ideas to hardware work and take each idea once; [wiki/bkm/SOURCES.md](wiki/bkm/SOURCES.md) maps which pack feeds which BKM.

- [tstack](https://github.com/t3dotgg/tstack) by Theo Browne: PR review, filing, CI monitoring and shipping
- [pstack](https://github.com/cursor/plugins/tree/main/pstack) by Lauren Tan (`poteto`): planning, code quality and running parallel work
- [Skills for Real Engineers](https://github.com/mattpocock/skills) by Matt Pocock: planning interviews, specs, TDD and codebase architecture
- [firstmate](https://github.com/kunchenguid/firstmate) by Kun Chen: one captain, one first mate, a crew of agents

## Layout

Code is laid out by project, then discipline, then the block or script:

```
src/<project>/<discipline>/<name>/      disciplines: arch, rtl, dv, fv, fpga, scripts
tools/<tool>/                           tools any project can use
```

The wiki mirrors both trees down to the same folder, so every document sits at the path of the code it describes: a block's spec and plans, a script's or a tool's PRD and spec. The rules are in [wiki/bkm/workflow/repo-layout.md](wiki/bkm/workflow/repo-layout.md).

```
factory/
├── src/                        Projects: design, models, verification and scripts
│   └── <project>/              One folder per project, e.g. nand2mario/, common/
│       ├── arch/<block>/       Architecture models
│       ├── rtl/<block>/        Hardware implementation
│       ├── dv/<block>/         Simulation tests (cocotb)
│       ├── fv/<block>/         Formal verification
│       ├── fpga/<board>/       Board tops, Quartus settings, pins, constraints
│       ├── scripts/<script>/   Scripts bound to this project or one of its blocks
│       ├── cfg/                Simulation targets and per-board build config
│       └── sw/                 Software that runs on the design
│
├── tools/                      Tools any project can use
│   └── <tool>/                 e.g. sim/, toolchain/, pages/
│
├── wiki/                       Documentation, published as the wiki site (mkdocs.yml)
│   ├── src/                    Mirrors src/
│   │   └── <project>/          The project's landing page
│   │       ├── arch/<block>/       Architecture specs
│   │       ├── rtl/<block>/        Block specs
│   │       ├── dv/<block>/         Verification plans
│   │       ├── fv/<block>/         Formal plans
│   │       ├── fpga/<board>/       Board docs
│   │       └── scripts/<script>/   prd.md, spec.md
│   ├── tools/                  Mirrors tools/
│   │   └── <tool>/             prd.md, spec.md
│   ├── specs/                  External reference specifications
│   ├── bkm/                    Best Known Methods, one directory per kind of work
│   └── project/                Glossary and decisions (ADRs)
│
├── cfg/                        Factory settings and limits
├── .claude/                    Agent skills that route to BKMs (source of truth)
├── .agents/skills              Symlink to .claude/skills, for Codex
├── .github/                    CI workflows and issue/PR templates
└── worktrees/                  Separate checkouts for development
```
