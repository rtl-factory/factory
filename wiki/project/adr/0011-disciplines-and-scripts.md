# Disciplines, not types; code bound to a project is a script, and only `tools/` holds tools

The second level of a project's path, `src/<project>/<discipline>/`, is called a discipline: `arch`, `rtl`, `dv`, `fv`, `fpga` and `scripts`. The word "type" clashed with the `type:` issue label and said nothing about what the folder holds. Code bound to one project, or to a block of `common`, is a script in that project's `scripts` discipline (`src/<project>/scripts/<script>/`, documented in `wiki/src/<project>/scripts/<script>/`), and "tool" means only code in the top-level `tools/`. This replaces the "project tool" in `src/<project>/tools/` from ADR 0010, whose shared name made "where does this tool live" ambiguous. The mirror rule of ADR 0010 is unchanged.

_The discipline list above is amended by [ADR 0012](0012-software-discipline.md) (`sw` is a discipline) and [ADR 0013](0013-architecture-lives-in-rtl-has.md) (no `arch` discipline; architecture is the rtl HAS)._
