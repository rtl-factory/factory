# factory

A hardware development factory: humans decide, agents execute. Every change starts with a GitHub issue. Agents start at `AGENTS.md`.

## Inspirations

Our BKMs (Best Known Methods) build on these agent-skill packs. We adapt their ideas to hardware work and take each idea once; [wiki/bkm/SOURCES.md](wiki/bkm/SOURCES.md) maps which pack feeds which BKM.

- [tstack](https://github.com/t3dotgg/tstack) by Theo Browne: PR review, filing, CI monitoring and shipping
- [pstack](https://github.com/cursor/plugins/tree/main/pstack) by Lauren Tan (`poteto`): planning, code quality and running parallel work
- [Skills for Real Engineers](https://github.com/mattpocock/skills) by Matt Pocock: planning interviews, specs, TDD and codebase architecture
- [firstmate](https://github.com/kunchenguid/firstmate) by Kun Chen: one captain, one first mate, a crew of agents

## Layout

First choose the project, then the type of work, then the block (an IP or unit):

```
src/<project>/<type>/<block>/
```

Every document lives in `wiki/` at the path that mirrors its code: `src/<project>/<type>/<block>/` is documented in `wiki/src/<project>/<type>/<block>/`, and a tool in `tools/<tool>/` in `wiki/tools/<tool>/`. The rule, and how to tell a repo tool from a project tool, is in [wiki/bkm/workflow/repo-layout.md](wiki/bkm/workflow/repo-layout.md).

```
factory/
├── src/                Projects: design, models and verification code
│   ├── <project>/      One folder per project, e.g. nand2mario/, common/
│   │   ├── arch/       Architecture models
│   │   ├── rtl/        Hardware implementation
│   │   ├── dv/         Simulation tests (cocotb)
│   │   ├── fv/         Formal verification
│   │   ├── fpga/       Board tops, Quartus settings, pins, constraints (one folder per board)
│   │   ├── cfg/        Simulation targets and per-board build config
│   │   ├── sw/         Software that runs on the design
│   │   └── tools/      Project tools: bound to this project or to one of its blocks
│
├── wiki/               Documentation, published as the wiki site (mkdocs.yml)
│   ├── src/            Specs, plans, PRDs per project, block and project tool (mirrors src/)
│   ├── specs/          External reference specifications
│   ├── bkm/            Best Known Methods, one directory per kind of work
│   ├── project/        Glossary and decisions (ADRs)
│   └── tools/          Repo tools' PRDs and specs (mirrors tools/)
│
├── cfg/                Project settings and limits
├── tools/              Repo tools: tools any project can use, one folder each
├── .claude/            Agent skills that route to BKMs (source of truth)
├── .agents/skills      Symlink to .claude/skills, for Codex
├── .github/            CI workflows and issue/PR templates
└── worktrees/          Separate checkouts for development
```
