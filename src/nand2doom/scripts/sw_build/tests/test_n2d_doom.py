from pathlib import Path
import contextlib
import errno
import functools
import io
import json
import lzma
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO_ROOT))
from src.nand2doom.scripts.sw_build import n2d_doom  # noqa: E402
from tools.bazel import scratch  # noqa: E402


def _tarball(entries: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return stream.getvalue()


def _upstream_source(test: unittest.TestCase, name: str) -> str:
    """A pinned upstream engine file, read from the vendored engine archive."""
    archive = scratch.pinned(
        n2d_doom.VENDORED_DOWNLOADS / f"doomgeneric-{n2d_doom.ENGINE_REVISION}.tar.gz",
        n2d_doom.ENGINE_SHA256,
        lambda path: n2d_doom._download(n2d_doom.ENGINE_URL, path, n2d_doom.ENGINE_SHA256,
                                        cache=path.parent))
    with tarfile.open(archive, "r:gz") as engine:
        member = f"doomgeneric-{n2d_doom.ENGINE_REVISION}/doomgeneric/{name}"
        return engine.extractfile(member).read().decode("utf-8")


def _upstream_r_draw(test: unittest.TestCase) -> str:
    return _upstream_source(test, "r_draw.c")


# Why: the rewritten drawers must stay byte-identical to upstream, so the host
# harness runs random columns and spans through both into two screens.
DRAW_HARNESS_PRELUDE = """#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef unsigned char byte;
typedef int fixed_t;
typedef byte lighttable_t;
#define RANGECHECK
#define FRACBITS 16
#define SCREENWIDTH 320
#define SCREENHEIGHT 200
static void I_Error(const char *format, ...) { (void)format; }
static byte *ylookup[SCREENHEIGHT];
static int columnofs[SCREENWIDTH];
static int centery;
static lighttable_t *dc_colormap, *ds_colormap;
static int dc_x, dc_yl, dc_yh, ds_y, ds_x1, ds_x2;
static fixed_t dc_iscale, dc_texturemid, ds_xfrac, ds_yfrac, ds_xstep, ds_ystep;
static byte *dc_source, *ds_source;
"""
DRAW_HARNESS_MAIN = """
static uint32_t state = 163u;
static uint32_t next(void) {
    state ^= state << 13; state ^= state >> 17; state ^= state << 5; return state;
}
static byte screens[2][SCREENWIDTH * SCREENHEIGHT];
static void target(int which) {
    for (int y = 0; y < SCREENHEIGHT; y++) ylookup[y] = screens[which] + y * SCREENWIDTH;
}
int main(void) {
    static byte colormap[256], texture[256], flat[4096];
    int columns = 0, spans = 0;
    for (int x = 0; x < SCREENWIDTH; x++) columnofs[x] = x;
    for (int i = 0; i < 256; i++) colormap[i] = (byte)next();
    for (int i = 0; i < 256; i++) texture[i] = (byte)next();
    for (int i = 0; i < 4096; i++) flat[i] = (byte)next();
    for (int i = 0; i < (int)sizeof screens[0]; i++) screens[0][i] = screens[1][i] = (byte)next();
    dc_colormap = ds_colormap = colormap;
    for (int round = 0; round < 20000; round++) {
        dc_x = next() % SCREENWIDTH;
        dc_yl = next() % SCREENHEIGHT;
        dc_yh = dc_yl - 1 + (int)(next() % (SCREENHEIGHT - dc_yl + 1));
        dc_iscale = (fixed_t)next() >> (next() % 16);
        dc_texturemid = (fixed_t)next();
        centery = next() % SCREENHEIGHT;
        dc_source = texture + next() % 128;
        target(0); upstream_R_DrawColumn();
        target(1); R_DrawColumn();
        if (memcmp(screens[0], screens[1], sizeof screens[0]) != 0) {
            printf("column %d differs\\n", round); return 1;
        }
        columns++;
        ds_y = next() % SCREENHEIGHT;
        ds_x1 = next() % SCREENWIDTH;
        ds_x2 = ds_x1 + (int)(next() % (SCREENWIDTH - ds_x1));
        ds_xfrac = (fixed_t)next(); ds_yfrac = (fixed_t)next();
        ds_xstep = (fixed_t)next() >> (next() % 16);
        ds_ystep = (fixed_t)next() >> (next() % 16);
        ds_source = flat;
        target(0); upstream_R_DrawSpan();
        target(1); R_DrawSpan();
        if (memcmp(screens[0], screens[1], sizeof screens[0]) != 0) {
            printf("span %d differs\\n", round); return 1;
        }
        spans++;
    }
    printf("columns=%d spans=%d identical\\n", columns, spans);
    return 0;
}
"""

# Why: an independent scalar contract for modulo32 coordinates. Unlike the
# upstream do/while span (whose invalid-count I_Error never returns), the port
# writes no pixels for a nonpositive count, so that boundary is checked here,
# apart from the upstream equivalence test, which uses only valid nonempty inputs.
DRAW_SCALAR_REFERENCE = """
static void maintained_R_DrawColumn(void) {
    int count = dc_yh - dc_yl + 1;
    for (int i = 0; i < count; i++) {
        uint32_t frac = (uint32_t)dc_texturemid +
            ((uint32_t)(dc_yl + i) - (uint32_t)centery) * (uint32_t)dc_iscale;
        ylookup[dc_yl + i][columnofs[dc_x]] = dc_colormap[dc_source[(frac >> 16) & 127]];
    }
}
static void maintained_R_DrawSpan(void) {
    int count = ds_x2 - ds_x1 + 1;
    uint32_t initial = (((uint32_t)ds_xfrac << 10) & 0xffff0000u) |
                       (((uint32_t)ds_yfrac >> 6) & 0xffffu);
    uint32_t step = (((uint32_t)ds_xstep << 10) & 0xffff0000u) |
                    (((uint32_t)ds_ystep >> 6) & 0xffffu);
    for (int i = 0; i < count; i++) {
        uint32_t position = initial + (uint32_t)i * step;
        unsigned spot = ((position >> 4) & 0xfc0u) | (position >> 26);
        ylookup[ds_y][columnofs[ds_x1] + i] = ds_colormap[ds_source[spot]];
    }
}
"""
DRAW_BOUNDARY_MAIN = """
typedef struct { byte before[16], pixels[SCREENWIDTH * SCREENHEIGHT], after[16]; } guarded_screen;
static guarded_screen screens[2];
static const uint32_t values[] = {0, 1, 0xffff, 0x10000, 0x7fffff,
                                  0x800000, 0x7fffffff, 0x80000000, 0xffffffff};
static void target(int which) {
    for (int y = 0; y < SCREENHEIGHT; y++) ylookup[y] = screens[which].pixels + y * SCREENWIDTH;
}
int main(void) {
    byte map[256], texture[256], flat[4096];
    for (int i = 0; i < 256; i++) { map[i] = (byte)(i ^ 0xa5); texture[i] = (byte)(i * 17 + 3); }
    for (int i = 0; i < 4096; i++) flat[i] = (byte)(i * 13 + (i >> 8));
    dc_source = texture; ds_source = flat; dc_colormap = ds_colormap = map;
    for (int x = 0; x < SCREENWIDTH; x++) columnofs[x] = x;
    centery = 100;
    int cases = 0;
    for (unsigned f = 0; f < sizeof values / sizeof values[0]; f++)
    for (unsigned step = 0; step < sizeof values / sizeof values[0]; step++)
    for (int count = -3; count <= 17; count++)
    for (int alignment = 0; alignment < 4; alignment++) {
        memset(screens, 0x69, sizeof screens);
        dc_x = SCREENWIDTH - 4 + alignment; dc_yl = count > 0 ? SCREENHEIGHT - count : 0;
        dc_yh = dc_yl + count - 1;
        dc_texturemid = (fixed_t)values[f]; dc_iscale = (fixed_t)values[step];
        target(0); maintained_R_DrawColumn(); target(1); R_DrawColumn();
        if (memcmp(&screens[0], &screens[1], sizeof screens[0])) {
            printf("column boundary differs f=%u step=%u count=%d alignment=%d\\n", f, step, count, alignment);
            return 1;
        }
        memset(screens, 0x69, sizeof screens);
        ds_y = SCREENHEIGHT - 1;
        ds_x1 = count > 0 ? ((SCREENWIDTH - count) & ~3) + alignment : SCREENWIDTH - 1; ds_x2 = ds_x1 + count - 1;
        ds_xfrac = (fixed_t)values[f]; ds_yfrac = (fixed_t)~values[f];
        ds_xstep = (fixed_t)values[step]; ds_ystep = (fixed_t)~values[step];
        target(0); maintained_R_DrawSpan(); target(1); R_DrawSpan();
        if (memcmp(&screens[0], &screens[1], sizeof screens[0])) {
            printf("span boundary differs f=%u step=%u count=%d alignment=%d\\n", f, step, count, alignment);
            return 1;
        }
        cases++;
    }
    printf("boundary cases=%d identical\\n", cases);
    return 0;
}
"""

# Why: lazy fill must match upstream wherever a reader can see (plane
# selection, ranges and top[minx..maxx]), so the harness drives one random
# visplane workload (R_FindPlane, R_CheckPlane, seg-loop top[] writes) through
# both; stale bytes outside the range are left from earlier frames on purpose.
VISPLANE_HARNESS_PRELUDE = """#include <stdio.h>
#include <string.h>
typedef unsigned char byte;
typedef int fixed_t;
#define SCREENWIDTH 320
#define MAXVISPLANES 128
typedef struct {
    fixed_t height; int picnum; int lightlevel; int minx; int maxx;
    byte pad1; byte top[SCREENWIDTH]; byte pad2; byte pad3;
    byte bottom[SCREENWIDTH]; byte pad4;
} visplane_t;
static int skyflatnum = 7;
static int errors;
static void I_Error(const char *format, ...) { (void)format; errors++; }
static visplane_t visplanes[MAXVISPLANES], *lastvisplane;
static visplane_t visplanes_lazy[MAXVISPLANES], *lastvisplane_lazy;
"""
VISPLANE_HARNESS_MAIN = """
static unsigned int state = 164u;
static unsigned int next(void) {
    state ^= state << 13; state ^= state >> 17; state ^= state << 5; return state;
}
static int same(void) {
    if (lastvisplane - visplanes != lastvisplane_lazy - visplanes_lazy) return 0;
    for (visplane_t *a = visplanes, *b = visplanes_lazy; a < lastvisplane; a++, b++) {
        if (a->height != b->height || a->picnum != b->picnum
            || a->lightlevel != b->lightlevel || a->minx != b->minx || a->maxx != b->maxx)
            return 0;
        if (a->minx <= a->maxx
            && memcmp(a->top + a->minx, b->top + b->minx, a->maxx - a->minx + 1) != 0)
            return 0;
    }
    return 1;
}
int main(void) {
    int checks = 0, grows = 0, splits = 0;
    /* Stale, different garbage in each copy before the first frame. */
    for (size_t i = 0; i < sizeof visplanes; i++) {
        ((byte *)visplanes)[i] = (byte)next();
        ((byte *)visplanes_lazy)[i] = (byte)next();
    }
    for (int frame = 0; frame < 400; frame++) {
        lastvisplane = visplanes;
        lastvisplane_lazy = visplanes_lazy;
        for (int seg = 0; seg < 60 && lastvisplane - visplanes < MAXVISPLANES - 2; seg++) {
            int height = (int)(next() % 4) * 8, pic = (int)(next() % 9), light = (int)(next() % 3);
            visplane_t *a = upstream_R_FindPlane(height, pic, light);
            visplane_t *b = R_FindPlane(height, pic, light);
            if (a - visplanes != b - visplanes_lazy || !same()) {
                printf("find %d/%d differs\\n", frame, seg); return 1;
            }
            int start = (int)(next() % SCREENWIDTH);
            int stop = start + (int)(next() % (next() % 4 ? 24u : SCREENWIDTH - start));
            if (stop >= SCREENWIDTH) stop = SCREENWIDTH - 1;
            int old_minx = a->minx, old_maxx = a->maxx;
            visplane_t *c = upstream_R_CheckPlane(a, start, stop);
            visplane_t *d = R_CheckPlane(b, start, stop);
            if (c - visplanes != d - visplanes_lazy || !same()) {
                printf("check %d/%d differs\\n", frame, seg); return 1;
            }
            checks++;
            if (c != a) splits++;
            else if (old_minx <= old_maxx && (c->minx < old_minx || c->maxx > old_maxx)) grows++;
            /* The seg loop marks some columns of [start, stop]. */
            for (int x = start; x <= stop; x++)
                if (next() % 3) c->top[x] = d->top[x] = (byte)(next() % 200);
            if (!same()) { printf("mark %d/%d differs\\n", frame, seg); return 1; }
        }
    }
    if (errors) { printf("I_Error called\\n"); return 1; }
    printf("checks=%d grows=%d splits=%d identical\\n", checks, grows, splits);
    return 0;
}
"""


class N2dDoomToolTests(unittest.TestCase):
    @functools.cached_property
    def root(self) -> Path:
        return scratch.checkout(self)

    def test_engine_experiments_leave_platform_and_capture_outside_lto(self) -> None:
        args = ("gcc", Path("engine"), Path("include"), Path("output"), REPO_ROOT)
        for level in ("Os", "O2", "O3"):
            for lto in (False, True):
                with self.subTest(level=level, lto=lto):
                    engine = n2d_doom._compiler_flags(*args, upstream=True,
                                                     engine_opt=level, engine_lto=lto)
                    local = n2d_doom._compiler_flags(*args, upstream=False,
                                                    engine_opt=level, engine_lto=lto)
                    self.assertIn("-" + level, engine)
                    self.assertEqual([flag for flag in local if flag.startswith("-O")], ["-Os"])
                    self.assertEqual("-flto=1" in engine, lto)
                    self.assertNotIn("-flto=1", local)
                    self.assertEqual("-fno-lto" in local, lto)
                    self.assertIn("-Werror", local)
                    self.assertFalse(any(flag.startswith("-Wno-") for flag in local))
                    link = n2d_doom.compiler_options(level, lto)["link_flags"]
                    self.assertEqual(link, ["-" + level, "-flto=1", "-flto-partition=one"] if lto else [])
        defaults = n2d_doom._compiler_flags(*args, upstream=False)
        self.assertEqual(defaults[:5], ["gcc", "-march=rv32im", "-mabi=ilp32", "-Os", "-ffreestanding"])
        for level, lto in (("O0", False), ("O2 -ffast-math", False), ("O2", 1)):
            with self.assertRaisesRegex(ValueError, "compiler options"):
                n2d_doom.compiler_options(level, lto)

    def test_plan_never_downloads_discovers_compilers_or_writes(self) -> None:
        output = self.root / "workdir/not-created"
        stdout = io.StringIO()
        with mock.patch.object(n2d_doom, "prepare") as prepare, \
                mock.patch.object(n2d_doom, "build") as build, \
                mock.patch.object(n2d_doom.shutil, "which") as which, \
                mock.patch.object(n2d_doom, "_download") as download, \
                mock.patch.object(n2d_doom, "_version") as version, \
                contextlib.redirect_stdout(stdout):
            self.assertEqual(n2d_doom.main(["build", "--plan", "--variant", "deterministic",
                                            "--output", str(output), "--engine-opt", "O2",
                                            "--engine-lto"], root=self.root), 0)
            for action in (prepare, build, which, download, version):
                action.assert_not_called()
        self.assertFalse(output.exists())
        recipe = json.loads(stdout.getvalue())
        self.assertEqual(recipe["compiler_options"]["engine_flags"], ["-O2", "-flto=1"])
        self.assertEqual(recipe["compiler_options"]["platform_flags"], ["-Os", "-fno-lto"])
        self.assertIn("capture.c", recipe["platform_sources"])
        self.assertEqual(recipe["identity_object"], "capture_identity.o")
        self.assertEqual(recipe["defines"], ["-DN2D_DETERMINISTIC_TIME=1"])
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                n2d_doom.main(["prepare", "--engine-lto"])

    def test_cli_build_passes_explicit_options_without_environment_hooks(self) -> None:
        with mock.patch.object(n2d_doom, "build", return_value={}) as build, \
                mock.patch.dict(os.environ, {"N2D_OPT": "-O3", "N2D_EXTRA_CFLAGS": "-ffast-math"}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(n2d_doom.main(["build", "--variant", "production", "--engine-opt", "O2",
                                           "--engine-lto"], root=self.root), 0)
            self.assertEqual(build.call_args.kwargs, {"engine_opt": "O2", "engine_lto": True})
            self.assertEqual(n2d_doom.main(["build", "--variant", "production"], root=self.root), 0)
            self.assertEqual(build.call_args.kwargs, {"engine_opt": None, "engine_lto": None})
            self.assertEqual(n2d_doom.main(["build", "--variant", "production", "--engine-opt",
                                           "Os", "--no-engine-lto"], root=self.root), 0)
            self.assertEqual(build.call_args.kwargs, {"engine_opt": "Os", "engine_lto": False})

    def test_variant_default_compiler_profiles(self) -> None:
        # Why: production and deterministic use the measured O2 + engine-LTO profile;
        # render verification keeps -Os so its call witnesses survive.
        for variant, flags in (("production", ["-O2", "-flto=1"]),
                               ("deterministic", ["-O2", "-flto=1"]),
                               ("render-verification", ["-Os"])):
            options = n2d_doom.variant_compiler_options(variant)
            self.assertEqual(options["engine_flags"], flags)
            self.assertEqual(options["platform_flags"][0], "-Os")
        self.assertEqual(n2d_doom.variant_compiler_options("production", "Os", False),
                         n2d_doom.compiler_options("Os", False))
        self.assertEqual(n2d_doom.variant_compiler_options("render-verification", engine_lto=True)
                         ["engine_flags"], ["-Os", "-flto=1"])
        with self.assertRaises(ValueError):
            n2d_doom.variant_compiler_options("unknown")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(n2d_doom.main(["build", "--plan", "--variant", "all"]), 0)
        plans = json.loads(stdout.getvalue())
        self.assertEqual(plans["production"]["compiler_options"]["link_flags"],
                         ["-O2", "-flto=1", "-flto-partition=one"])
        self.assertEqual(plans["render-verification"]["compiler_options"]["engine_flags"], ["-Os"])

    def test_local_archive_preferred_and_corruption_never_fetches(self) -> None:
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); local = root/'vendor'; local.mkdir()
            source = local/'input.tar.gz'; source.write_bytes(b'exact')
            expected = hashlib.sha256(b'exact').hexdigest()
            output = root/'out/input.tar.gz'
            with mock.patch.object(n2d_doom, 'VENDORED_DOWNLOADS', local), mock.patch.object(n2d_doom.urllib.request, 'urlopen') as network:
                n2d_doom._download('unused', output, expected)
                self.assertEqual(output.read_bytes(), b'exact')
                output.unlink(); source.write_bytes(b'bad')
                with self.assertRaises(ValueError): n2d_doom._download('unused', output, expected)
                self.assertFalse(output.exists())
                network.assert_not_called()

    def test_fetch_names_its_user_agent(self) -> None:
        import hashlib, io
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root/'out/input.deb'
            with mock.patch.object(n2d_doom, 'VENDORED_DOWNLOADS', root/'vendor'), \
                    mock.patch.object(n2d_doom.urllib.request, 'urlopen', return_value=io.BytesIO(b'deb')) as network:
                n2d_doom._download('http://example.invalid/input.deb', output, hashlib.sha256(b'deb').hexdigest())
            request = network.call_args[0][0]
            self.assertEqual(request.get_header('User-agent'), n2d_doom.USER_AGENT)
            self.assertEqual(output.read_bytes(), b'deb')

    def test_inputs_are_immutable_and_licensed(self) -> None:
        self.assertEqual(40, len(n2d_doom.ENGINE_REVISION))
        self.assertEqual(64, len(n2d_doom.ENGINE_SHA256))
        self.assertEqual(64, len(n2d_doom.FREEDOOM_SHA256))
        self.assertEqual(64, len(n2d_doom.WAD_SHA256))
        self.assertEqual(64, len(n2d_doom.PICOLIBC_SHA256))
        self.assertEqual(0x02000000, n2d_doom.WAD_ADDRESS)
        self.assertEqual(28_795_076, n2d_doom.WAD_BYTE_LENGTH)
        self.assertEqual(0x03B760C4, n2d_doom.WAD_END_ADDRESS)
        self.assertEqual("n2d-platform-v1", n2d_doom.MEMORY_MAP_REVISION)
        self.assertEqual("GPL-2.0-or-later", n2d_doom.ENGINE_LICENSE)

    def test_hash_is_byte_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fixture"
            path.write_bytes(b"nand2doom\n")
            self.assertEqual(
                "f599639a6bfa1989af748f66d7f61f8708add23b8aa574098670c791f2e5abf4",
                n2d_doom._sha256(path),
            )

    def test_output_is_confined_before_prepare_or_build(self) -> None:
        # Why: under bazel test the test's scratch is an allowed output and holds tempfile's folders.
        environment = mock.patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        for name in ("TEST_UNDECLARED_OUTPUTS_DIR", "TEST_TMPDIR"):
            os.environ.pop(name, None)
        accepted = self.root / "workdir/software/confined-doom"
        self.assertEqual(
            accepted.resolve(), n2d_doom._confined_output(self.root, accepted)
        )
        with tempfile.TemporaryDirectory() as temporary:
            escaped = Path(temporary) / "escaped-doom"
            with self.assertRaisesRegex(ValueError, "below"):
                n2d_doom._confined_output(self.root, escaped)
            with self.assertRaisesRegex(ValueError, "below"):
                n2d_doom.prepare(escaped, self.root)
            with mock.patch.object(n2d_doom, "prepare") as prepare:
                with self.assertRaisesRegex(ValueError, "below"):
                    n2d_doom.build(escaped, self.root, None)
                prepare.assert_not_called()
            result = subprocess.run(
                [sys.executable, str(REPO_ROOT / "src/nand2doom/scripts/sw_build/n2d_doom.py"),
                 "prepare", "--output", str(escaped)],
                capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("output must be below", result.stderr)
            self.assertFalse(escaped.exists())

    def test_output_may_be_in_the_bazel_test_scratch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.dict(os.environ, {"TEST_UNDECLARED_OUTPUTS_DIR": temporary}):
            inside = Path(temporary) / "software/doom"
            self.assertEqual(inside.resolve(), n2d_doom._confined_output(self.root, inside))

    def _fetch_with_cache_error(self, code: int) -> tuple[Path, Path]:
        temporary = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary)
        source = temporary / "source.bin"
        source.write_bytes(b"nand2doom\n")
        cache = temporary / "cache"
        destination = temporary / "out/source.bin"
        with mock.patch.object(n2d_doom.shutil, "copyfile", side_effect=OSError(code, "cache")):
            n2d_doom._download(source.as_uri(), destination,
                               "f599639a6bfa1989af748f66d7f61f8708add23b8aa574098670c791f2e5abf4", cache)
        return destination, cache

    def test_a_read_only_download_cache_is_not_filled(self) -> None:
        destination, cache = self._fetch_with_cache_error(errno.EROFS)
        self.assertEqual(b"nand2doom\n", destination.read_bytes())
        self.assertEqual([], list(cache.iterdir()))

    def test_other_download_cache_errors_still_fail(self) -> None:
        with self.assertRaises(OSError):
            self._fetch_with_cache_error(errno.ENOSPC)

    def test_doom_binutils_follow_the_named_compiler(self) -> None:
        source = (REPO_ROOT / "src/nand2doom/scripts/sw_build/n2d_doom.py").read_text()
        # Why: a riscv64-elf host has no riscv64-unknown-elf-* tool, so every lookup goes
        # through the shared resolver (#221).
        self.assertNotIn("which(\"riscv64-unknown-elf", source)
        self.assertNotIn("with_name(\"riscv64-unknown-elf", source)

    def test_output_rejects_existing_symlink_component(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.root / "workdir") as temporary:
            link = Path(temporary) / "redirect"
            link.symlink_to(Path(tempfile.gettempdir()), target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symbolic-link"):
                n2d_doom._confined_output(self.root, link / "doom")

    def test_prepare_rejects_redirected_nested_output_before_download(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.root / "workdir") as temporary:
            output = Path(temporary) / "doom"
            output.mkdir()
            (output / "downloads").symlink_to(
                Path(tempfile.gettempdir()), target_is_directory=True
            )
            with mock.patch.object(n2d_doom, "_download") as download:
                with self.assertRaisesRegex(ValueError, "symbolic-link child"):
                    n2d_doom.prepare(output, self.root)
                download.assert_not_called()

    def test_picolibc_verification_hashes_consumed_artifacts_and_rejects_tls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary)
            for relative in (*n2d_doom.PICOLIBC_CONSUMED, n2d_doom.PICOLIBC_COPYRIGHT_PATH):
                (prefix / relative).parent.mkdir(parents=True, exist_ok=True)
                (prefix / relative).write_bytes(relative.encode())
            config = prefix / "include/picolibc.h"
            config.parent.mkdir(parents=True)
            config.write_text("#undef NEWLIB_TLS\n#undef PICOLIBC_TLS\n#define TINY_STDIO\n")
            hashes = n2d_doom._verify_picolibc(prefix)
            self.assertEqual(n2d_doom._sha256(prefix / "lib/libc.a"), hashes["lib/libc.a"])
            self.assertEqual(n2d_doom._tree_sha256(prefix / "include"), hashes["include/"])
            config.write_text("#define PICOLIBC_TLS\n")
            with self.assertRaisesRegex(RuntimeError, "thread-local storage"):
                n2d_doom._verify_picolibc(prefix)
            config.unlink()
            with self.assertRaisesRegex(RuntimeError, "configuration header missing"):
                n2d_doom._verify_picolibc(prefix)
            (prefix / "lib/libdummyhost.a").unlink()
            with self.assertRaisesRegex(RuntimeError, "artifact missing"):
                n2d_doom._verify_picolibc(prefix)

    def test_warning_exceptions_apply_only_to_upstream_sources(self) -> None:
        arguments = ("gcc", Path("engine"), Path("include"), Path("output"),
                     REPO_ROOT)
        local = n2d_doom._compiler_flags(*arguments, upstream=False)
        upstream = n2d_doom._compiler_flags(*arguments, upstream=True)
        self.assertFalse(any(flag.startswith("-Wno-") for flag in local))
        self.assertEqual(
            set(n2d_doom.UPSTREAM_WARNING_EXCEPTIONS),
            {flag for flag in upstream if flag.startswith("-Wno-")},
        )
        self.assertIn("-Werror", local)

    def test_upstream_object_list_has_no_host_backend(self) -> None:
        self.assertIn("doomgeneric", n2d_doom.ENGINE_OBJECTS)
        self.assertIn("w_file", n2d_doom.ENGINE_OBJECTS)
        self.assertNotIn("doomgeneric_xlib", n2d_doom.ENGINE_OBJECTS)
        self.assertNotIn("w_file_stdc", n2d_doom.ENGINE_OBJECTS)

    def test_completed_display_has_an_explicit_scene_hook(self) -> None:
        source = (REPO_ROOT / "src/nand2doom/scripts/sw_build/n2d_doom.py").read_text()
        self.assertIn('display = engine_dir / "d_main.c"', source)
        self.assertIn("N2D_RecordSceneReady", source)

    def test_direct_render_hooks_are_generated_patches(self) -> None:
        source = (REPO_ROOT / "src/nand2doom/scripts/sw_build/n2d_doom.py").read_text()
        self.assertIn("text = _adapt_framebuffer_acquire(text)", source)
        self.assertIn("text = _adapt_direct_video(text)", source)
        self.assertIn('f"DG_ScreenBuffer = {BACK_FRAMEBUFFER};"', source)
        display = ('#include "doomstat.h"\n' + n2d_doom.DISPLAY_SEAM
                   + "\t\t\n    redrawsbar = false;\n" + n2d_doom.WIPE_SEAM)
        adapted = n2d_doom._adapt_framebuffer_acquire(display)
        self.assertEqual(adapted, n2d_doom._adapt_framebuffer_acquire(adapted))
        self.assertIn(n2d_doom.DISPLAY_SEAM + n2d_doom.DISPLAY_ACQUIRE, adapted)
        self.assertIn(n2d_doom.WIPE_SEAM + "\tN2D_FramebufferAcquire();\n", adapted)
        prior = ('#include "doomstat.h"\nextern void N2D_FramebufferAcquire(void);\n'
                 + n2d_doom.DISPLAY_SEAM + "    N2D_FramebufferAcquire();\n"
                 + "\t\t\n    redrawsbar = false;\n" + n2d_doom.WIPE_SEAM
                 + "\tN2D_FramebufferAcquire();\n")
        self.assertEqual(n2d_doom._adapt_framebuffer_acquire(prior), adapted)
        for invalid in (display.replace("nodrawers", "other"), display + display,
                        adapted.replace(n2d_doom.WIPE_ACQUIRE, "")
                        .replace(n2d_doom.WIPE_SEAM, "")):
            with self.assertRaises(RuntimeError):
                n2d_doom._adapt_framebuffer_acquire(invalid)
        video = ("\tI_VideoBuffer = (byte*)Z_Malloc (SCREENWIDTH * SCREENHEIGHT, PU_STATIC, "
                 "NULL);  // For DOOM to draw on\n\tZ_Free (I_VideoBuffer);\n"
                 "void I_FinishUpdate (void)\n{\n    int y;\n    memcpy(a, b, c);\n\n"
                 "\tDG_DrawFrame();\n}\n")
        direct = n2d_doom._adapt_direct_video(video)
        self.assertEqual(direct, n2d_doom._adapt_direct_video(direct))
        self.assertIn("I_VideoBuffer = (byte *)DG_ScreenBuffer;", direct)
        self.assertNotIn("Z_Free", direct)
        self.assertNotIn("memcpy", direct)
        with self.assertRaises(RuntimeError):
            n2d_doom._adapt_direct_video(video.replace("Z_Malloc", "malloc"))

    def test_draw_loop_seam_is_pinned_and_idempotent(self) -> None:
        source = (REPO_ROOT / "src/nand2doom/scripts/sw_build/n2d_doom.py").read_text()
        self.assertIn('draw = engine_dir / "r_draw.c"', source)
        upstream = _upstream_r_draw(self)
        adapted = n2d_doom._adapt_draw_loops(upstream)
        self.assertEqual(adapted, n2d_doom._adapt_draw_loops(adapted))
        self.assertEqual(adapted.count(n2d_doom.DRAW_LOOPS_MARKER), 2)
        # Why: only the active bodies may change; the unrolled #if 0 variants stay upstream.
        self.assertEqual(adapted.count("void R_DrawColumn (void) \n"), 1)
        self.assertEqual(adapted.count("void R_DrawSpan (void) \n"), 1)
        for invalid in (upstream.replace("dc_source[(frac>>FRACBITS)&127]", "dc_source[0]", 1),
                        adapted.replace(n2d_doom.DRAW_SPAN, "")):
            with self.assertRaises(RuntimeError):
                n2d_doom._adapt_draw_loops(invalid)

    def test_draw_loop_rewrite_writes_identical_pixels(self) -> None:
        compiler = shutil.which("cc") or shutil.which("gcc")
        if compiler is None:
            self.skipTest("host C compiler unavailable")
        upstream = _upstream_r_draw(self)
        bodies = []
        for name in ("R_DrawColumn", "R_DrawSpan"):
            match = re.search(rf"^void {name} \(void\) *\n\{{ *\n.*?\n\}} *\n",
                              upstream, flags=re.MULTILINE | re.DOTALL)
            bodies.append(f"#define {name} upstream_{name}\n{match.group()}#undef {name}\n")
        program = (DRAW_HARNESS_PRELUDE + "".join(bodies) + n2d_doom.DRAW_COLUMN
                   + n2d_doom.DRAW_SPAN + DRAW_HARNESS_MAIN)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "draw.c"
            binary = Path(directory) / "draw"
            source.write_text(program)
            subprocess.run([compiler, "-O2", "-fwrapv", "-Wall", "-Werror", "-o",
                            str(binary), str(source)], check=True, timeout=60)
            result = subprocess.run([str(binary)], capture_output=True, text=True,
                                    timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("columns=20000 spans=20000 identical", result.stdout)

    def test_drawer_empty_remainder_and_wrap_boundaries(self) -> None:
        compiler = shutil.which("cc") or shutil.which("gcc")
        if compiler is None:
            self.skipTest("host C compiler unavailable")
        program = (DRAW_HARNESS_PRELUDE + DRAW_SCALAR_REFERENCE + n2d_doom.DRAW_COLUMN
                   + n2d_doom.DRAW_SPAN + DRAW_BOUNDARY_MAIN)
        with tempfile.TemporaryDirectory() as directory:
            source, binary = Path(directory) / "draw.c", Path(directory) / "draw"
            source.write_text(program)
            subprocess.run([compiler, "-O2", "-fwrapv", "-Wall", "-Werror", "-o",
                            str(binary), str(source)], check=True, timeout=60)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("boundary cases=6804 identical", result.stdout)

    def test_status_bar_persistence_keeps_full_copy_cases(self) -> None:
        hook = n2d_doom.DISPLAY_ACQUIRE
        # Why: only a full-width level view, with no wipe, resize, menu or automap, may
        # skip the rows it redraws; the wipe step always copies everything.
        for condition in ("gamestate == GS_LEVEL", "gamestate == wipegamestate",
                          "gametic", "!setsizeneeded", "!automapactive", "!menuactive",
                          "scaledviewwidth == SCREENWIDTH", "? viewheight : 0"):
            self.assertIn(condition, hook)
        self.assertEqual(n2d_doom.WIPE_ACQUIRE, "\tN2D_FramebufferAcquire();\n")
        platform = (REPO_ROOT / "src/nand2doom/sw/doom/platform.c").read_text()
        self.assertIn("void N2D_FramebufferAcquire(void) { N2D_FramebufferAcquireFrom(0); }",
                      platform)
        self.assertIn("word = (size_t)first_row * 80u", platform)
        self.assertIn("if (first_row < 0 || first_row > 200) first_row = 0;", platform)

    def test_visplane_seam_is_pinned_and_idempotent(self) -> None:
        source = (REPO_ROOT / "src/nand2doom/scripts/sw_build/n2d_doom.py").read_text()
        self.assertIn('plane = engine_dir / "r_plane.c"', source)
        upstream = _upstream_source(self, "r_plane.c")
        adapted = n2d_doom._adapt_visplanes(upstream)
        self.assertEqual(adapted, n2d_doom._adapt_visplanes(adapted))
        self.assertEqual(adapted.count(n2d_doom.VISPLANES_MARKER), 2)
        self.assertEqual(adapted.count("memset (pl->top"), 4)
        self.assertNotIn("memset (check->top", adapted)
        for invalid in (upstream.replace("check->maxx = -1;", "check->maxx = 0;", 1),
                        upstream.replace("if (pl->top[x] != 0xff)", "if (pl->top[x])", 1),
                        adapted.replace(n2d_doom.CHECK_PLANE, "")):
            with self.assertRaises(RuntimeError):
                n2d_doom._adapt_visplanes(invalid)

    def test_lazy_visplane_fill_is_equivalent(self) -> None:
        compiler = shutil.which("cc") or shutil.which("gcc")
        if compiler is None:
            self.skipTest("host C compiler unavailable")
        upstream = _upstream_source(self, "r_plane.c")
        bodies = []
        for name in ("R_FindPlane", "R_CheckPlane"):
            match = re.search(rf"^visplane_t\*\n{name}\n\(.*?\n\}}\n",
                              upstream, flags=re.MULTILINE | re.DOTALL)
            bodies.append(f"#define {name} upstream_{name}\n{match.group()}#undef {name}\n")
        lazy = ("#define visplanes visplanes_lazy\n#define lastvisplane lastvisplane_lazy\n"
                + n2d_doom.FIND_PLANE + n2d_doom.CHECK_PLANE
                + "#undef visplanes\n#undef lastvisplane\n")
        program = VISPLANE_HARNESS_PRELUDE + "".join(bodies) + lazy + VISPLANE_HARNESS_MAIN
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "planes.c"
            binary = Path(directory) / "planes"
            source.write_text(program)
            subprocess.run([compiler, "-O2", "-Wall", "-Werror", "-o",
                            str(binary), str(source)], check=True, timeout=60)
            result = subprocess.run([str(binary)], capture_output=True, text=True,
                                    timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        match = re.search(r"checks=(\d+) grows=(\d+) splits=(\d+) identical", result.stdout)
        self.assertIsNotNone(match, result.stdout)
        # Why: the equivalence means little unless the workload exercises creation, growth
        # on both kinds and splits.
        self.assertTrue(all(int(value) > 1000 for value in match.groups()), result.stdout)

    def test_build_maps_disposable_paths_out_of_firmware(self) -> None:
        source = (REPO_ROOT / "src/nand2doom/scripts/sw_build/n2d_doom.py").read_text()
        self.assertIn("-ffile-prefix-map={output.resolve()}=/n2d-doom-build", source)
        self.assertIn("-fmacro-prefix-map={repository.resolve()}=/nand2anything", source)

    def test_linker_layout_reserves_tls_zone_stack_and_wad(self) -> None:
        script = (REPO_ROOT / "src/nand2doom/sw/doom/n2d_doom.ld").read_text()
        self.assertIn(".bss ALIGN(ADDR(.tbss) + SIZEOF(.tbss), 16)", script)
        self.assertIn("__heap_end = 0x00700000", script)
        self.assertIn("__zone_start = 0x00800000", script)
        self.assertIn("__zone_end = 0x01800000", script)
        self.assertIn("__stack_bottom = 0x01e00000", script)
        self.assertIn("__wad_start = 0x02000000", script)
        self.assertIn("__wad_end = 0x03b760c4", script)
        self.assertIn("ASSERT(__tls_end <= __bss_start", script)

    def test_variants_are_production_and_deterministic(self) -> None:
        self.assertEqual(set(n2d_doom.VARIANTS), {"production", "deterministic", "render-verification"})
        self.assertEqual(n2d_doom.VARIANTS["production"]["defines"], [])
        self.assertEqual(n2d_doom.VARIANTS["production"]["output"],
                         "workdir/software/n2d_doom")
        self.assertEqual(n2d_doom.VARIANTS["deterministic"]["defines"],
                         ["-DN2D_DETERMINISTIC_TIME=1"])
        self.assertNotEqual(n2d_doom.VARIANTS["deterministic"]["output"],
                            n2d_doom.VARIANTS["production"]["output"])
        with self.assertRaisesRegex(ValueError, "unknown Doom firmware variant"):
            n2d_doom.build(self.root / "workdir/software/x", self.root, None, "fast")

    def test_variant_selection(self) -> None:
        select = n2d_doom.selected_variants
        self.assertEqual(select("build", None, None), ["production", "deterministic"])
        self.assertEqual(select("build", "all", None),
                         ["production", "deterministic", "render-verification"])
        self.assertEqual(select("build", "deterministic", None), ["deterministic"])
        # Why: an explicit output without a variant has always meant the production image.
        self.assertEqual(select("build", None, "workdir/software/x"), ["production"])
        self.assertEqual(select("build", "deterministic", "workdir/software/x"),
                         ["deterministic"])
        self.assertEqual(select("prepare", None, None), ["production"])
        with self.assertRaisesRegex(ValueError, "--output requires a single --variant"):
            select("build", "all", "workdir/software/x")

    def test_variant_cli_rejects_one_output_for_all_variants(self) -> None:
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "src/nand2doom/scripts/sw_build/n2d_doom.py"),
             "build", "--variant", "all", "--output", "workdir/software/x"],
            capture_output=True, text=True, check=False,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("--output requires a single --variant", result.stderr)

    def test_seed_inputs_hard_links_production_downloads_and_toolchain(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            (repository / "workdir/software").mkdir(parents=True)
            source = repository / n2d_doom.VARIANTS["production"]["output"]
            (source / "downloads").mkdir(parents=True)
            (source / "downloads/archive.zip").write_bytes(b"pinned")
            (source / "downloads/partial.zip.part").write_bytes(b"x")
            libc = source / "toolchain" / n2d_doom.TOOLCHAIN_LIBC
            libc.parent.mkdir(parents=True)
            libc.write_bytes(b"libc")
            target = repository / n2d_doom.VARIANTS["deterministic"]["output"]
            n2d_doom._seed_inputs(target.resolve(), repository)
            self.assertEqual((target / "downloads/archive.zip").read_bytes(), b"pinned")
            self.assertFalse((target / "downloads/partial.zip.part").exists())
            self.assertEqual((target / "toolchain" / n2d_doom.TOOLCHAIN_LIBC).read_bytes(),
                             b"libc")
            self.assertEqual((target / "downloads/archive.zip").stat().st_ino,
                             (source / "downloads/archive.zip").stat().st_ino)
            n2d_doom._seed_inputs(source.resolve(), repository)
            (source / "downloads/new.zip").write_bytes(b"new")
            n2d_doom._seed_inputs(target.resolve(), repository)
            self.assertFalse((target / "downloads/new.zip").exists())
    def test_partial_production_toolchain_is_never_shared_or_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            source = repository / n2d_doom.VARIANTS["production"]["output"]
            partial = source / "toolchain/picolibc/include/stdio.h"
            partial.parent.mkdir(parents=True)
            partial.write_bytes(b"production")
            target = (repository / n2d_doom.VARIANTS["deterministic"]["output"]).resolve()
            n2d_doom._seed_inputs(target, repository)
            self.assertFalse((target / "toolchain").exists())
            # Why: a partial tree that shares a link must be rebuilt from scratch: the
            # link is removed, never written through.
            shared = target / "toolchain/picolibc/include/stdio.h"
            shared.parent.mkdir(parents=True)
            os.link(partial, shared)
            runs = self._fake_picolibc_source(target)
            with self._fake_picolibc_build(runs):
                n2d_doom._picolibc(target, None, "gcc")
            self.assertEqual(shared.read_bytes(), b"built")
            self.assertEqual(partial.read_bytes(), b"production")

    def test_picolibc_build_is_reused_until_its_recipe_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            runs = self._fake_picolibc_source(output)
            with self._fake_picolibc_build(runs):
                prefix = n2d_doom._picolibc(output, None, "gcc")
                self.assertEqual(2, len(runs))  # Why: meson setup, then meson install
                setup = runs[0]
                self.assertIn("-Dthread-local-storage=false", setup)
                cross = Path(next(a for a in setup if a.startswith("--cross-file="))
                             .split("=", 1)[1]).read_text()
                self.assertIn("-msave-restore", cross)
                self.assertEqual(b"license", (prefix / "COPYING.picolibc").read_bytes())
                n2d_doom._picolibc(output, None, "gcc")
                self.assertEqual(2, len(runs))
            with self._fake_picolibc_build(runs, version="gcc 2"):
                n2d_doom._picolibc(output, None, "gcc")
                self.assertEqual(4, len(runs))
            self.assertEqual(Path("/override"),
                             n2d_doom._picolibc(output, Path("/override"), "gcc"))

    @staticmethod
    def _fake_picolibc_source(output: Path) -> list:
        source = output / "downloads" / f"picolibc-{n2d_doom.PICOLIBC_VERSION}.tar.xz"
        source.parent.mkdir(parents=True, exist_ok=True)
        tree = f"picolibc-{n2d_doom.PICOLIBC_VERSION}/"
        source.write_bytes(lzma.compress(_tarball({tree + "COPYING.picolibc": b"license",
                                                   tree + "meson.build": b""})))
        return []

    @staticmethod
    @contextlib.contextmanager
    def _fake_picolibc_build(runs: list, version: str = "gcc 1"):
        def run(command, **_):
            runs.append(command)
            if "install" in command:
                prefix = Path(next(a for a in runs[-2] if a.startswith("--prefix="))
                              .split("=", 1)[1])
                (prefix / "include").mkdir(parents=True)
                (prefix / "include/stdio.h").write_bytes(b"built")
                (prefix / "lib").mkdir()
                (prefix / "lib/libc.a").write_bytes(b"libc")
                Path(command[command.index("-C") + 1]).mkdir(exist_ok=True)

        with mock.patch.object(n2d_doom, "_download"), \
             mock.patch.object(n2d_doom, "_version", return_value=version), \
             mock.patch.object(n2d_doom.n2d_image, "_tool", side_effect=lambda _n, d: d), \
             mock.patch.object(n2d_doom.subprocess, "run", side_effect=run):
            yield

    @staticmethod
    def _guarded(source: str, line: str) -> bool:
        """True when `line` sits inside an #ifdef N2D_DETERMINISTIC_TIME block."""
        guarded = []
        for text in source.splitlines():
            stripped = text.strip()
            if stripped.startswith("#if"):
                guarded.append(stripped == "#ifdef N2D_DETERMINISTIC_TIME")
            elif stripped.startswith("#endif"):
                guarded.pop()
            if text == line:
                return any(guarded)
        return False

    def test_deterministic_time_is_compiled_out_of_production(self) -> None:
        doom = REPO_ROOT / "src/nand2doom/sw/doom"
        clock = (doom / "clock.c").read_text()
        production = clock.split("#elif defined(N2D_DETERMINISTIC_TIME)")[0] + \
            clock.split("#else\nstatic uint32_t n2d_clock_read_tick")[1]
        self.assertNotIn("minstret", production)
        self.assertIn("csrr %0, minstret", clock)
        # Why: the production binary's byte identity is the real guarantee; this only
        # checks that the frame-seam waits are compiled out of it.
        platform = (doom / "platform.c").read_text()
        for symbol in ("video_wait(", "video_mark("):
            for line in platform.splitlines():
                if symbol in line and not line.startswith(("static", "/*")):
                    self.assertTrue(self._guarded(platform, line), line)
        capture = (doom / "capture.c").read_text()
        self.assertIn("#define N2D_WAIT_BEGIN() ((void)0)", capture)
        # Why: host probes copy only capture.c and capture.h, so production capture.c
        # must not need clock.h.
        self.assertTrue(capture.index('#include "clock.h"') > capture.index(
            "#ifdef N2D_DETERMINISTIC_TIME"))


if __name__ == "__main__":
    unittest.main()
