/* License: GPLv3 Copyright: 2026
 * Included by fonts.c so this renderer shares font selection and sprite upload
 * with the normal cell renderer. It never modifies the logical line.
 */

static pixel
bidi_sample(const pixel *row, unsigned width, double x) {
    if (x < 0 || x > width - 1u) return 0;
    unsigned left = (unsigned)x, right = MIN(left + 1u, width - 1u);
    unsigned weight = (unsigned)((x - left) * 256);
    pixel ans = 0;
    for (unsigned shift = 0; shift < 32; shift += 8) {
        unsigned a = (row[left] >> shift) & 0xff, b = (row[right] >> shift) & 0xff;
        ans |= ((a * (256 - weight) + b * weight + 128) >> 8) << shift;
    }
    return ans;
}

static bool
render_bidi_glyph_group(
    FontGroup *fg, Font *font, hb_glyph_info_t *info, hb_glyph_position_t *positions, unsigned num_glyphs, unsigned num_cells, GPUCell *dest) {
    const FontCellMetrics metrics = fg->fcm;
    const unsigned cw = metrics.cell_width, ch = metrics.cell_height;
    const unsigned padding = font->italic ? fg->sprite_padding : 0;
    // Cache positions as well as glyph IDs: contextual positioning can differ
    // even when the resulting glyph sequence is identical. A separate table
    // prevents collisions with the existing renderer's glyph-only keys.
    const size_t key_bytes = num_glyphs * (sizeof(glyph_index) + 4 * sizeof(int32_t));
    if (!num_glyphs || key_bytes + 12u > UINT16_MAX) return false;
    const unsigned key_count = (unsigned)(key_bytes / sizeof(glyph_index));
    ensure_glyph_render_scratch_space(MAX(key_count, num_cells));
    glyph_index *key = global_glyph_render_scratch.glyphs;
    uint8_t *p = (uint8_t *)key;
    for (unsigned i = 0; i < num_glyphs; i++) {
        glyph_index glyph = info[i].codepoint;
        memcpy(p, &glyph, sizeof(glyph));
        p += sizeof(glyph);
        const int32_t pos[] = {positions[i].x_advance, positions[i].y_advance, positions[i].x_offset, positions[i].y_offset};
        memcpy(p, pos, sizeof(pos));
        p += sizeof(pos);
    }
    // CoreText uses font units; FreeType uses 26.6 pixel units and rounds each
    // advance. Measure using the same conversion as the font's rasterizer.
    const double advance = glyph_run_advance(font->face, positions, num_glyphs);
    if (advance < 0 || advance > UINT_MAX) return false;
    const unsigned native_width = advance != 0.0 ? (unsigned)advance : num_cells * cw;
    const unsigned native_cells = MAX(1u, (native_width + cw - 1u) / cw);
    SpritePosition **sprites = global_glyph_render_scratch.sprite_positions;
    bool all_rendered = true;
    for (unsigned i = 0; i < num_cells; i++) {
        bool created;
        sprites[i] = find_or_create_sprite_position(font->bidi_sprite_position_hash_table, key, key_count, i, num_cells, 1, 0, 0, 0, &created);
        if (!sprites[i]) return false;
        if (!sprites[i]->rendered) all_rendered = false;
    }
    if (!all_rendered) {
        ensure_canvas_can_fit(fg, native_cells + 2, 1);
        const unsigned stride = native_cells * cw + 2 * padding;
        bool colored = false;
        GlyphRenderInfo ri = {0};
        if (!render_glyphs_in_cells(
                font->face,
                font->bold,
                font->italic,
                info,
                positions,
                num_glyphs,
                fg->canvas.buf,
                cw,
                ch,
                native_cells,
                metrics.baseline,
                padding,
                &colored,
                (FONTS_DATA_HANDLE)fg,
                &ri))
            return false;
        const RunFont rf = {.scale = 1, .font_idx = font - fg->fonts};
        const Region region = {.right = cw, .bottom = ch};
        const DecorationMetadata dm = index_for_decorations(fg, rf, region, region, metrics);
        const unsigned out_width = cw + 2 * padding;
        pixel *cell = calloc((size_t)out_width * (ch + 1u), sizeof(pixel));
        if (!cell) return false;
        const double ratio = (double)native_width / (num_cells * cw);
        for (unsigned i = 0; i < num_cells; i++) {
            if (sprites[i]->rendered) continue;
            memset(cell, 0, (size_t)out_width * (ch + 1u) * sizeof(pixel));
            for (unsigned y = 0; y < ch; y++) {
                const pixel *src = fg->canvas.buf + y * stride;
                for (unsigned x = 0; x < out_width; x++) {
                    const double sx = ((double)i * cw + x - padding + .5) * ratio - .5 + padding;
                    cell[y * out_width + x] = bidi_sample(src, stride, sx);
                }
            }
            sprites[i]->idx = padding ? current_send_padded_sprite_to_gpu(fg, cell, dm, metrics) : current_send_sprite_to_gpu(fg, cell, dm, metrics);
            if (!sprites[i]->idx) {
                free(cell);
                return false;
            }
            sprites[i]->colored = colored;
            sprites[i]->rendered = true;
        }
        free(cell);
    }
    for (unsigned i = 0; i < num_cells; i++) set_cell_sprite(dest + i, sprites[i]);
    return true;
}

