# Epics and epic branches are opt-in

By default an issue branch merges straight into `main` under full CI. Only when several issues cannot reach `main` one at a time (each would break `main` until the others land) do we open an epic: its issue branches merge into an epic branch under light CI, and the epic branch merges into `main` under full CI once healthy. We rejected always-on epic branches because long-lived integration branches drift from `main` and delay real CI.
