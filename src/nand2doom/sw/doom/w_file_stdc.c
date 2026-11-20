/* Fixed-memory WAD backend for GPL-2.0-or-later doomgeneric. */
#include <stddef.h>
#include <string.h>

#include "w_file.h"

enum { WAD_ADDRESS = 0x02000000u, WAD_LENGTH = 28795076u };

static wad_file_t wad;
static wad_file_class_t memory_wad_file;

static wad_file_t *open_file(char *path) {
    if (strcmp(path, "freedoom1.wad") != 0) return NULL;
    wad.file_class = &memory_wad_file;
    wad.mapped = (byte *)WAD_ADDRESS;
    wad.length = WAD_LENGTH;
    return &wad;
}

static void close_file(wad_file_t *file) { (void)file; }

static size_t read_file(wad_file_t *file, unsigned int offset,
                        void *buffer, size_t length) {
    if (offset > file->length) return 0;
    if (length > file->length - offset) length = file->length - offset;
    memcpy(buffer, file->mapped + offset, length);
    return length;
}

static wad_file_class_t memory_wad_file = { open_file, close_file, read_file };
wad_file_class_t stdc_wad_file = { open_file, close_file, read_file };
