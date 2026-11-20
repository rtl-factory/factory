/* Simulation-only renderer. Production capture and clock sources are unmodified. */
#include "capture.h"
#include "clock.h"
const uint32_t n2d_capture_identity[8]={1,2,3,4,5,6,7,8};
static uint32_t colors[256];
static volatile uint32_t *const completion=(volatile uint32_t *)0x20000004u;
/* The probe already copies the front bank before its completed-scene marker. */
void N2D_CaptureAcquire(void) {}
uint32_t N2D_CaptureVideo(void) { return *(volatile uint32_t *)0x20001004u; }
void N2D_CaptureColor(uint32_t i,uint32_t value) {
    ((volatile uint32_t *)0x10010000u)[i]=value;
}
void N2D_CaptureCommit(void) { *(volatile uint32_t *)0x20001000u=1; }
uint32_t N2D_CaptureTime(void) { return DG_GetTicksMs(); }
void c_entry(void) {
    N2D_ClockInit(); N2D_CaptureInit();
    for (uint32_t frame=1;;++frame) {
        /* Warm the request line before host mutations, without granting a scene. */
        N2D_CaptureScene(0);
        *completion=0xc0680001u;
        volatile uint8_t *fb=(volatile uint8_t *)0x10000000u;
        for(uint32_t y=0;y<200;++y)
            for(uint32_t x=0;x<320;++x)
                fb[y*320+x]=(uint8_t)(3*x+5*y+frame);
        *completion=0xc0680002u;
        for(uint32_t i=0;i<256;++i) colors[i]=(i*13u+frame)&4095u;
        N2D_CaptureUpload(colors);
        N2D_CaptureFrame();
        /* Show the frame, then make the back bank equal the display. */
        *(volatile uint32_t *)0x20001008u=1;
        while(*(volatile uint32_t *)0x20001008u&2u) {}
        for(uint32_t w=0;w<16000;++w)
            ((volatile uint32_t *)0x10000000u)[w]=((volatile uint32_t *)0x10020000u)[w];
        *completion=0xc0680003u;
        N2D_CaptureScene(1);
        *completion=0xc0680004u;
    }
}
