# CPU software provenance

Opcode forms in `opcodes.json` adapt the MIT mgbdis base/CB dictionaries.
The original notice is retained verbatim in [MGBDIS_LICENSE.md](MGBDIS_LICENSE.md).
Illegal slots and disassembler aliases are omitted; accumulator, full-address
LDH and address-based JR spellings follow this assembler contract. No executable
disassembler, ROM, assets or upstream tests are imported. The original parser,
expressions and linker do not change the opcode data license.

The [immutable upstream record](mgbdis-provenance.json) is copied from donor
tools/provenance.json. The donor file hashes and import pin are in
[the import manifest](../../cfg/cpu-import.json).
RGBDS is not a dependency of this slice. No external oracle is installed.
