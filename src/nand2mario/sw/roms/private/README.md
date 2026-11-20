# Private cartridge backups: NOT FOR REDISTRIBUTION

**Everything in this directory is proprietary content that must never be
published.** It lives here only because this repository is private.

`super-mario-land-rev1.gb` is the repository owner's (Amichai Ben-David)
personal backup of a Super Mario Land cartridge he bought and owns. The game is
copyrighted Nintendo software. It is kept so he can run his own copy on his own
FPGA board.

Rules for agents and contributors:

- Never redistribute, upload, attach, or mirror this file or anything derived
  from it (screenshots, GIFs, frame hashes tied to images, disassembly).
- The owner approved gameplay captures in `media/` and the README for this
  private repository only (#438).
- Never include it, or captures of it, in public showcase media, public README
  galleries, the wiki, release artifacts, Git LFS, CI caches, or any public
  fork or mirror. If this repository is ever made public, delete this
  directory and purge it from history first.
- Do not add it to `src/nand2mario/cfg/games.json` or any automated
  qualification or gallery list.
- `media/sml-board-de2-115.gif`, `media/sml-board-de2-115-webcam.jpg` and
  `media/sml-board-de2-115-provenance.json` are the DE2-115 **board** captures
  (#356), kept here under the same private-only rule as the simulation media.
- Other simulation and board evidence for it (raw records, logs, other
  photographs) stays in the untracked `workdir/`.

The repository `.gitignore` ignores `*.gb` and `private/`. This file was
force-added on purpose (#438). Do not loosen those ignore rules.

## Media captions

These captions are owned here; the root README shows the media with one-line
badges and links to this section. Private repository only, as above.

**BOARD: physical Terasic DE2-115, 2026-10-10**
(`media/sml-board-de2-115.gif`, `media/sml-board-de2-115-webcam.jpg`). The
board ran SOF `e709dc42…a601e4` (25 MHz, all corners met), built from source
commit `132ea2b9` ([#397](https://github.com/amichai-bd/nand2anything/pull/397)).
The cartridge was loaded over the UART and played with the JTAG keyboard. The
four GIF frames are snapshots of the displayed 160x144 frame, read back over
the UART (not an electrical VGA capture), at 1000 ms editorial spacing: title,
Start, Right held, A. Mario lost a life to the Goomba before the A frame, which
shows the restart (MARIO×01). The photograph is an unchanged board-webcam
still. [Provenance](media/sml-board-de2-115-provenance.json).

**Verilator RTL simulation of nand2anything hardware, framebuffer extracted**
(`media/sml-gameplay.gif`, `media/sml-frame-{0150,0330,0420,0560}.png`). Every
frame is the 160x144 `pixel_shade_o` stream of the full `n2m_system` RTL (not
an emulator), 638 consecutive frames at 25 MHz with scripted joypad input:
power-on, title, Start, then Right held with A jumps. The GIF holds 319 frames,
every second captured frame (0, 2, ..., 636), at about 33.4 ms each (3/3/4 cs
delays, 10.66 s total), which is real-time speed; DMG palette, 2x scale. The
stills are captured frames 150 (title), 330 (mid-jump in World 1-1, time 397),
420 (running right) and 560 (jumping over a pipe). The DMG boot ROM is not
modelled, so there is no Nintendo logo scroll; the cartridge starts at 0x0100.
Not a board capture. **No sound:** the APU is silent (full register behaviour,
no audio output), so the GIF has no audio by design. Recorded at commit
`6554b95` ([#439](https://github.com/amichai-bd/nand2anything/pull/439)); the
capture needs the owner's private ROM and is not reproducible from a public
checkout.

## Pins

| Field | Value |
|---|---|
| Size | 65,536 bytes |
| SHA-256 | `49fbd2f61f953d5ef28cab73e357e524c3009ad19fcf30f6a9ee0ae273be41dc` |
| SHA-1 | `418203621b887caa090215d97e3f509b79affd3e` |
| CRC32 | `2c27ec70` |
| Title (0x0134) | `SUPER MARIOLAND` |
| Cartridge type (0x0147) | 0x01, MBC1 |
| ROM size (0x0148) | 0x01, 64 KiB (4 x 16 KiB banks) |
| RAM size (0x0149) | 0x00, none |
| Version (0x014C) | 0x01 (Rev 1) |
| Header checksum | 0x9D (valid) |
| Global checksum | 0x5ECF (valid) |

Check it with `sha256sum src/nand2mario/sw/roms/private/super-mario-land-rev1.gb`.
