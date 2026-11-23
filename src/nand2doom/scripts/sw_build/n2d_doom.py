#!/usr/bin/env python3
"""Fetch licensed inputs and build the nand2doom RV32IM Doom image.

Spec: wiki/src/nand2doom/scripts/sw_build/spec.md#doom-build.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import tarfile
import urllib.request
import zipfile


ENGINE_REVISION = "dcb7a8dbc7a16ce3dda29382ac9aae9d77d21284"
ENGINE_URL = f"https://github.com/ozkl/doomgeneric/archive/{ENGINE_REVISION}.tar.gz"
ENGINE_SHA256 = "1bd3f7f26220494159a38d71f2847ec81b58d6bbd7c7c8d81b08993018001148"
ENGINE_LICENSE = "GPL-2.0-or-later"
FREEDOOM_VERSION = "0.13.0"
FREEDOOM_URL = (
    "https://github.com/freedoom/freedoom/releases/download/v0.13.0/"
    "freedoom-0.13.0.zip"
)
FREEDOOM_SHA256 = "3f9b264f3e3ce503b4fb7f6bdcb1f419d93c7b546f4df3e874dd878db9688f59"
WAD_SHA256 = "7323bcc168c5a45ff10749b339960e98314740a734c30d4b9f3337001f9e703d"
WAD_ADDRESS = 0x02000000
WAD_BYTE_LENGTH = 28_795_076
WAD_END_ADDRESS = WAD_ADDRESS + WAD_BYTE_LENGTH
MEMORY_MAP_REVISION = "n2d-platform-v1"
PICOLIBC_VERSION = "1.8.6-2"
# Why: the pinned public inputs (sw/third_party/manifest.json) are not committed
# to the factory: the first build fetches each one from its pinned origin,
# checks its SHA-256 and keeps it here for later builds.
USER_AGENT = "rtl-factory-n2d-build/1"
VENDORED_DOWNLOADS = Path(__file__).resolve().parents[4] / "workdir/downloads/nand2doom"
PICOLIBC_URL = (
    "http://archive.ubuntu.com/ubuntu/pool/universe/p/picolibc/"
    "picolibc-riscv64-unknown-elf_1.8.6-2_all.deb"
)
PICOLIBC_SHA256 = "9563bbe39bbdf4eda1970ced8c54e5df47a112d91427fc4fc38100428fd23a9b"
PICOLIBC_ARTIFACT_SHA256 = {
    "lib/picolibc/riscv64-unknown-elf/lib/rv32im/ilp32/libc.a":
        "91a855bfce24342c2dce4bf5ad3f6a4f7bd573b4e29686bdb8fc84d54d491f53",
    "lib/picolibc/riscv64-unknown-elf/lib/rv32im/ilp32/libm.a":
        "8448849dbd49b2d6da85f2ead81e06b07de7a230e48fea4804f80fe498a6a673",
    "lib/picolibc/riscv64-unknown-elf/lib/rv32im/ilp32/libdummyhost.a":
        "49e93f26a014367e3b57b6ac0c78aa257e04777e4b26c2df6cd1f22ec5a5c914",
    "share/doc/picolibc-riscv64-unknown-elf/copyright":
        "73879138376be7ab8ccdb4eb116f11d215ff70210ca2ab5183b2b6a7276e52a5",
}
PICOLIBC_INCLUDE_ROOT = "lib/picolibc/riscv64-unknown-elf/include"
PICOLIBC_COPYRIGHT_PATH = "share/doc/picolibc-riscv64-unknown-elf/copyright"
PICOLIBC_INCLUDE_TREE_SHA256 = (
    "cd4d0ba9175675b6cf8c62f527502304cd0bc0c58a89e685360877c17f96ee1d"
)
# Ref: wiki/src/nand2doom/sw/doom/spec.md "Doom time contract", "Engine compiler
# experiments" and "Deterministic-time verification build": production keeps
# the real-time cycle clock; the verification-only deterministic variant
# derives Doom time from retired instructions (minstret) and pauses it across
# video waits, a reproducible time policy, not a cross-architecture frame/state
# guarantee; render verification fixes one game tic per display and requires
# state witnesses. Production and deterministic build the engine -O2 with
# engine-only LTO, the measured speed winner; render verification stays -Os
# because LTO inlines away the G_Ticker/D_Display call witnesses it needs.
VARIANTS = {
    "production": {
        "defines": [],
        "time_source": "mmio_cycle_counter_0x20000008",
        "output": "workdir/software/n2d_doom",
        "engine_opt": "O2", "engine_lto": True,
    },
    "deterministic": {
        "defines": ["-DN2D_DETERMINISTIC_TIME=1"],
        "time_source": "minstret_25000_per_ms_video_waits_paused",
        "output": "workdir/software/n2d_doom_deterministic",
        "engine_opt": "O2", "engine_lto": True,
    },
    "render-verification": {
        "defines": ["-DN2D_DETERMINISTIC_TIME=1", "-DN2D_VERIFY_SINGLETICS=1"],
        "time_source": "minstret_25000_per_ms_video_waits_paused",
        "output": "workdir/software/n2d_doom_render_verification",
        "engine_opt": "Os", "engine_lto": False,
    },
}
UPSTREAM_WARNING_EXCEPTIONS = (
    "-Wno-missing-field-initializers", "-Wno-sign-compare",
    "-Wno-implicit-fallthrough", "-Wno-unused-parameter",
    "-Wno-unused-but-set-parameter", "-Wno-unused-but-set-variable",
    "-Wno-unused-variable", "-Wno-unused-const-variable",
    "-Wno-enum-conversion", "-Wno-type-limits", "-Wno-format",
    "-Wno-dangling-pointer",
)

ENGINE_OBJECTS = """
dummy am_map doomdef doomstat dstrings d_event d_items d_iwad d_loop d_main
d_mode d_net f_finale f_wipe g_game hu_lib hu_stuff info i_cdmus i_endoom
i_joystick i_scale i_sound i_system i_timer memio m_argv m_bbox m_cheat
m_config m_controls m_fixed m_menu m_misc m_random p_ceilng p_doors p_enemy
p_floor p_inter p_lights p_map p_maputl p_mobj p_plats p_pspr p_saveg p_setup
p_sight p_spec p_switch p_telept p_tick p_user r_bsp r_data r_draw r_main
r_plane r_segs r_sky r_things sha1 sounds statdump st_lib st_stuff s_sound
tables v_video wi_stuff w_checksum w_file w_main w_wad z_zone i_input i_video
doomgeneric
""".split()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _confined_output(repository: Path, requested: Path) -> Path:
    repository = repository.resolve()
    root = (repository / "workdir").resolve()
    candidate = requested if requested.is_absolute() else repository / requested
    candidate = Path(os.path.abspath(candidate))
    try:
        relative = candidate.relative_to(root)
    except ValueError as error:
        raise ValueError(f"output must be below {root}") from error
    current = root
    for component in relative.parts:
        current = current / component
        if current.is_symlink():
            raise ValueError(f"output contains symbolic-link component: {current}")
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ValueError(f"output resolves outside {root}") from error
    return resolved


def _validate_output_tree(output: Path) -> None:
    if output.exists() and not output.is_dir():
        raise ValueError(f"output is not a directory: {output}")
    if not output.exists():
        return
    for child in output.rglob("*"):
        if child.is_symlink():
            raise ValueError(f"output contains symbolic-link child: {child}")
        try:
            child.resolve().relative_to(output)
        except ValueError as error:
            raise ValueError(f"output child resolves outside {output}: {child}") from error


def _temporary_beside(path: Path) -> Path:
    """A new, uniquely named file next to path, for writing before an atomic rename."""
    handle, name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".part")
    os.close(handle)
    # Why: mkstemp makes the file private; the pinned input keeps the usual mode.
    os.chmod(name, 0o644)
    return Path(name)


def _download(url: str, destination: Path, expected: str) -> None:
    if destination.is_symlink():
        raise ValueError(f"download destination is a symbolic link: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and _sha256(destination) == expected:
        return
    local = VENDORED_DOWNLOADS / destination.name
    if local.exists() and (local.is_symlink() or _sha256(local) != expected):
        raise ValueError(f"cached input changed: {local.name} (delete it to fetch it again)")
    # Why: parallel test targets may fetch the same input, so each writes its own
    # temporary and renames it into place.
    temporary = _temporary_beside(destination)
    try:
        if local.exists():
            shutil.copyfile(local, temporary)
        else:
            # Why: archive.ubuntu.com answers 403 to urllib's default Python-urllib agent.
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as out:
                shutil.copyfileobj(response, out)
        if _sha256(temporary) != expected:
            raise RuntimeError(f"digest mismatch for {url}")
        if not local.exists() and local != destination:
            VENDORED_DOWNLOADS.mkdir(parents=True, exist_ok=True)
            vendored = _temporary_beside(local)
            shutil.copyfile(temporary, vendored)
            vendored.replace(local)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def _seed_inputs(output: Path, repository: Path) -> None:
    """Hard-link the production output's pinned downloads and toolchain.

    Another variant's output then skips about 1.3 GB of fetching and
    extraction. Nothing is trusted: every download is digest-checked again
    and the toolchain's consumed artifacts and headers are verified as usual.
    """
    source = _confined_output(
        repository, repository / VARIANTS["production"]["output"])
    if source == output or not source.is_dir():
        return
    for tree in ("downloads", "toolchain"):
        root = source / tree
        if not root.is_dir() or (output / tree).exists():
            continue
        if tree == "toolchain" and not (root / TOOLCHAIN_LIBC).is_file():
            continue  # Why: a partial extraction must not be seeded into another output
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise ValueError(f"seed input is a symbolic link: {path}")
            target = output / tree / path.relative_to(root)
            if path.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif path.is_file() and not path.name.endswith(".part"):
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.link(path, target)
                except OSError:
                    shutil.copy2(path, target)


def _adapt_scene(text: str) -> str:
    old_declaration = "extern void N2D_RecordSceneReady(void);\n"
    if old_declaration in text:
        if text.count(old_declaration) != 1 or text.count("N2D_RecordSceneReady();") != 2:
            raise RuntimeError("ambiguous prior completed-scene hooks")
        text = text.replace(old_declaration, "extern void N2D_RecordSceneReady(int gameplay);\n")
        text = text.replace("N2D_RecordSceneReady();", "N2D_RecordSceneReady(gamestate == GS_LEVEL);")
    scene_declaration = "extern void N2D_RecordSceneReady(int gameplay);\n"
    if scene_declaration not in text:
        seam = '#include "doomstat.h"\n'
        if text.count(seam) != 1:
            raise RuntimeError("pinned d_main.c declaration seam changed")
        text = text.replace(seam, seam + scene_declaration, 1)
    normal_finish = "\tI_FinishUpdate ();              // page flip or blit buffer\n\treturn;"
    normal_hook = ("\tI_FinishUpdate ();              // page flip or blit buffer\n"
                   "\tN2D_RecordSceneReady(gamestate == GS_LEVEL);\n\treturn;")
    if normal_hook not in text:
        if text.count(normal_finish) != 1:
            raise RuntimeError("pinned d_main.c normal-finish seam changed")
        text = text.replace(normal_finish, normal_hook, 1)
    wipe_finish = "    } while (!done);\n}"
    wipe_hook = "    } while (!done);\n    N2D_RecordSceneReady(gamestate == GS_LEVEL);\n}"
    if wipe_hook not in text:
        if text.count(wipe_finish) != 1:
            raise RuntimeError("pinned d_main.c completed-wipe seam changed")
        text = text.replace(wipe_finish, wipe_hook, 1)
    if (text.count(scene_declaration) != 1 or text.count(normal_hook) != 1
            or text.count(wipe_hook) != 1
            or text.count("N2D_RecordSceneReady(gamestate == GS_LEVEL);") != 2):
        raise RuntimeError("ambiguous completed-scene hooks")
    return text


# Ref: wiki/src/nand2doom/sw/doom/spec.md "Direct framebuffer rendering": Doom
# draws straight into the back framebuffer bank; the platform requests a bank
# swap at frame end and, before the next frame's first screen access, waits for
# it and copies the displayed bank into the back bank.
BACK_FRAMEBUFFER = "(pixel_t *)0x10000000u"
ACQUIRE_DECLARATION = ("extern void N2D_FramebufferAcquire(void);\n"
                       "extern void N2D_FramebufferAcquireFrom(int first_row);\n")
DISPLAY_SEAM = ("    if (nodrawers)\n"
                "    \treturn;                    // for comparative timing / profiling\n")
WIPE_SEAM = "\twipestart = nowtime;\n"
# Ref: wiki/src/nand2doom/sw/doom/spec.md "Status-bar-only persistence": a
# full-width level view with no wipe, resize, menu or automap redraws every row
# above viewheight, so only the rows below it need the displayed image.
# Everything else copies it all.
DISPLAY_ACQUIRE = (
    "    // n2d: a full-width level view redraws rows [0, viewheight).\n"
    "    N2D_FramebufferAcquireFrom(gamestate == GS_LEVEL && gamestate == wipegamestate\n"
    "                               && gametic && !setsizeneeded && !automapactive\n"
    "                               && !menuactive && scaledviewwidth == SCREENWIDTH\n"
    "                               ? viewheight : 0);\n")
WIPE_ACQUIRE = "\tN2D_FramebufferAcquire();\n"


def _adapt_framebuffer_acquire(text: str) -> str:
    """Acquire the back bank before D_Display and before each wipe step draws."""
    # Why: a tree prepared before status-bar-only persistence lacks the new
    # declaration, so it is upgraded in place.
    if ACQUIRE_DECLARATION not in text:
        text = text.replace("extern void N2D_FramebufferAcquire(void);\n",
                            ACQUIRE_DECLARATION, 1)
    text = text.replace(DISPLAY_SEAM + "    N2D_FramebufferAcquire();\n",
                        DISPLAY_SEAM + DISPLAY_ACQUIRE)
    if ACQUIRE_DECLARATION not in text:
        seam = '#include "doomstat.h"\n'
        if text.count(seam) != 1:
            raise RuntimeError("pinned d_main.c declaration seam changed")
        text = text.replace(seam, seam + ACQUIRE_DECLARATION, 1)
    for seam, hook in ((DISPLAY_SEAM, DISPLAY_ACQUIRE), (WIPE_SEAM, WIPE_ACQUIRE)):
        if seam + hook not in text:
            if text.count(seam) != 1:
                raise RuntimeError("pinned d_main.c framebuffer-acquire seam changed")
            text = text.replace(seam, seam + hook, 1)
    if (text.count(ACQUIRE_DECLARATION) != 1 or text.count(DISPLAY_ACQUIRE) != 1
            or text.count("N2D_FramebufferAcquire();") != 1
            or text.count("N2D_FramebufferAcquireFrom(") != 2):
        raise RuntimeError("ambiguous framebuffer-acquire hooks")
    return text


def _adapt_direct_video(text: str) -> str:
    """Point I_VideoBuffer at DG_ScreenBuffer and make I_FinishUpdate a swap."""
    allocation = ("\tI_VideoBuffer = (byte*)Z_Malloc (SCREENWIDTH * SCREENHEIGHT, "
                  "PU_STATIC, NULL);  // For DOOM to draw on\n")
    direct = "\tI_VideoBuffer = (byte *)DG_ScreenBuffer;  // back framebuffer bank\n"
    if direct not in text:
        if text.count(allocation) != 1:
            raise RuntimeError("pinned i_video.c screen allocation seam changed")
        text = text.replace(allocation, direct, 1)
    release = "\tZ_Free (I_VideoBuffer);\n"
    if release in text:
        if text.count(release) != 1:
            raise RuntimeError("pinned i_video.c screen release seam changed")
        text = text.replace(release, "", 1)
    finish = ("void I_FinishUpdate (void)\n{\n"
              "    /* The frame is already in the back framebuffer bank. */\n"
              "\tDG_DrawFrame();\n}\n")
    if finish not in text:
        text, count = re.subn(
            r"void I_FinishUpdate \(void\)\n\{.*?\n\tDG_DrawFrame\(\);\n\}\n",
            lambda _: finish, text, count=1, flags=re.DOTALL)
        if count != 1:
            raise RuntimeError("pinned i_video.c finish-update seam changed")
    return text


# Ref: wiki/src/nand2doom/sw/doom/spec.md "Register-resident drawers": upstream
# reloads dc_source, dc_colormap (ds_*) from globals after every byte store,
# which may alias them. These bodies hold them in locals, consume the remainder
# first and unroll 4x. Columns encode the original 23 relevant bits modulo32,
# shifted by nine; every texture index and framebuffer byte stays identical.
DRAW_LOOPS_MARKER = "/* n2d: register-resident drawer */"
DRAW_COLUMN = DRAW_LOOPS_MARKER + """
void R_DrawColumn (void)
{
    int count = dc_yh - dc_yl;
    byte *dest;
    const byte *source = dc_source;
    const lighttable_t *colormap = dc_colormap;
    unsigned int frac, fracstep;

    if (count < 0)
	return;
#ifdef RANGECHECK
    if ((unsigned)dc_x >= SCREENWIDTH || dc_yl < 0 || dc_yh >= SCREENHEIGHT)
	I_Error ("R_DrawColumn: %i to %i at %i", dc_yl, dc_yh, dc_x);
#endif
    dest = ylookup[dc_yl] + columnofs[dc_x];
    // Keep the 23 significant fixed-point bits modulo32, shifted by nine.
    // Then frac >>25 is exactly the old (signed_frac >>16)&127 index.
    fracstep = (unsigned int)dc_iscale << 9;
    frac = ((unsigned int)dc_texturemid +
            ((unsigned int)dc_yl - (unsigned int)centery) *
            (unsigned int)dc_iscale) << 9;
    count++;
    int head = count & 3;
    while (head-- > 0)
    {
	*dest = colormap[source[frac>>25]];
	dest += SCREENWIDTH;
	frac += fracstep;
	count--;
    }
    while (count >= 4)
    {
	byte a = colormap[source[frac>>25]]; frac += fracstep;
	byte b = colormap[source[frac>>25]]; frac += fracstep;
	byte c = colormap[source[frac>>25]]; frac += fracstep;
	byte d = colormap[source[frac>>25]]; frac += fracstep;
	dest[0*SCREENWIDTH] = a;
	dest[1*SCREENWIDTH] = b;
	dest[2*SCREENWIDTH] = c;
	dest[3*SCREENWIDTH] = d;
	dest += 4*SCREENWIDTH;
	count -= 4;
    }

}
"""
DRAW_SPAN = DRAW_LOOPS_MARKER + """
void R_DrawSpan (void)
{
    unsigned int position, step;
    byte *dest;
    int count;
    const byte *source = ds_source;
    const lighttable_t *colormap = ds_colormap;

#ifdef RANGECHECK
    if (ds_x2 < ds_x1 || ds_x1<0 || ds_x2>=SCREENWIDTH || (unsigned)ds_y>SCREENHEIGHT)
	I_Error( "R_DrawSpan: %i to %i at %i", ds_x1,ds_x2,ds_y);
#endif
    position = ((ds_xfrac << 10) & 0xffff0000) | ((ds_yfrac >> 6)  & 0x0000ffff);
    step = ((ds_xstep << 10) & 0xffff0000) | ((ds_ystep >> 6)  & 0x0000ffff);
    dest = ylookup[ds_y] + columnofs[ds_x1];
    count = ds_x2 - ds_x1 + 1;
    if (count <= 0) return;
#define N2D_SPOT(p) ((((p) >> 4) & 0x0fc0) | ((p) >> 26))
    int head = count & 3;
    while (head-- > 0)
    {
	*dest++ = colormap[source[N2D_SPOT(position)]];
	position += step;
	count--;
    }
    while (count >= 4)
    {
	byte a = colormap[source[N2D_SPOT(position)]]; position += step;
	byte b = colormap[source[N2D_SPOT(position)]]; position += step;
	byte c = colormap[source[N2D_SPOT(position)]]; position += step;
	byte d = colormap[source[N2D_SPOT(position)]]; position += step;
	dest[0] = a;
	dest[1] = b;
	dest[2] = c;
	dest[3] = d;
	dest += 4;
	count -= 4;
    }

#undef N2D_SPOT
}
"""
DRAW_LOOPS_UPSTREAM = {
    "R_DrawColumn": ("3ee968f69352aa98d7494dea3c98c221c8163c6a5d7a61fcb26485a1212db17c",
                     DRAW_COLUMN),
    "R_DrawSpan": ("0c88d10ccc7e9edf2f7b909148c6418ee4a2883ea13fda42b431741debaee95c",
                   DRAW_SPAN),
}


def _adapt_draw_loops(text: str) -> str:
    """Replace the active R_DrawColumn and R_DrawSpan with local-pointer bodies."""
    if text.count(DRAW_LOOPS_MARKER) == 2 and DRAW_COLUMN in text and DRAW_SPAN in text:
        return text
    if DRAW_LOOPS_MARKER in text:
        # Why: only the two exact maintained bodies of the previous port are upgraded,
        # so a partially edited prepared tree still fails closed.
        previous = {
            "R_DrawColumn": "d825136c4e6f083fcc9df381a2862a0abba14502d31f08262f581e338bc4fe7e",
            "R_DrawSpan": "666e64214f46cf2b2695688ece4561cdd1611991b013b3e3e137a96ad66629e1",
        }
        matches = {}
        for name, digest in previous.items():
            match = re.search(rf"^void {name} \(void\) *\n\{{ *\n.*?\n\}} *\n",
                              text, flags=re.MULTILINE | re.DOTALL)
            if (text.count(DRAW_LOOPS_MARKER) != 2 or match is None
                    or hashlib.sha256(match.group().encode()).hexdigest() != digest
                    or DRAW_LOOPS_MARKER + "\n" + match.group() not in text):
                raise RuntimeError("partial or modified r_draw.c drawer rewrite")
            matches[name] = match.group()
        for name, (_, body) in DRAW_LOOPS_UPSTREAM.items():
            text = text.replace(DRAW_LOOPS_MARKER + "\n" + matches[name], body, 1)
        return text
    for name, (digest, body) in DRAW_LOOPS_UPSTREAM.items():
        # Ref: The first match is the active definition; later ones sit in #if 0.
        match = re.search(rf"^void {name} \(void\) *\n\{{ *\n.*?\n\}} *\n",
                          text, flags=re.MULTILINE | re.DOTALL)
        if match is None or hashlib.sha256(match.group().encode()).hexdigest() != digest:
            raise RuntimeError(f"pinned r_draw.c {name} seam changed")
        text = text[:match.start()] + body + text[match.end():]
    return text


# Ref: wiki/src/nand2doom/sw/doom/spec.md "Wall-loop global contract": callbacks
# see the original published globals at every call boundary; stepping values
# are owned by r_segs.c throughout this call.
WALL_LOOP_MARKER = "/* n2d: wall-loop locals */"
WALL_LOOP_UPSTREAM_SHA256 = "3d89e7f4f777991af21da4dc842b4715b99848965916b6b6e13b2405fef0d767"
WALL_LOOP_LOCALS = ("rw_x", "rw_scale", "topfrac", "bottomfrac")


def _wall_loop_body(upstream: str) -> str:
    names = WALL_LOOP_LOCALS
    body = upstream
    for name in names:
        body = re.sub(rf"\b{name}\b", "n2d_" + name, body)
    declarations = "".join(
        f"    {'int' if name == 'rw_x' else 'fixed_t'} n2d_{name} = {name};\n"
        for name in names)
    body = body.replace("    angle_t", declarations + "    angle_t", 1)
    body = body.replace("n2d_rw_x++)", "rw_x = ++n2d_rw_x)", 1)
    end = "\tn2d_bottomfrac += bottomstep;"
    assert body.count(end) == 1
    body = body.replace(end, end + "\n\trw_scale = n2d_rw_scale;\n\ttopfrac = n2d_topfrac;\n\tbottomfrac = n2d_bottomfrac;")
    return WALL_LOOP_MARKER + "\n" + body


def _adapt_wall_loop(text: str) -> str:
    """Replace only the pinned wall loop, preserving its public globals."""
    pattern = r"^void R_RenderSegLoop \(void\)\n\{\n.*?\n\}\n"
    match = re.search(pattern, text, flags=re.MULTILINE | re.DOTALL)
    if match is None:
        raise RuntimeError("pinned r_segs.c wall-loop seam changed")
    source = match.group()
    if WALL_LOOP_MARKER in text:
        if (text.count(WALL_LOOP_MARKER) != 1
                or WALL_LOOP_MARKER + "\n" + match.group() not in text):
            raise RuntimeError("partial or modified r_segs.c wall-loop rewrite")
        names = WALL_LOOP_LOCALS
        # Why: only the exact inserted declarations and publications are reversed; the
        # final round trip below rejects a mixed, missing or modified prepared body.
        for name in names:
            declaration = (
                f"    {'int' if name == 'rw_x' else 'fixed_t'} n2d_{name} = {name};\n")
            source = source.replace(declaration, "", 1)
        source = source.replace("rw_x = ++n2d_rw_x)", "n2d_rw_x++)", 1)
        source = source.replace("\n\trw_scale = n2d_rw_scale;\n\ttopfrac = n2d_topfrac;\n\tbottomfrac = n2d_bottomfrac;", "", 1)
        for name in names:
            source = re.sub(rf"\bn2d_{name}\b", name, source)
        if (hashlib.sha256(source.encode()).hexdigest() != WALL_LOOP_UPSTREAM_SHA256
                or WALL_LOOP_MARKER + "\n" + match.group() != _wall_loop_body(source)):
            raise RuntimeError("partial or modified r_segs.c wall-loop rewrite")
        return text
    if hashlib.sha256(source.encode()).hexdigest() != WALL_LOOP_UPSTREAM_SHA256:
        raise RuntimeError("pinned r_segs.c wall-loop seam changed")
    return text[:match.start()] + _wall_loop_body(source) + text[match.end():]


# Ref: wiki/src/nand2doom/sw/doom/spec.md "Lazy visplane fill": upstream sets all
# 320 top[] bytes of every new visplane to 0xff (unused). Only top[minx..maxx]
# is ever read, so these bodies mark just the columns a plane gains when it is
# created or grows; plane selection, ranges and every value read are unchanged.
VISPLANES_MARKER = "/* n2d: lazy visplane fill */"
FIND_PLANE = VISPLANES_MARKER + """
visplane_t*
R_FindPlane
( fixed_t\theight,
  int\t\tpicnum,
  int\t\tlightlevel )
{
    visplane_t*\tcheck;

    if (picnum == skyflatnum)
    {
\theight = 0;\t\t\t// all skys map together
\tlightlevel = 0;
    }

    for (check=visplanes; check<lastvisplane; check++)
    {
\tif (height == check->height
\t    && picnum == check->picnum
\t    && lightlevel == check->lightlevel)
\t    return check;
    }

    if (lastvisplane - visplanes == MAXVISPLANES)
\tI_Error ("R_FindPlane: no more visplanes");

    lastvisplane++;

    check->height = height;
    check->picnum = picnum;
    check->lightlevel = lightlevel;
    check->minx = SCREENWIDTH;
    check->maxx = -1;
    // No columns yet: R_CheckPlane marks them as the plane gains them.
    return check;
}
"""
CHECK_PLANE = VISPLANES_MARKER + """
visplane_t*
R_CheckPlane
( visplane_t*\tpl,
  int\t\tstart,
  int\t\tstop )
{
    int\t\tintrl;
    int\t\tintrh;
    int\t\tunionl;
    int\t\tunionh;
    int\t\tx;

    if (start < pl->minx)
    {
\tintrl = pl->minx;
\tunionl = start;
    }
    else
    {
\tunionl = pl->minx;
\tintrl = start;
    }

    if (stop > pl->maxx)
    {
\tintrh = pl->maxx;
\tunionh = stop;
    }
    else
    {
\tunionh = pl->maxx;
\tintrh = stop;
    }

    for (x=intrl ; x<= intrh ; x++)
\tif (pl->top[x] != 0xff)
\t    break;

    if (x > intrh)
    {
\t// use the same one, marking only the columns it gains
\tif (pl->minx > pl->maxx)
\t    memset (pl->top+unionl, 0xff, unionh-unionl+1);
\telse
\t{
\t    if (unionl < pl->minx)
\t\tmemset (pl->top+unionl, 0xff, pl->minx-unionl);
\t    if (unionh > pl->maxx)
\t\tmemset (pl->top+pl->maxx+1, 0xff, unionh-pl->maxx);
\t}
\tpl->minx = unionl;
\tpl->maxx = unionh;
\treturn pl;
    }

    // make a new visplane
    lastvisplane->height = pl->height;
    lastvisplane->picnum = pl->picnum;
    lastvisplane->lightlevel = pl->lightlevel;

    pl = lastvisplane++;
    pl->minx = start;
    pl->maxx = stop;

    memset (pl->top+start, 0xff, stop-start+1);

    return pl;
}
"""
VISPLANES_UPSTREAM = {
    "R_FindPlane": ("8aac6498a7a53a8d2bb50a9653ca66464472a8121c4543b7fffd517e26654dfe",
                    FIND_PLANE),
    "R_CheckPlane": ("993359464a8a32370b76ecfa239693b30d7c6fbb6d19071a725d4b92a1562303",
                     CHECK_PLANE),
}


def _adapt_visplanes(text: str) -> str:
    """Replace R_FindPlane and R_CheckPlane with lazily filling bodies."""
    if text.count(VISPLANES_MARKER) == 2 and FIND_PLANE in text and CHECK_PLANE in text:
        return text
    if VISPLANES_MARKER in text:
        raise RuntimeError("partial or modified r_plane.c visplane rewrite")
    for name, (digest, body) in VISPLANES_UPSTREAM.items():
        matches = list(re.finditer(rf"^visplane_t\*\n{name}\n\(.*?\n\}}\n",
                                   text, flags=re.MULTILINE | re.DOTALL))
        if (len(matches) != 1
                or hashlib.sha256(matches[0].group().encode()).hexdigest() != digest):
            raise RuntimeError(f"pinned r_plane.c {name} seam changed")
        text = text[:matches[0].start()] + body + text[matches[0].end():]
    return text


def prepare(output: Path, repository: Path | None = None) -> dict[str, object]:
    repository = (Path(__file__).resolve().parents[4]
                  if repository is None else repository)
    output = _confined_output(repository, output)
    _validate_output_tree(output)
    downloads = output / "downloads"
    engine_archive = downloads / f"doomgeneric-{ENGINE_REVISION}.tar.gz"
    freedoom_archive = downloads / f"freedoom-{FREEDOOM_VERSION}.zip"
    _download(ENGINE_URL, engine_archive, ENGINE_SHA256)
    _download(FREEDOOM_URL, freedoom_archive, FREEDOOM_SHA256)

    source_root = output / "source"
    engine_root = source_root / f"doomgeneric-{ENGINE_REVISION}"
    if not engine_root.exists():
        source_root.mkdir(parents=True, exist_ok=True)
        with tarfile.open(engine_archive, "r:gz") as archive:
            archive.extractall(source_root, filter="data")
    _validate_output_tree(output)
    engine_dir = engine_root / "doomgeneric"

    wad = output / "freedoom1.wad"
    if not wad.exists() or _sha256(wad) != WAD_SHA256:
        with zipfile.ZipFile(freedoom_archive) as archive:
            wad.write_bytes(archive.read("freedoom-0.13.0/freedoom1.wad"))
    with zipfile.ZipFile(freedoom_archive) as archive:
        for source, destination in (
            ("COPYING.txt", "FREEDOOM-COPYING.txt"),
            ("CREDITS.txt", "FREEDOOM-CREDITS.txt"),
            ("CREDITS-MUSIC.txt", "FREEDOOM-CREDITS-MUSIC.txt"),
        ):
            (output / destination).write_bytes(
                archive.read(f"freedoom-0.13.0/{source}")
            )
    if _sha256(wad) != WAD_SHA256:
        raise RuntimeError("Freedoom WAD digest mismatch after extraction")
    if wad.stat().st_size != WAD_BYTE_LENGTH:
        raise RuntimeError("Freedoom WAD length mismatch after extraction")

    video = engine_dir / "i_video.c"
    text = video.read_text(encoding="utf-8")
    declaration = "extern void N2D_SetPalette(const uint32_t *colors);\n"
    text = text.replace(
        "extern void N2D_SetPalette(const unsigned char *colors);\n", declaration
    )
    if declaration not in text:
        text = text.replace('#include "doomgeneric.h"\n',
                            '#include "doomgeneric.h"\n' + declaration)
    needle = "#ifdef CMAP256\n\n    palette_changed = true;"
    replacement = "#ifdef CMAP256\n\n    N2D_SetPalette((const uint32_t *)colors);\n    palette_changed = true;"
    text = text.replace(
        "#ifdef CMAP256\n\n    N2D_SetPalette((const unsigned char *)colors);\n"
        "    palette_changed = true;",
        replacement,
    )
    if replacement not in text:
        if needle not in text:
            raise RuntimeError("pinned i_video.c palette seam changed")
        text = text.replace(needle, replacement)
    text = _adapt_direct_video(text)
    video.write_text(text, encoding="utf-8")

    generic = engine_dir / "doomgeneric.c"
    text = generic.read_text(encoding="utf-8")
    new = f"DG_ScreenBuffer = {BACK_FRAMEBUFFER};"
    if new not in text:
        for old in ("DG_ScreenBuffer = malloc(DOOMGENERIC_RESX * DOOMGENERIC_RESY * 4);",
                    "DG_ScreenBuffer = malloc(DOOMGENERIC_RESX * DOOMGENERIC_RESY"
                    " * sizeof(pixel_t));"):
            if text.count(old) == 1:
                text = text.replace(old, new)
                break
        else:
            raise RuntimeError("pinned doomgeneric.c allocation seam changed")
        generic.write_text(text, encoding="utf-8")

    game = engine_dir / "g_game.c"
    text = game.read_text(encoding="utf-8")
    declaration = ("extern void N2D_RecordTiccmd(int angleturn, int forwardmove, "
                   "int sidemove, unsigned int buttons);\n")
    player_declaration = ("extern void N2D_RecordPlayerState(unsigned int angle, "
                          "int x, int y);\n")
    if declaration not in text:
        seam = '#include "doomstat.h"\n'
        if seam not in text:
            raise RuntimeError("pinned g_game.c declaration seam changed")
        text = text.replace(seam, seam + declaration, 1)
    if player_declaration not in text:
        text = text.replace(declaration, declaration + player_declaration, 1)
    call = ("    N2D_RecordTiccmd(cmd->angleturn, cmd->forwardmove, "
            "cmd->sidemove, cmd->buttons);\n")
    if call not in text:
        text, count = re.subn(
            r"(        carry = desired_angleturn - cmd->angleturn;\n    \}\n)\} *\n",
            r"\1" + call + "}\n", text, count=1,
        )
        if count != 1:
            raise RuntimeError("pinned g_game.c ticcmd seam changed")
    player_call = ("\tif (players[consoleplayer].mo != NULL)\n"
                   "\t    N2D_RecordPlayerState(players[consoleplayer].mo->angle,\n"
                   "\t                          players[consoleplayer].mo->x,\n"
                   "\t                          players[consoleplayer].mo->y);\n")
    if player_call not in text:
        seam = "\tP_Ticker (); \n\tST_Ticker ();"
        if seam not in text:
            raise RuntimeError("pinned g_game.c player-state seam changed")
        text = text.replace(seam, "\tP_Ticker (); \n" + player_call + "\tST_Ticker ();", 1)
    game.write_text(text, encoding="utf-8")

    display = engine_dir / "d_main.c"
    text = display.read_text(encoding="utf-8")
    text = _adapt_scene(text)
    text = _adapt_framebuffer_acquire(text)
    display.write_text(text, encoding="utf-8")

    misc = engine_dir / "m_misc.c"
    text = misc.read_text(encoding="utf-8")
    marker = "boolean M_FileExists(char *filename)\n{\n"
    injected = marker + '    if (!strcmp(filename, "freedoom1.wad")) return true;\n'
    if injected not in text:
        if marker not in text:
            raise RuntimeError("pinned m_misc.c file seam changed")
        misc.write_text(text.replace(marker, injected), encoding="utf-8")

    system = engine_dir / "i_system.c"
    text = system.read_text(encoding="utf-8")
    text = re.sub(
        r"static byte \*AutoAllocMemory\(int \*size, int default_ram, int min_ram\)\n"
        r"\{.*?\n\}\n\n",
        "", text, count=1, flags=re.DOTALL,
    )
    zone_replacement = """byte *I_ZoneBase (int *size)
{
    extern byte __zone_start[];
    extern byte __zone_end[];
    *size = (int)(__zone_end - __zone_start);
    return __zone_start;
}

