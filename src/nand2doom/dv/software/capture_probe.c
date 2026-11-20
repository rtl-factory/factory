/* Independent fake video/time devices around the production capture algorithm. */
#include "capture.h"

const uint32_t n2d_capture_identity[8] = {1,2,3,4,5,6,7,8};
#ifdef N2D_CAPTURE_HOST
uint32_t capture_results[128];
#else
#define capture_results ((volatile uint32_t *)0x8000u)
#endif
static uint32_t now, status, uploads, commits, seen_ready, mode, cancel_at;
static uint32_t colors[256], banks[2][256], calls, failures;
static uint32_t replace_at, acquired;

void N2D_CaptureAcquire(void) {
    if (n2d_capture.state != CAP_PREPARING || !n2d_capture.request
            || n2d_capture.last != n2d_capture.request) ++failures;
    acquired = n2d_capture.request;
}

uint32_t N2D_CaptureVideo(void) {
    ++calls;
    if (cancel_at && calls == cancel_at) n2d_capture.request = 0;
    if (replace_at && calls == replace_at) n2d_capture.request = 2;
    if (mode != 2 && mode != 4) {
        if (status & 2u) status = (status ^ 1u) & ~2u;
        status += 4u;
    }
    if (mode == 3 && n2d_capture.state == CAP_PREPARING) return status ^ 1u;
    return status;
}
void N2D_CaptureColor(uint32_t index, uint32_t value) {
    if (status & 2u) ++failures;
    if (value != ((index * 13u) & 4095u)) ++failures;
    banks[(status & 1u) ^ 1u][index] = value;
    ++uploads;
}
void N2D_CaptureCommit(void) {
    ++commits;
    if (mode == 1) status ^= 1u; else status |= 2u;
}
uint32_t N2D_CaptureTime(void) {
    /* Host request arrives during scene service, after the platform seam. */
    if (mode == 6 && n2d_capture.palette_known && !n2d_capture.last
            && n2d_capture.state == CAP_IDLE) n2d_capture.request = 1;
    if (n2d_capture.ready && n2d_capture.request == n2d_capture.ready) {
        ++seen_ready;
        if (acquired != n2d_capture.ready || n2d_capture.state != CAP_READY || (status & 3u) != (n2d_capture.video & 1u))
            ++failures;
        for (uint32_t i = 0; i < 256; ++i)
            if (banks[status & 1u][i] != n2d_capture.palette[i]) ++failures;
        n2d_capture.request = mode == 5 ? 2u : 0u;
    }
    return now++;
}
static void reset(uint32_t test_mode) {
    now = status = uploads = commits = seen_ready = calls = cancel_at = failures = 0;
    replace_at = acquired = 0;
    mode = test_mode;
    for (uint32_t i = 0; i < 256; ++i) colors[i] = 0xface0000u | (i * 13u);
    N2D_CaptureInit();
}
static void record(uint32_t *out) {
    capture_results[(*out)++] = n2d_capture.state;
    capture_results[(*out)++] = n2d_capture.error;
    capture_results[(*out)++] = seen_ready;
    capture_results[(*out)++] = failures;
}
void c_entry(void) {
    uint32_t out = 0;
    reset(0); N2D_CaptureScene(1); record(&out); /* ordinary empty frame */
    reset(0); n2d_capture.request=1; N2D_CaptureScene(0); record(&out); /* title */
    reset(0); n2d_capture.request=1; N2D_CaptureScene(1); record(&out); /* unknown */
    for (uint32_t m=0; m<2; ++m) {
        reset(m); N2D_CaptureUpload(colors); N2D_CaptureFrame();
        n2d_capture.request=1; N2D_CaptureScene(1); record(&out);
        n2d_capture.request=2; N2D_CaptureScene(1); record(&out); /* clean repeat */
        n2d_capture.request=2; N2D_CaptureScene(1); record(&out); /* duplicate */
        n2d_capture.request=0; N2D_CaptureScene(1); record(&out);
    }
    reset(2); status=2; N2D_CaptureUpload(colors); record(&out); /* prior pending timeout */
    reset(3); N2D_CaptureUpload(colors); n2d_capture.request=1;
    N2D_CaptureScene(1); record(&out); /* incorrect active bank */
    reset(4); N2D_CaptureUpload(colors); status=1; n2d_capture.request=1;
    N2D_CaptureScene(1); record(&out); /* raster timeout */
    for (uint32_t at=1; at<=6; ++at) {
        reset(1); N2D_CaptureUpload(colors); calls=0; cancel_at=at;
        n2d_capture.request=1; N2D_CaptureScene(1); record(&out);
    }
    reset(0); N2D_CaptureUpload(colors); N2D_CaptureUpload(colors);
    capture_results[out++] = uploads;
    capture_results[out++] = commits;
    capture_results[out++] = failures;
    capture_results[out++] = n2d_capture.magic;
    reset(1); now=0xfffffff0u; status=0xfffffff9u;
    N2D_CaptureUpload(colors); n2d_capture.request=0xffffffffu;
    N2D_CaptureScene(1); record(&out);
    n2d_capture.request=1; N2D_CaptureScene(1); record(&out);
    reset(1); N2D_CaptureUpload(colors); mode=2; status=3;
    calls=0; cancel_at=3; n2d_capture.request=1;
    N2D_CaptureScene(1); record(&out); /* cancel while waiting for pending */
    reset(1); N2D_CaptureUpload(colors); calls=0; replace_at=3;
    n2d_capture.request=1; N2D_CaptureScene(1); record(&out);
    reset(5); N2D_CaptureUpload(colors); n2d_capture.request=1;
    N2D_CaptureScene(1); record(&out); /* replace parked request */
    n2d_capture.request=3; N2D_CaptureScene(1); record(&out); /* error needs zero */
    n2d_capture.request=0; N2D_CaptureScene(1); record(&out);
    mode=1; n2d_capture.request=4; N2D_CaptureScene(1); record(&out);
    reset(1); N2D_CaptureUpload(colors); mode=6;
    N2D_CaptureScene(1); record(&out); /* request arriving inside the scene seam */
    capture_results[out] = out;
}