static bool
render_bidi_run(FontGroup *fg, const BidiLayout *layout, unsigned first_cell, unsigned end_cell, ssize_t font_idx, bool disable_ligature, GPUCell *output) {
    Font *font = fg->fonts + font_idx;
    const BidiCell *first = layout->cells + first_cell, *last = layout->cells + end_cell - 1;
    const unsigned start = first->first, end = last->first + last->count;
    const bool rtl = FRIBIDI_LEVEL_IS_RTL(first->level);
    hb_buffer_clear_contents(harfbuzz_buffer);
    // Supplying the entire logical row preserves joining context across style
    // and font boundaries. Only the requested run is added to the buffer.
    hb_buffer_add_codepoints(harfbuzz_buffer, layout->text, layout->count, start, end - start);
    hb_buffer_set_direction(harfbuzz_buffer, rtl ? HB_DIRECTION_RTL : HB_DIRECTION_LTR);
    hb_buffer_set_script(harfbuzz_buffer, first->script);
    hb_buffer_set_language(harfbuzz_buffer, hb_language_get_default());
    size_t num_features = font->num_ffs_hb_features;
    if (num_features && !disable_ligature) num_features--;
    hb_shape(harfbuzz_font_for_face(font->face), harfbuzz_buffer, font->ffs_hb_features, num_features);
    unsigned n = 0;
    hb_glyph_info_t *info = hb_buffer_get_glyph_infos(harfbuzz_buffer, &n);
    hb_glyph_position_t *positions = hb_buffer_get_glyph_positions(harfbuzz_buffer, NULL);
    if (!hb_buffer_allocation_successful(harfbuzz_buffer) || !info || !positions || !n) return false;
    for (unsigned i = 0; i < n;) {
        const unsigned group_start = i;
        unsigned lo = UINT_MAX, hi = 0;
        for (;;) {
            unsigned next = i + 1;
            while (next < n && info[next].cluster == info[i].cluster) next++;
            const unsigned a = info[i].cluster;
            const unsigned b = rtl ? (i ? info[i - 1].cluster : end) : (next < n ? info[next].cluster : end);
            if (a < start || b > end || a >= b) return false;
            const unsigned owner_lo = layout->owners[a], owner_hi = layout->owners[b - 1];
            // Several HarfBuzz clusters can share one source cell after
            // decomposing a presentation-form ligature. Rasterize them together
            // and fit the complete result into that original cell span.
            if (i > group_start && (owner_hi < lo || owner_lo > hi)) break;
            lo = MIN(lo, owner_lo);
            hi = MAX(hi, owner_hi);
            i = next;
            if (i == n) break;
        }
        unsigned visual_x = UINT_MAX, width = 0, right = 0;
        for (unsigned owner = lo; owner <= hi; owner++) {
            const BidiCell *cell = layout->cells + owner;
            visual_x = MIN(visual_x, cell->visual_x);
            right = MAX(right, cell->visual_x + cell->width);
            width += cell->width;
        }
        if (visual_x + width != right) return false;
        if (!render_bidi_glyph_group(fg, font, info + group_start, positions + group_start, i - group_start, width, output + visual_x)) return false;
    }
    return true;
}

static bool
render_bidi_row(FontGroup *fg, const Line *line, const BidiLayout *layout, DisableLigature strategy, int cursor_x, GPUCell *output) {
    RAII_ListOfChars(lc);
    ssize_t *fonts = malloc(layout->num_cells * sizeof(*fonts));
    TextCache *tc = tc_alloc();
    bool ok = false;
    if (!fonts || !tc) goto cleanup;
    for (unsigned i = 0; i < layout->num_cells; i++) {
        const BidiCell *cell = layout->cells + i;
        CPUCell normalized = line->cpu_cells[cell->x];
        ListOfChars text = {.chars = layout->text + cell->first, .count = cell->count, .capacity = cell->count};
        cell_set_chars(&normalized, tc, &text);
        bool main_font, emoji;
        fonts[i] = font_for_cell(fg, &normalized, line->gpu_cells + cell->x, &main_font, &emoji, tc, &lc);
        // Keep color emoji and private-use icons, whose existing sprites can
        // occupy adjacent blank cells, in addition to special/blank sprites.
        if (emoji || is_private_use(char_props_for(lc.chars[0]))) fonts[i] = NO_FONT;
        memcpy(output + cell->visual_x, line->gpu_cells + cell->x, cell->width * sizeof(GPUCell));
    }
    for (unsigned i = 0; i < layout->num_cells;) {
        const unsigned start = i++;
        const BidiCell *cell = layout->cells + start;
        if (fonts[start] <= BOX_FONT) continue;
        while (i < layout->num_cells && fonts[i] == fonts[start] && layout->cells[i].level == cell->level && layout->cells[i].script == cell->script) i++;
        const BidiCell *last = layout->cells + i - 1;
        const bool disable = strategy == DISABLE_LIGATURES_ALWAYS ||
                             (strategy == DISABLE_LIGATURES_CURSOR && cursor_x >= (int)cell->x && cursor_x < (int)(last->x + last->width));
        if (!render_bidi_run(fg, layout, start, i, fonts[start], disable, output)) goto cleanup;
    }
    ok = true;
cleanup:
    free(fonts);
    if (tc) tc_decref(tc);
    return ok;
}

