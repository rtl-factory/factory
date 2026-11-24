# Quartus build

One build tool for every project's board image: it stages a Quartus project from the project's manifest and the board's pin map, compiles it, audits its timing and gates the reports, then binds the passing SOF to its commit. It knows no design. Code: `tools/quartus/`.

- [Requirements](prd.md)
- [Spec](spec.md)
