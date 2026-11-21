# factory

A hardware development factory: humans decide, agents execute. Every change starts with a GitHub issue. Agents start at `AGENTS.md`, which Claude Code and Codex read natively (Claude Code reads it when no `CLAUDE.md` exists, [docs](https://code.claude.com/docs/en/memory)).

## Inspirations

Our BKMs (Best Known Methods) build on these agent-skill packs. We adapt their ideas to hardware work and take each idea once; [wiki/bkm/SOURCES.md](wiki/bkm/SOURCES.md) maps which pack feeds which BKM.

- [tstack](https://github.com/t3dotgg/tstack) by Theo Browne: PR review, filing, CI monitoring and shipping
- [pstack](https://github.com/cursor/plugins/tree/main/pstack) by Lauren Tan (`poteto`): planning, code quality and running parallel work
- [Skills for Real Engineers](https://github.com/mattpocock/skills) by Matt Pocock: planning interviews, specs, TDD and codebase architecture
- [firstmate](https://github.com/kunchenguid/firstmate) by Kun Chen: one captain, one first mate, a crew of agents

## Layout

Code is laid out by project, then discipline, then the block or script:

```
src/<project>/<discipline>/<name>/      disciplines: rtl, dv, fv, fpga, scripts, sw
tools/<tool>/                           tools any project can use
```

The wiki mirrors both trees down to the same folder, so every document sits at the path of the code it describes, and each discipline has its standard documents: the design's HAS and each block's MAS for `rtl`, a DV plan, an FV plan, a board spec, and a PRD and spec for scripts, software and tools. The rules are in [wiki/bkm/layout.md](wiki/bkm/layout.md).

```
factory/
├── src/                        Projects: design, models, verification and scripts
│   └── <project>/              One folder per project, e.g. nand2mario/, common/
│       ├── rtl/<block>/        Hardware implementation
│       ├── dv/<block>/         Simulation tests (cocotb)
│       ├── fv/<block>/         Formal verification
│       ├── fpga/<board>/       Board tops, Quartus settings, pins, constraints
│       ├── scripts/<script>/   Host scripts bound to this project or one of its blocks
│       ├── sw/<name>/          Software that runs on the design's own CPU
│       └── cfg/                Simulation targets and per-board build config (not a discipline)
│
├── tools/                      Tools any project can use
│   └── <tool>/                 e.g. sim/, toolchain/, pages/
│
├── wiki/                       Documentation, published as the wiki site (mkdocs.yml)
│   ├── src/                    Mirrors src/
│   │   └── <project>/          The project's landing page
│   │       ├── rtl/has.md          HAS: the design's high-level architecture
│   │       ├── rtl/<block>/        mas.md: the block's micro-architecture
│   │       ├── dv/<block>/         dv-plan.md
│   │       ├── fv/<block>/         fv-plan.md
│   │       ├── fpga/<board>/       board-spec.md
│   │       ├── scripts/<script>/   prd.md, spec.md
│   │       └── sw/<name>/          prd.md, spec.md
│   ├── tools/                  Mirrors tools/
│   │   └── <tool>/             prd.md, spec.md
│   ├── specs/                  External reference specifications
│   ├── bkm/                    Best Known Methods, one file per area
│   └── project/                Glossary and decisions (ADRs)
│
├── cfg/                        Factory settings and limits
├── .claude/                    Agent skills that route to BKMs (source of truth)
├── .agents/skills              Symlink to .claude/skills, for Codex
├── .github/                    CI workflows and issue/PR templates
└── worktrees/                  Separate checkouts for development
```