void I_PrintBanner"""
    if "extern byte __zone_start[];" not in text:
        text, count = re.subn(
            r"byte \*I_ZoneBase \(int \*size\)\n\{.*?\n\}\n\nvoid I_PrintBanner",
            zone_replacement, text, count=1, flags=re.DOTALL,
        )
        if count != 1:
            raise RuntimeError("pinned i_system.c zone seam changed")
    system.write_text(text, encoding="utf-8")

    checksum = engine_dir / "w_checksum.c"
    text = checksum.read_text(encoding="utf-8")
    # Why: network compatibility is deliberately absent on this single-player
    # platform; dropping the directory-walking helpers with their caller keeps the
    # warning-clean build free of dead SHA-1 bookkeeping.
    text, count = re.subn(
        r"static wad_file_t \*\*open_wadfiles = NULL;.*?\n(?=void W_Checksum)",
        "", text, count=1, flags=re.DOTALL,
    )
    if count != 1 and "static void ChecksumAddLump" in text:
        raise RuntimeError("pinned w_checksum.c helper seam changed")
    checksum_replacement = """void W_Checksum(sha1_digest_t digest)
{
    memset(digest, 0, sizeof(sha1_digest_t));
}
"""
    if "memset(digest, 0, sizeof(sha1_digest_t));" not in text:
        text, count = re.subn(
            r"void W_Checksum\(sha1_digest_t digest\)\n\{.*?\n\}\n",
            checksum_replacement, text, count=1, flags=re.DOTALL,
        )
        if count != 1:
            raise RuntimeError("pinned w_checksum.c single-player seam changed")
    checksum.write_text(text, encoding="utf-8")

    draw = engine_dir / "r_draw.c"
    draw.write_text(_adapt_draw_loops(draw.read_text(encoding="utf-8")), encoding="utf-8")
    segs = engine_dir / "r_segs.c"
    segs.write_text(_adapt_wall_loop(segs.read_text(encoding="utf-8")), encoding="utf-8")
    plane = engine_dir / "r_plane.c"
    plane.write_text(_adapt_visplanes(plane.read_text(encoding="utf-8")), encoding="utf-8")

    manifest = {
        "engine": {
            "name": "doomgeneric", "revision": ENGINE_REVISION,
            "archive_sha256": ENGINE_SHA256, "license": ENGINE_LICENSE,
            "url": ENGINE_URL,
        },
        "game_data": {
            "name": "Freedoom Phase 1", "version": FREEDOOM_VERSION,
            "archive_sha256": FREEDOOM_SHA256, "wad_sha256": WAD_SHA256,
            "license": "BSD-3-Clause", "url": FREEDOOM_URL,
            "byte_length": wad.stat().st_size, "address": WAD_ADDRESS,
        },
    }
    (output / "inputs.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def _run(command: list[str], cwd: Path | None = None) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def _version(tool: str) -> str:
    result = subprocess.run(
        [tool, "--version"], check=True, capture_output=True, text=True
    )
    lines = (result.stdout or result.stderr).splitlines()
    if not lines:
        raise RuntimeError(f"tool did not report a version: {tool}")
    return lines[0]


TOOLCHAIN_LIBC = "usr/lib/picolibc/riscv64-unknown-elf/lib/rv32im/ilp32/libc.a"


def _picolibc(output: Path, override: Path | None) -> Path:
    if override is not None:
        return override
    package = output / "downloads" / f"picolibc-{PICOLIBC_VERSION}.deb"
    _download(PICOLIBC_URL, package, PICOLIBC_SHA256)
    root = output / "toolchain"
    if not (root / TOOLCHAIN_LIBC).is_file():
        # Why: a partial tree is re-extracted from scratch; removing it only unlinks
        # these names, so files hard-linked from another output are never overwritten
        # in place.
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True)
        _extract_deb_data(package, root)
    return root / "usr"


def _extract_deb_data(package: Path, root: Path) -> None:
    """Extract a Debian package's data tarball without host packaging tools.

    A .deb is a common-format ar archive whose data member is a tarball. The
    pinned package digest is verified before this runs; extraction uses the
    tarfile "data" filter so no member can escape ``root``.
    """
    archive = package.read_bytes()
    if not archive.startswith(b"!<arch>\n"):
        raise RuntimeError(f"not an ar archive: {package}")
    offset = 8
    while offset + 60 <= len(archive):
        header = archive[offset:offset + 60]
        if header[58:60] != b"`\n":
            raise RuntimeError(f"malformed ar member header: {package}")
        name = header[:16].decode("ascii").strip().rstrip("/")
        size = int(header[48:58].decode("ascii").strip())
        start = offset + 60
        if start + size > len(archive):
            raise RuntimeError(f"truncated ar member {name}: {package}")
        if name.startswith("data.tar"):
            payload = archive[start:start + size]
            if name.endswith(".zst"):
                try:
                    from compression import zstd
                except ModuleNotFoundError as error:
                    raise RuntimeError(
                        f"{name} needs Python 3.14 compression.zstd; run with "
                        "Python 3.14 or pass --picolibc-prefix: "
                        f"{package}") from error
                payload = zstd.decompress(payload)
            elif name not in ("data.tar", "data.tar.gz", "data.tar.xz"):
                raise RuntimeError(f"unsupported package data member {name}")
            with tarfile.open(fileobj=io.BytesIO(payload)) as data:
                data.extractall(root, filter="data")
            return
        offset = start + size + (size & 1)
    raise RuntimeError(f"package has no data member: {package}")


def _verify_picolibc(prefix: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative, expected in PICOLIBC_ARTIFACT_SHA256.items():
        artifact = prefix / relative
        if not artifact.is_file():
            raise RuntimeError(f"pinned Picolibc artifact missing: {artifact}")
        observed = _sha256(artifact)
        if observed != expected:
            raise RuntimeError(
                f"pinned Picolibc artifact digest mismatch: {artifact}"
            )
        hashes[relative] = observed
    include_root = prefix / PICOLIBC_INCLUDE_ROOT
    include_digest = _tree_sha256(include_root)
    if include_digest != PICOLIBC_INCLUDE_TREE_SHA256:
        raise RuntimeError(
            f"pinned Picolibc include tree digest mismatch: {include_root}"
        )
    hashes[f"{PICOLIBC_INCLUDE_ROOT}/"] = include_digest
    return hashes


def _tree_sha256(root: Path) -> str:
    if not root.is_dir() or root.is_symlink():
        raise RuntimeError(f"pinned include tree missing: {root}")
    digest = hashlib.sha256()
    files = sorted(root.rglob("*"), key=lambda path: path.relative_to(root).as_posix())
    for path in files:
        if path.is_symlink():
            raise RuntimeError(f"pinned include tree contains symbolic link: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(_sha256(path)))
    return digest.hexdigest()


ENGINE_OPT_LEVELS = ("Os", "O2", "O3")
PLATFORM_SOURCES = ("w_file_stdc.c", "crt0.S", "fast_memory.c", "platform.c",
                    "syscalls.c", "clock.c", "capture.c")


def variant_compiler_options(variant: str, engine_opt: str | None = None,
                             engine_lto: bool | None = None) -> dict:
    """The variant's default engine profile, with explicit overrides applied."""
    if variant not in VARIANTS:
        raise ValueError(f"unknown Doom firmware variant: {variant}")
    return compiler_options(
        VARIANTS[variant]["engine_opt"] if engine_opt is None else engine_opt,
        VARIANTS[variant]["engine_lto"] if engine_lto is None else engine_lto)


