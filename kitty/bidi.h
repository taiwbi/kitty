/* License: GPLv3 Copyright: 2026 */
#pragma once

#include "line.h"
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wpedantic"
#include <fribidi.h>
#include <hb.h>
#pragma GCC diagnostic pop

typedef struct {
    index_type x, width, visual_x;
    unsigned first, count;
    FriBidiLevel level;
    hb_script_t script;
} BidiCell;

typedef struct {
    char_type *text;
    unsigned *owners;
    FriBidiLevel *levels;
    BidiCell *cells;
    unsigned count, num_cells;
} BidiLayout;

#define BIDI_CACHED_ROWS 32u
#define BIDI_ROW_CACHE_BYTES (4u * 1024u * 1024u)
typedef struct {
    uint8_t *key;
    size_t key_size;
    uint64_t hash, age;
    GPUCell *cells;
} BidiCachedRow;

bool line_needs_bidi(const Line *line);
bool bidi_layout(const Line *line, BidiLayout *layout);
void free_bidi_layout(BidiLayout *layout);
