---
status: active
upstream: cursor/plugins pstack/skills/principle-type-system-discipline/SKILL.md @ aaeb0ea (MIT, see ../LICENSE-pstack)
---

# Type System Discipline

Apply when designing types, reviewing a function signature, or writing code in any statically-typed language. Make illegal states unrepresentable, brand semantic primitives, parse external data at boundaries, exhaust variants, derive from authoritative schemas.

Make the compiler reject impossible states, mismatched primitives, and unhandled variants.

- **Brand semantic primitives.** If two arguments share a primitive type but mean different things, give each its own type. Validate once at creation.
- **External data is untyped until parsed.** JSON, RPC payloads, CLI args, config, environment variables, and database rows go through a parse function at the boundary that returns the typed model.
- **Derive types from authoritative schemas.** When a schema file owns a shape, derive the type from it instead of hand-rolling a parallel one.

**Factory-local:** in SystemVerilog this means `typedef enum` for states and opcodes, packed `struct`s for buses and fields, and one package that owns each shared type.