def compiler_options(engine_opt: str = "Os", engine_lto: bool = False) -> dict:
    """An engine-only profile; local ABI objects retain -Os and stay outside LTO."""
    if engine_opt not in ENGINE_OPT_LEVELS or type(engine_lto) is not bool:
        raise ValueError("invalid engine compiler options")
    engine = [f"-{engine_opt}"]
    platform = ["-Os"]
    link = []
    if engine_lto:
        engine.append("-flto=1")
        platform.append("-fno-lto")
        # Why: one LTO partition and one worker keep link-time optimization deterministic.
        link = [f"-{engine_opt}", "-flto=1", "-flto-partition=one"]
    return {"engine_opt": engine_opt, "engine_lto": engine_lto,
            "engine_flags": engine, "platform_flags": platform, "link_flags": link}


def _compiler_flags(gcc: str, engine: Path, include: Path, output: Path,
                    repository: Path, upstream: bool, *, engine_opt: str = "Os",
                    engine_lto: bool = False) -> list[str]:
    options = compiler_options(engine_opt, engine_lto)
    optimization = options["engine_flags" if upstream else "platform_flags"]
    flags = [
        gcc, "-march=rv32im", "-mabi=ilp32", *optimization, "-ffreestanding",
        "-fno-builtin", "-fno-pic", "-fno-pie", "-mno-relax",
        "-msmall-data-limit=0",
        # Why: upstream diagnostics embed __FILE__, so paths are mapped to keep the
        # firmware byte-identical across issue worktrees and disposable output locations.
        f"-ffile-prefix-map={repository.resolve()}=/nand2anything",
        f"-ffile-prefix-map={output.resolve()}=/n2d-doom-build",
        f"-fmacro-prefix-map={repository.resolve()}=/nand2anything",
        f"-fmacro-prefix-map={output.resolve()}=/n2d-doom-build",
        "-ffunction-sections", "-fdata-sections", "-Wall", "-Wextra", "-Werror",
        "-DNORMALUNIX", "-DLINUX", "-D_DEFAULT_SOURCE", "-DCMAP256",
        "-DDOOMGENERIC_RESX=320", "-DDOOMGENERIC_RESY=200",
        "-I", str(engine), "-isystem", str(include),
    ]
    if upstream:
        flags.extend(UPSTREAM_WARNING_EXCEPTIONS)
    return flags


