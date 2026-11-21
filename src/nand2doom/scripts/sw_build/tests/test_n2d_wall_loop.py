"""Pinned wall-loop pixel and consumer-visible state equivalence."""
from pathlib import Path
import hashlib
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from src.nand2doom.scripts.sw_build import n2d_doom
from src.nand2doom.scripts.sw_build.tests.test_n2d_doom import _upstream_source

PRELUDE = r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint32_t angle_t;
typedef int32_t fixed_t;
typedef unsigned char byte;
#define SCREENWIDTH 320
#define SCREENHEIGHT 200
#define HEIGHTBITS 12
#define HEIGHTUNIT (1<<HEIGHTBITS)
#define FRACBITS 16
#define ANGLETOFINESHIFT 19
#define LIGHTSCALESHIFT 12
#define MAXLIGHTSCALE 48
static int rw_x, rw_stopx, markceiling, markfloor, segtextured;
static int midtexture, toptexture, bottomtexture, maskedtexture, viewheight;
static fixed_t rw_scale, rw_scalestep, topfrac, topstep, bottomfrac, bottomstep;
static fixed_t pixhigh, pixlow, pixhighstep, pixlowstep;
static angle_t rw_centerangle, xtoviewangle[SCREENWIDTH];
static fixed_t rw_offset, rw_distance, finetangent[8192];
static fixed_t rw_midtexturemid, rw_toptexturemid, rw_bottomtexturemid;
static int dc_x, dc_yl, dc_yh;
static fixed_t dc_texturemid;
static uint32_t dc_iscale;
static byte *dc_source, *dc_colormap;
static byte maps[MAXLIGHTSCALE][256], textures[4][128][128];
static byte *lights[MAXLIGHTSCALE], **walllights;
static short ceilingclip[SCREENWIDTH], floorclip[SCREENWIDTH];
static short masked[SCREENWIDTH], *maskedtexturecol;
typedef struct { byte top[SCREENWIDTH], bottom[SCREENWIDTH]; } plane_t;
static plane_t ceiling, floor, *ceilingplane, *floorplane;
static byte framebuffer[SCREENWIDTH*SCREENHEIGHT];
static uint32_t state = 370;
static uint32_t next(void) {state^=state<<13;state^=state>>17;state^=state<<5;return state;}
static fixed_t FixedMul(fixed_t a, fixed_t b) {return (fixed_t)(((int64_t)a*b)>>16);}
/* Capture globals and memory at BOTH texture lookup and drawing boundaries. */
typedef struct {
    int values[29]; plane_t ceiling, floor;
    short cc[SCREENWIDTH], fc[SCREENWIDTH], masked[SCREENWIDTH];
} witness_t;
static witness_t witnesses[512];
static int nwitness;
static void observe(int phase, int tex, int col) {
    witness_t *w=&witnesses[nwitness++];
    int values[]={phase,tex,col,rw_x,rw_stopx,rw_scale,rw_scalestep,
        topfrac,topstep,bottomfrac,bottomstep,pixhigh,pixlow,pixhighstep,pixlowstep,
        markceiling,markfloor,segtextured,midtexture,toptexture,bottomtexture,
        maskedtexture,dc_x,dc_yl,dc_yh,dc_texturemid,(int)dc_iscale,
        (int)((uintptr_t)dc_source-(uintptr_t)&textures[0][0][0]),
        (int)((uintptr_t)dc_colormap-(uintptr_t)&maps[0][0])};
    memcpy(w->values,values,sizeof values);w->ceiling=ceiling;w->floor=floor;
    memcpy(w->cc,ceilingclip,sizeof ceilingclip);memcpy(w->fc,floorclip,sizeof floorclip);
    memcpy(w->masked,masked,sizeof masked);
}
static byte *R_GetColumn(int tex,int col) {
    observe(0,tex,col);return textures[tex][(unsigned)col&127];
}
static void draw(void) {
    observe(1,0,0);
    uint32_t frac=(uint32_t)dc_texturemid + ((uint32_t)dc_yl-100u)*dc_iscale;
    for(int y=dc_yl;y<=dc_yh;y++) {
        if(y<0||y>=SCREENHEIGHT||dc_x<0||dc_x>=SCREENWIDTH) {puts("invalid draw");return;}
        framebuffer[y*SCREENWIDTH+dc_x]=dc_colormap[dc_source[(frac>>16)&127]];
        frac+=dc_iscale;
    }
}
static void (*colfunc)(void)=draw;
'''
MAIN = r'''
typedef struct {
    int final[13]; plane_t ceiling,floor;
    short cc[SCREENWIDTH],fc[SCREENWIDTH],masked[SCREENWIDTH];
    byte framebuffer[SCREENWIDTH*SCREENHEIGHT];
    witness_t calls[512]; int ncalls;
} output_t;
static output_t outputs[2];
static int start,length,mode,flags,variant;
static void setup(void) {
    rw_x=start;rw_stopx=start+length;
    markceiling=flags&1;markfloor=(flags>>1)&1;
    midtexture=mode==1?1:0;toptexture=(mode&2)?2:0;bottomtexture=(mode&4)?3:0;
    maskedtexture=(mode&8)!=0;
    segtextured=midtexture|toptexture|bottomtexture|maskedtexture;
    rw_scale=0x50000;rw_scalestep=variant&1?-127:251;
    topfrac=(variant==3?-3:30+variant*9)*HEIGHTUNIT-1;
    bottomfrac=(variant==2?211:150-variant*7)*HEIGHTUNIT+1;
    topstep=variant&1?-4097:4081;bottomstep=variant&2?-4081:4097;
    pixhigh=70*HEIGHTUNIT+variant;pixlow=110*HEIGHTUNIT-variant;
    pixhighstep=variant&1?-4099:4091;pixlowstep=variant&2?-4079:4093;
    rw_centerangle=0;rw_offset=variant*65536;rw_distance=0x10000;
    rw_midtexturemid=-12345;rw_toptexturemid=77777;rw_bottomtexturemid=-88888;
    viewheight=168;dc_x=dc_yl=dc_yh=0;dc_source=textures[0][0];dc_colormap=maps[0];
    dc_texturemid=0;dc_iscale=0;walllights=lights;
    ceilingplane=&ceiling;floorplane=&floor;maskedtexturecol=masked;
    memset(&ceiling,0x6a,sizeof ceiling);memset(&floor,0xa6,sizeof floor);
    memset(framebuffer,0x69,sizeof framebuffer);memset(witnesses,0,sizeof witnesses);nwitness=0;
    for(int x=0;x<SCREENWIDTH;x++) {
        ceilingclip[x]=(short)(x%4==0?viewheight:x%19-1);
        floorclip[x]=(short)(x%5==0?0:200-x%23);masked[x]=-1;
        xtoviewangle[x]=(angle_t)(x*7)<<ANGLETOFINESHIFT;
    }
}
static void save(int target) {
    output_t *o=&outputs[target];memset(o,0,sizeof *o);
    int vals[]={rw_x,rw_scale,topfrac,bottomfrac,pixhigh,pixlow,dc_x,dc_yl,dc_yh,
                dc_texturemid,(int)dc_iscale,(int)((uintptr_t)dc_source-(uintptr_t)&textures[0][0][0]),
                (int)((uintptr_t)dc_colormap-(uintptr_t)&maps[0][0])};
    memcpy(o->final,vals,sizeof vals);o->ceiling=ceiling;o->floor=floor;
    memcpy(o->cc,ceilingclip,sizeof ceilingclip);memcpy(o->fc,floorclip,sizeof floorclip);
    memcpy(o->masked,masked,sizeof masked);memcpy(o->framebuffer,framebuffer,sizeof framebuffer);
    memcpy(o->calls,witnesses,sizeof witnesses);o->ncalls=nwitness;
}
int main(void) {
    for(int t=0;t<4;t++)for(int x=0;x<128;x++)for(int y=0;y<128;y++)textures[t][x][y]=(byte)next();
    for(int m=0;m<MAXLIGHTSCALE;m++){lights[m]=maps[m];for(int p=0;p<256;p++)maps[m][p]=(byte)next();}
    for(int i=0;i<8192;i++)finetangent[i]=(fixed_t)(i*171-0x50000);
    int lengths[]={-1,0,1,2,3,7,16,31};int cases=0;
    for(mode=0;mode<16;mode++)for(flags=0;flags<4;flags++)
    for(variant=0;variant<4;variant++)for(unsigned n=0;n<sizeof lengths/sizeof lengths[0];n++) {
        length=lengths[n];start=SCREENWIDTH-32+(variant&1);
        setup();upstream_R_RenderSegLoop();save(0);
        setup();R_RenderSegLoop();save(1);
        if(memcmp(&outputs[0],&outputs[1],sizeof outputs[0])) {
            printf("wall state/pixels differ mode=%d flags=%d variant=%d length=%d\n",mode,flags,variant,length);return 1;
        }
        cases++;
    }
    printf("wall cases=%d callbacks/final state/pixels identical\n",cases);return 0;
}
'''


class WallLoopTests(unittest.TestCase):
    def upstream(self):
        text = _upstream_source(self, "r_segs.c")
        body = re.search(r"^void R_RenderSegLoop \(void\)\n\{\n.*?\n\}\n", text, re.M | re.S).group()
        return text, body

    def test_pinned_seam_upgrade_idempotence_and_rejection(self):
        text, body = self.upstream()
        adapted = n2d_doom._adapt_wall_loop(text)
        self.assertEqual(adapted, n2d_doom._adapt_wall_loop(adapted))
        self.assertEqual(adapted.count(n2d_doom.WALL_LOOP_MARKER), 1)
        for invalid in (text.replace("rw_scale += rw_scalestep", "rw_scale -= rw_scalestep"),
                        adapted.replace("rw_scale = n2d_rw_scale;", ""),
                        adapted + n2d_doom.WALL_LOOP_MARKER,
                        adapted.replace(n2d_doom.WALL_LOOP_MARKER + "\n", "", 1)
                        + n2d_doom.WALL_LOOP_MARKER,
                        adapted.replace("n2d_topfrac = topfrac", "n2d_topfrac = bottomfrac")):
            with self.assertRaises(RuntimeError):
                n2d_doom._adapt_wall_loop(invalid)
        self.assertEqual(hashlib.sha256(body.encode()).hexdigest(), n2d_doom.WALL_LOOP_UPSTREAM_SHA256)

    def test_callback_state_final_state_and_pixels(self):
        compiler = shutil.which("cc")
        if compiler is None:
            self.skipTest("host C compiler unavailable")
        _, body = self.upstream()
        program = PRELUDE + body.replace("R_RenderSegLoop", "upstream_R_RenderSegLoop", 1)
        program += n2d_doom._wall_loop_body(body) + MAIN
        generated = Path(__file__).resolve().parents[5] / "workdir/unit-tests/wall-loop"
        generated.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=generated) as directory:
            source = Path(directory)/"wall.c";binary = Path(directory)/"wall"
            source.write_text(program)
            subprocess.run([compiler, "-O2", "-fwrapv", "-Wall", "-Wextra", "-Werror",
                            str(source), "-o", str(binary)],check=True,timeout=60)
            result = subprocess.run([str(binary)],capture_output=True,text=True,timeout=60)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn("wall cases=2048 callbacks/final state/pixels identical",result.stdout)

    def test_negative_controls_detect_missing_publications(self):
        generate = n2d_doom._wall_loop_body
        for old,new in [("rw_x = ++n2d_rw_x)", "n2d_rw_x++)"),
                        ("rw_scale = n2d_rw_scale;", "")]:
            with self.subTest(publication=old), mock.patch.object(
                    n2d_doom,"_wall_loop_body",side_effect=lambda text:generate(text).replace(old,new)):
                with self.assertRaises(AssertionError):
                    self.test_callback_state_final_state_and_pixels()


if __name__ == "__main__":
    unittest.main()