static void
evict_bidi_row(FontGroup *fg, BidiCachedRow *row) {
    // Cache entries may have different widths, so recover their stored size
    // from the first key word rather than the current line.
    if (!row->key) return;
    uint32_t columns;
    memcpy(&columns, row->key, sizeof(columns));
    fg->bidi_cache_bytes -= row->key_size + columns * sizeof(GPUCell);
    free(row->key);
    free(row->cells);
    *row = (BidiCachedRow){0};
}

const GPUCell *
render_visual_line(FONTS_DATA_HANDLE handle, Line *line, DisableLigature strategy, int cursor_x) {
    if (!OPT(bidi_rendering) || OPT(force_ltr)) return line->gpu_cells;
    BidiLayout layout;
    if (!bidi_layout(line, &layout)) return line->gpu_cells;
    FontGroup *fg = (FontGroup *)handle;
    const uint32_t header[] = {line->xnum, layout.count, layout.num_cells, strategy, strategy == DISABLE_LIGATURES_CURSOR ? (uint32_t)cursor_x : UINT32_MAX};
    const size_t cells_size = line->xnum * sizeof(GPUCell);
    const size_t key_size =
        sizeof(header) + line->xnum * (sizeof(CPUCell) + sizeof(GPUCell)) + layout.count * sizeof(char_type) + layout.num_cells * sizeof(BidiCell);
    uint8_t *key = NULL;
    GPUCell *output = NULL;
    const GPUCell *ans = line->gpu_cells;
    if (key_size + cells_size > BIDI_ROW_CACHE_BYTES) goto cleanup;
    key = malloc(key_size);
    if (!key) goto cleanup;
    uint8_t *p = key;
#define append_key(data, sz) \
    do {                     \
        memcpy(p, data, sz); \
        p += sz;             \
    } while (0)
    append_key(header, sizeof(header));
    append_key(line->cpu_cells, line->xnum * sizeof(CPUCell));
    append_key(line->gpu_cells, cells_size);
    append_key(layout.text, layout.count * sizeof(char_type));
    append_key(layout.cells, layout.num_cells * sizeof(BidiCell));
#undef append_key
    const uint64_t hash = vt_hash_bytes(key, key_size);
    unsigned victim = 0;
    for (unsigned i = 0; i < BIDI_CACHED_ROWS; i++) {
        BidiCachedRow *row = fg->bidi_rows + i;
        if (row->key && row->hash == hash && row->key_size == key_size && memcmp(row->key, key, key_size) == 0) {
            row->age = ++fg->bidi_cache_clock;
            ans = row->cells;
            goto cleanup;
        }
        if (row->age < fg->bidi_rows[victim].age) victim = i;
    }
    output = malloc(cells_size);
    if (!output || !render_bidi_row(fg, line, &layout, strategy, cursor_x, output)) goto cleanup;
    evict_bidi_row(fg, fg->bidi_rows + victim);
    while (fg->bidi_cache_bytes + key_size + cells_size > BIDI_ROW_CACHE_BYTES) {
        unsigned oldest = 0;
        for (unsigned i = 1; i < BIDI_CACHED_ROWS; i++)
            if (fg->bidi_rows[i].key && (!fg->bidi_rows[oldest].key || fg->bidi_rows[i].age < fg->bidi_rows[oldest].age)) oldest = i;
        evict_bidi_row(fg, fg->bidi_rows + oldest);
    }
    fg->bidi_rows[victim] = (BidiCachedRow){.key = key, .key_size = key_size, .hash = hash, .age = ++fg->bidi_cache_clock, .cells = output};
    fg->bidi_cache_bytes += key_size + cells_size;
    ans = output;
    key = NULL;
    output = NULL;
cleanup:
    free(key);
    free(output);
    free_bidi_layout(&layout);
    if (PyErr_Occurred()) PyErr_Print();
    return ans;
}