def build(output: Path, repository: Path,
          picolibc_override: Path | None,
          variant: str = "production", *, engine_opt: str | None = None,
          engine_lto: bool | None = None) -> dict[str, object]:
    options = variant_compiler_options(variant, engine_opt, engine_lto)
    engine_opt, engine_lto = options["engine_opt"], options["engine_lto"]
    defines = list(VARIANTS[variant]["defines"])
    output = _confined_output(repository, output)
    _validate_output_tree(output)
    _seed_inputs(output, repository)
    inputs = prepare(output, repository)
    _validate_output_tree(output)
    picolibc_prefix = _picolibc(output, picolibc_override)
    picolibc_hashes = _verify_picolibc(picolibc_prefix)
    picolibc_notice = output / "PICOLIBC-COPYRIGHT.txt"
    picolibc_notice.write_bytes(
        (picolibc_prefix / PICOLIBC_COPYRIGHT_PATH).read_bytes()
    )
    engine = output / "source" / f"doomgeneric-{ENGINE_REVISION}" / "doomgeneric"
    include = picolibc_prefix / "lib/picolibc/riscv64-unknown-elf/include"
    library = picolibc_prefix / "lib/picolibc/riscv64-unknown-elf/lib/rv32im/ilp32"
    if not (include / "stdio.h").is_file() or not (library / "libc.a").is_file():
        raise RuntimeError(
            "picolibc RV32IM/ILP32 not found; install picolibc-riscv64-unknown-elf "
            "or pass --picolibc-prefix"
        )
    build_dir = output / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    gcc = shutil.which("riscv64-unknown-elf-gcc")
    objcopy = shutil.which("riscv64-unknown-elf-objcopy")
    if gcc is None or objcopy is None:
        raise RuntimeError("riscv64-unknown-elf GCC/binutils are required")
    common = _compiler_flags(
        gcc, engine, include, output, repository, upstream=False,
        engine_opt=engine_opt, engine_lto=engine_lto
    ) + defines
    upstream = _compiler_flags(
        gcc, engine, include, output, repository, upstream=True,
        engine_opt=engine_opt, engine_lto=engine_lto
    ) + defines
    # Why: input identity is computed before linking, so there is no self-hash
    # cycle; the final manifest binds it to the full ELF and binary hashes.
    source_hashes = {
        str(path.relative_to(repository)): _sha256(path)
        for path in sorted((repository / "src/nand2doom/sw/doom").iterdir())
        if path.is_file()
    }
    source_hashes["src/nand2doom/scripts/sw_build/n2d_doom.py"] = _sha256(
        repository / "src/nand2doom/scripts/sw_build/n2d_doom.py")
    identity_inputs = {
        "sources": source_hashes, "engine_tree": _tree_sha256(engine),
        "libc": picolibc_hashes, "compiler": _version(gcc),
        "engine_archive": ENGINE_SHA256, "wad": WAD_SHA256,
        "compiler_options": options,
    }
    if defines:
        # Why: production has no timing defines, so its identity is unchanged; other
        # variants get distinct identities.
        identity_inputs["variant_defines"] = defines
    identity = hashlib.sha256(json.dumps(
        identity_inputs, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()
    identity_source = build_dir / "capture_identity.c"
    identity_source.write_text(
        '#include <stdint.h>\nconst uint32_t n2d_capture_identity[8] = {'
        + ','.join(f'0x{int.from_bytes(bytes.fromhex(identity)[i:i+4], "little"):08x}u'
                   for i in range(0, 32, 4)) + '};\n')
    objects: list[Path] = []
    identity_object = build_dir / "capture_identity.o"
    _run(common + ["-c", str(identity_source), "-o", str(identity_object)])
    objects.append(identity_object)
    for name in ENGINE_OBJECTS:
        source = engine / f"{name}.c"
        obj = build_dir / f"{name}.o"
        _run(upstream + ["-c", str(source), "-o", str(obj)])
        objects.append(obj)
    for source in PLATFORM_SOURCES:
        path = repository / "src/nand2doom/sw/doom" / source
        obj = build_dir / (Path(source).stem + ".o")
        _run(common + ["-c", str(path), "-o", str(obj)])
        objects.append(obj)

    elf = output / "n2d_doom.elf"
    link_map = output / "n2d_doom.map"
    linker = repository / "src/nand2doom/sw/doom/n2d_doom.ld"
    _run([
        gcc, "-march=rv32im", "-mabi=ilp32", *options["link_flags"], "-nostdlib", "-nostartfiles",
        "-fno-pic", "-fno-pie", "-mno-relax", f"-Wl,-T,{linker}",
        "-Wl,--gc-sections", "-Wl,--no-warn-rwx-segments",
        f"-Wl,-Map,{link_map}", *map(str, objects),
        f"-L{library}", "-Wl,--start-group", "-lc", "-lm", "-ldummyhost", "-lgcc",
        "-Wl,--end-group", "-o", str(elf),
    ])
    image = output / "n2d_doom.bin"
    _run([objcopy, "-O", "binary", str(elf), str(image)])
    symbols = subprocess.run(
        [str(Path(objcopy).with_name("riscv64-unknown-elf-nm")),
         "-S", "--defined-only", str(elf)],
        check=True, capture_output=True, text=True, timeout=30).stdout
    capture_symbols = {}
    for line in symbols.splitlines():
        fields = line.split()
        if len(fields) == 4 and fields[3] in ("n2d_capture", "n2d_capture_identity"):
            address, size, kind, name = fields
            if name in capture_symbols:
                raise RuntimeError(f"duplicate capture symbol: {name}")
            capture_symbols[name] = (int(address, 16), int(size, 16), kind.lower())
    if set(capture_symbols) != {"n2d_capture", "n2d_capture_identity"}:
        raise RuntimeError("missing capture symbols")
    mailbox, size, kind = capture_symbols["n2d_capture"]
    identity_address, identity_size, identity_kind = capture_symbols["n2d_capture_identity"]
    if size != 1120 or kind != "b" or mailbox % 32 or not 0 <= mailbox < 0x700000 - size:
        raise RuntimeError("invalid capture BSS symbol")
    if identity_size != 32 or identity_kind != "r" or identity_address % 4:
        raise RuntimeError("invalid capture identity symbol")
    if image.read_bytes()[identity_address:identity_address + 32] != bytes.fromhex(identity):
        raise RuntimeError("capture identity differs from linked binary")
    wad = output / "freedoom1.wad"
    manifest = {
        **inputs, "schema_version": 1, "architecture": "rv32im",
        "compiler_options": options,
        "variant": {
            "name": variant, "defines": defines,
            "time_source": VARIANTS[variant]["time_source"],
        },
        "capture": {
            "abi": 1, "words": 280, "address": mailbox,
            "identity_address": identity_address, "identity": identity,
            "identity_inputs": identity_inputs,
            "elf": {"path": elf.name, "sha256": _sha256(elf)},
        },
        "abi": "ilp32", "endianness": "little", "entry": 0,
        "memory_map_revision": MEMORY_MAP_REVISION,
        "toolchain": {
            "compiler": _version(gcc),
            "objcopy": _version(objcopy),
        },
        "libc": {
            "name": "picolibc-riscv64-unknown-elf",
            "version": PICOLIBC_VERSION,
            "package_sha256": PICOLIBC_SHA256,
            "url": PICOLIBC_URL,
            "source": ("verified_override" if picolibc_override is not None
                       else "pinned_package"),
            "licenses": "BSD-family",
            "license_notice": {
                "path": picolibc_notice.name,
                "sha256": _sha256(picolibc_notice),
            },
            "artifact_sha256": picolibc_hashes,
        },
        "segments": [
            {"address": 0, "path": image.name, "byte_length": image.stat().st_size,
             "sha256": _sha256(image)},
            {"address": WAD_ADDRESS, "path": wad.name, "byte_length": wad.stat().st_size,
             "sha256": _sha256(wad)},
        ],
        "markers": {
            "boot": "0xd00d3701",
            "frame": "0xd00d3702",
            "scene": "0xd00d3703",
        },
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def selected_variants(command: str, variant: str | None,
                      output: str | None) -> list[str]:
    """Variants a command builds. A single --output without --variant keeps
    the historical meaning: the production image in that directory."""
    if command == "prepare" or output is not None:
        # Why: inputs are variant-independent, so prepare or an explicit --output works
        # on one directory.
        if variant == "all":
            if output is not None:
                raise ValueError("--output requires a single --variant")
            return ["production"]
        return [variant or "production"]
    if variant is None:
        return ["production", "deterministic"]  # Why: keeps the ordinary default build; render verification is opt-in.
    return list(VARIANTS) if variant == "all" else [variant]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "build"))
    parser.add_argument("--variant", choices=(*VARIANTS, "all"), default=None,
                        help="firmware variant: default builds production and deterministic; "
                             "all includes render-verification, or production when "
                             "--output is given; prepare uses one directory")
    parser.add_argument("--output",
                        help="output directory for one variant (default: "
                             "workdir/software/n2d_doom or n2d_doom_deterministic)")
    parser.add_argument("--picolibc-prefix")
    parser.add_argument("--engine-opt", choices=ENGINE_OPT_LEVELS, default=None,
                        help="engine-only optimization level (default: the variant's, "
                             "O2 for production/deterministic, Os for render-verification)")
    parser.add_argument("--engine-lto", action=argparse.BooleanOptionalAction, default=None,
                        help="LTO only upstream engine objects; platform/capture remain "
                             "native (default: the variant's, on except render-verification)")
    parser.add_argument("--plan", action="store_true",
                        help="show selected recipe without downloads, compiler discovery or writes")
    args = parser.parse_args(argv)
    repository = Path(__file__).resolve().parents[4]
    if args.command == "prepare" and (args.engine_opt is not None
                                      or args.engine_lto is not None):
        parser.error("compiler options apply only to build")
    try:
        variants = selected_variants(args.command, args.variant, args.output)
    except ValueError as error:
        parser.error(str(error))
    manifests = {}
    for variant in variants:
        requested = Path(args.output or VARIANTS[variant]["output"])
        try:
            output = _confined_output(repository, requested)
        except ValueError as error:
            parser.error(str(error))
        if args.plan:
            manifests[variant] = {"command": args.command, "variant": variant,
                                  "output": str(output.relative_to(repository)),
                                  "defines": list(VARIANTS[variant]["defines"]),
                                  "compiler_options": variant_compiler_options(
                                      variant, args.engine_opt, args.engine_lto),
                                  "engine_objects": list(ENGINE_OBJECTS),
                                  "platform_sources": list(PLATFORM_SOURCES),
                                  "identity_object": "capture_identity.o"}
        elif args.command == "prepare":
            manifests[variant] = prepare(output, repository)
        else:
            prefix = (Path(args.picolibc_prefix).resolve()
                      if args.picolibc_prefix else None)
            manifests[variant] = build(output, repository, prefix, variant,
                                       engine_opt=args.engine_opt, engine_lto=args.engine_lto)
    result = manifests[variants[0]] if len(variants) == 1 else manifests
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
