# factory

## Layout

First choose the type of work, then choose the hardware block:

```
src/<type>/<block>/
```

```
factory/
├── src/                Design, models, and verification code
│   ├── arch/           Architecture models
│   ├── rtl/            Hardware implementation
│   ├── dv/             Simulation tests
│   ├── fv/             Formal verification
│   └── syn/            Synthesis setup and constraints
│
├── wiki/               Documentation
│   ├── src/            Documentation for the hardware blocks (mirrors src/)
│   ├── specs/          External reference specifications
│   ├── bkm/            Working methods and guides
│   ├── project/        Repository layout and design decisions
│   ├── tools/          Documentation for the tools
│   └── tracker/        Metrics and dashboards
│
├── cfg/                Project settings and limits
├── tools/              Scripts and shared automation
├── .claude/            Agent skills (source of truth), agents, and settings
├── .agents/            Symlink to .claude/skills for Codex
├── .github/            CI workflows and issue/PR templates
└── worktrees/          Separate checkouts for development
```
amichai was here
