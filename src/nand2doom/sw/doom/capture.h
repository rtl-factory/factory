/* Complete-frame capture protocol; GPL-2.0-or-later. */
#ifndef N2D_CAPTURE_H
#define N2D_CAPTURE_H
#include <stdint.h>

#define N2D_CAPTURE_MAGIC 0x4332444eu
#define N2D_CAPTURE_ABI 1u
#define N2D_CAPTURE_WORDS 280u
enum { CAP_IDLE, CAP_PREPARING, CAP_READY, CAP_ERROR };
enum { CAP_OK, CAP_UNKNOWN_PALETTE, CAP_BANK, CAP_TIMEOUT, CAP_SEQUENCE };
/* All fields are LE u32. Only request belongs to the host. Reserved words zero. */
typedef struct {
    uint32_t magic, abi, words, request, state, ready, last, frame;
    uint32_t video, generation, error, palette_known;
    uint32_t identity[8];
    uint32_t reserved[4];
    uint32_t palette[256];
} n2d_capture_mailbox;
extern volatile n2d_capture_mailbox n2d_capture;
extern const uint32_t n2d_capture_identity[8];
void N2D_CaptureInit(void);
int N2D_CaptureUpload(const uint32_t *colors);
void N2D_CaptureFrame(void);
void N2D_CaptureScene(int gameplay);

/* These tiny IO functions are replaced only in compiled verification probes. */
/* Make the back window equal the completed display before READY. */
void N2D_CaptureAcquire(void);
uint32_t N2D_CaptureVideo(void);
void N2D_CaptureColor(uint32_t index, uint32_t value);
void N2D_CaptureCommit(void);
uint32_t N2D_CaptureTime(void);
#endif
