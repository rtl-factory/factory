/* SPDX-License-Identifier: GPL-2.0-or-later
 *
 * Copyright(C) 1993-1996 Id Software, Inc.
 * Copyright(C) 2005-2014 Simon Howard
 *
 * This program is free software; you can redistribute it and/or
 * modify it under the terms of the GNU General Public License
 * as published by the Free Software Foundation; either version 2
 * of the License, or (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
 * GNU General Public License for more details.
 *
 * See ../../../sw/third_party/DOOMGENERIC-LICENSE for the license text.
 * FixedMul and FixedDiv are adapted from doomgeneric/m_fixed.c, and
 * SlopeDiv from doomgeneric/tables.c, at DoomGeneric revision
 * dcb7a8dbc7a16ce3dda29382ac9aae9d77d21284 in the pinned vendored archive.
 * Local adaptation for issue #70: freestanding fixed-width types and iabs,
 * explicit narrowing casts, noinline attributes, and a deterministic test
 * driver with a signature store. The driver is project-owned verification.
 */

/*
 * Doom fixed-point arithmetic kernel for core IPC measurement.
 *
 * FixedMul, FixedDiv and SlopeDiv follow the Doom engine's definitions
 * (m_fixed.c, tables.c) so the compiler and libgcc emit the same RV32M
 * sequences as the Doom image: FixedMul is MUL plus MULH, FixedDiv divides
 * a 64-bit dividend through libgcc's __divdi3 (DIVU/REMU pairs on 16-bit
 * normalized divisors), and SlopeDiv is one 32-bit DIVU. The loop mirrors
 * the per-seg and per-span work of R_ScaleFromGlobalAngle, R_PointToAngle
 * and R_MapPlane over pseudo-random world distances and angles.
 */
#include <stdint.h>

typedef int32_t fixed_t;

enum { FRACBITS = 16, SLOPERANGE = 2048, SIGNATURE_ADDRESS = 0x80 };

static int32_t iabs(int32_t value) { return value < 0 ? -value : value; }

__attribute__((noinline)) fixed_t FixedMul(fixed_t a, fixed_t b) {
    return (fixed_t)(((int64_t)a * (int64_t)b) >> FRACBITS);
}

__attribute__((noinline)) fixed_t FixedDiv(fixed_t a, fixed_t b) {
    if ((iabs(a) >> 14) >= iabs(b)) return (a ^ b) < 0 ? INT32_MIN : INT32_MAX;
    return (fixed_t)(((int64_t)a << FRACBITS) / b);
}

__attribute__((noinline)) int32_t SlopeDiv(uint32_t num, uint32_t den) {
    uint32_t answer;
    if (den < 512) return SLOPERANGE;
    answer = (num << 3) / (den >> 8);
    return answer <= SLOPERANGE ? (int32_t)answer : SLOPERANGE;
}

static uint32_t next(uint32_t *seed) {
    *seed = *seed * 1664525u + 1013904223u;
    return *seed;
}

void c_entry(void) {
    const fixed_t projection = 160 << FRACBITS;
    uint32_t seed = 0x2D0Au;
    uint32_t signature = 0;

    for (int seg = 0; seg < 24; ++seg) {
        /* World distance 16..2063 map units, fine sines in (0, 1.0]. */
        fixed_t distance = (fixed_t)((next(&seed) >> 5) & 0x07FFFFFF) + (16 << FRACBITS);
        fixed_t sinea = (fixed_t)((next(&seed) >> 16) | 0x0400);
        fixed_t sineb = (fixed_t)((next(&seed) >> 16) | 0x0400);

        /* R_ScaleFromGlobalAngle */
        fixed_t num = FixedMul(projection, sineb);
        fixed_t den = FixedMul(distance, sinea);
        fixed_t scale = FixedDiv(num, den);

        /* R_PointToAngle octant slope */
        uint32_t dx = (uint32_t)distance;
        uint32_t dy = (uint32_t)FixedMul(distance, sinea);
        int32_t slope = SlopeDiv(dy, dx);

        /* R_MapPlane span setup */
        fixed_t length = FixedMul(FixedMul(distance, sineb), sinea);
        fixed_t xstep = FixedMul(distance, 0x0000C000);
        fixed_t ystep = FixedMul(distance, -0x00008000);

        signature += (uint32_t)scale ^ (uint32_t)slope ^ (uint32_t)length;
        signature += (uint32_t)(xstep - ystep);
    }
    *(volatile uint32_t *)SIGNATURE_ADDRESS = signature;
}
