/* nand2doom Set-2 scan code to DoomGeneric key table (GPL-2.0-or-later). */
#ifndef N2D_DOOM_KEYMAP_H
#define N2D_DOOM_KEYMAP_H

#include <stdint.h>

#include "doomkeys.h"

/*
 * Index: ten-bit input event {extended (bit 9), break (bit 8), scan code}
 * folded to {extended, scan code}; the break bit is the caller's press state.
 * Plain keys use the first 256 entries, E0-prefixed keys the second 256.
 * Zero means "no Doom key": the event is consumed and ignored.
 * Contract: wiki/src/nand2doom/sw/doom/spec.md "Doom keyboard map".
 */
#define N2D_KEY_E0 0x100u

static const unsigned char n2d_doom_keymap[512] = {
    /* Movement: extended arrows; plain keypad 8/2/4/6 (NumLock on) too. */
    [N2D_KEY_E0 | 0x75] = KEY_UPARROW,    [0x75] = KEY_UPARROW,
    [N2D_KEY_E0 | 0x72] = KEY_DOWNARROW,  [0x72] = KEY_DOWNARROW,
    [N2D_KEY_E0 | 0x6b] = KEY_LEFTARROW,  [0x6b] = KEY_LEFTARROW,
    [N2D_KEY_E0 | 0x74] = KEY_RIGHTARROW, [0x74] = KEY_RIGHTARROW,
    /* Actions and modifiers: left and right Ctrl/Alt, both Shifts. */
    [0x14] = KEY_FIRE,   [N2D_KEY_E0 | 0x14] = KEY_FIRE,
    [0x29] = KEY_USE,
    [0x12] = KEY_RSHIFT, [0x59] = KEY_RSHIFT, [N2D_KEY_E0 | 0x12] = KEY_RSHIFT,
    [0x11] = KEY_LALT,   [N2D_KEY_E0 | 0x11] = KEY_LALT,
    [0x5a] = KEY_ENTER,  [N2D_KEY_E0 | 0x5a] = KEY_ENTER,
    [0x76] = KEY_ESCAPE,
    [0x0d] = KEY_TAB,          /* automap */
    [0x66] = KEY_BACKSPACE,    /* menu back */
    [0x4e] = KEY_MINUS,        /* automap zoom out, smaller view */
    [0x55] = KEY_EQUALS,       /* automap zoom in, larger view */
    /* Digits: weapons 1-7 (8 for Doom II), 0 automap max zoom. */
    [0x16] = '1', [0x1e] = '2', [0x26] = '3', [0x25] = '4', [0x2e] = '5',
    [0x36] = '6', [0x3d] = '7', [0x3e] = '8', [0x46] = '9', [0x45] = '0',
    /* Letters: Y/N prompts, automap f/g/m/c, cheats, save-name entry. */
    [0x1c] = 'a', [0x32] = 'b', [0x21] = 'c', [0x23] = 'd', [0x24] = 'e',
    [0x2b] = 'f', [0x34] = 'g', [0x33] = 'h', [0x43] = 'i', [0x3b] = 'j',
    [0x42] = 'k', [0x4b] = 'l', [0x3a] = 'm', [0x31] = 'n', [0x44] = 'o',
    [0x4d] = 'p', [0x15] = 'q', [0x2d] = 'r', [0x1b] = 's', [0x2c] = 't',
    [0x3c] = 'u', [0x2a] = 'v', [0x1d] = 'w', [0x22] = 'x', [0x35] = 'y',
    [0x1a] = 'z',
};

static inline unsigned char n2d_doom_key(uint32_t event) {
    return n2d_doom_keymap[((event >> 1) & N2D_KEY_E0) | (event & 0xffu)];
}

#endif
