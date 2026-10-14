/* License: GPLv3 Copyright: 2026 */
#include "bidi.h"
#include "arabic-presentation-forms.h"
#include "charsets.h"

bool
line_needs_bidi(const Line *line) {
    bool needs_bidi = false;
    if (line->attrs.has_image_placeholders) return false;
    RAII_ListOfChars(lc);
    for (index_type x = 0; x < line->xnum; x++) {
        const CPUCell *cell = line->cpu_cells + x;
        // Normal wide characters are supported; scaled and multi-row cells are not.
        if (cell->is_multicell) {
            if (cell->scale != 1 || cell->subscale_n || cell->subscale_d || cell->y) return false;
            if (cell->x) continue;
        }
        text_in_cell(cell, line->text_cache, &lc);
        for (size_t i = 0; i < lc.count; i++) {
            if (lc.chars[i] == IMAGE_PLACEHOLDER_CHAR) return false;
            const FriBidiCharType type = fribidi_get_bidi_type(lc.chars[i]);
            if (FRIBIDI_IS_RTL(type) || FRIBIDI_IS_EXPLICIT(type) || FRIBIDI_IS_ISOLATE(type)) needs_bidi = true;
        }
    }
    return needs_bidi;
}

static unsigned
presentation_form(char_type cp, const char_type **text) {
    size_t left = 0, right = arraysz(arabic_presentation_forms);
    while (left < right) {
        const size_t mid = left + (right - left) / 2;
        if (arabic_presentation_forms[mid].cp < cp) left = mid + 1;
        else right = mid;
    }
    if (left < arraysz(arabic_presentation_forms) && arabic_presentation_forms[left].cp == cp) {
        *text = arabic_presentation_text + arabic_presentation_forms[left].offset;
        return arabic_presentation_forms[left].count;
    }
    return 0;
}

void
free_bidi_layout(BidiLayout *layout) {
    free(layout->text);
    free(layout->owners);
    free(layout->levels);
    free(layout->cells);
    *layout = (BidiLayout){0};
}

bool
bidi_layout(const Line *line, BidiLayout *layout) {
    *layout = (BidiLayout){0};
    if (!line_needs_bidi(line)) return false;
    // Bounds also keep glyph/cache span sizes representable in the renderer.
    if (!line->xnum || line->xnum > UINT16_MAX) return false;
    layout->cells = calloc(line->xnum, sizeof(BidiCell));
    if (!layout->cells) return false;
    size_t capacity = 0;
    RAII_ListOfChars(lc);
    for (index_type x = 0; x < line->xnum;) {
        const CPUCell *cpu = line->cpu_cells + x;
        const unsigned owner = layout->num_cells++;
        BidiCell *cell = layout->cells + owner;
        cell->x = x;
        cell->width = cpu->is_multicell ? cpu->width : 1;
        cell->first = layout->count;
        if (!cell->width || x + cell->width > line->xnum) goto fail;
        text_in_cell(cpu, line->text_cache, &lc);
        for (size_t j = 0; j < lc.count; j++) {
            char_type cp = lc.chars[j] ? lc.chars[j] : ' ';
            const char_type *text = &cp;
            unsigned n = presentation_form(cp, &text);
            if (!n) n = 1;
            if (layout->count + n > capacity) {
                capacity = MAX(capacity * 2, layout->count + n + 128u);
                char_type *new_text = realloc(layout->text, capacity * sizeof(char_type));
                if (!new_text) goto fail;
                layout->text = new_text;
                unsigned *new_owners = realloc(layout->owners, capacity * sizeof(unsigned));
                if (!new_owners) goto fail;
                layout->owners = new_owners;
            }
            memcpy(layout->text + layout->count, text, n * sizeof(char_type));
            for (unsigned k = 0; k < n; k++) layout->owners[layout->count++] = owner;
        }
        cell->count = layout->count - cell->first;
        x += cell->width;
    }
    const unsigned n = layout->count;
    FriBidiCharType *types = malloc(n * sizeof(*types));
    FriBidiBracketType *brackets = malloc(n * sizeof(*brackets));
    FriBidiStrIndex *order = malloc(n * sizeof(*order));
    bool *seen = calloc(layout->num_cells, sizeof(*seen));
    layout->levels = malloc(n * sizeof(*layout->levels));
    bool ok = false;
    if (!types || !brackets || !order || !seen || !layout->levels) goto cleanup;
    fribidi_get_bidi_types(layout->text, n, types);
    fribidi_get_bracket_types(layout->text, n, types, brackets);
    FriBidiParType base = FRIBIDI_PAR_LTR;
    if (!fribidi_get_par_embedding_levels_ex(types, brackets, n, &base, layout->levels)) goto cleanup;
    for (unsigned i = 0; i < n; i++) order[i] = i;
    // HarfBuzz handles glyph mirroring. Only request the ordering map here.
    if (!fribidi_reorder_line(0, types, n, 0, base, layout->levels, NULL, order)) goto cleanup;
    index_type visual_x = 0;
    for (unsigned i = 0; i < n; i++) {
        const unsigned owner = layout->owners[order[i]];
        if (!seen[owner]) {
            seen[owner] = true;
            layout->cells[owner].visual_x = visual_x;
            visual_x += layout->cells[owner].width;
        }
    }
    if (visual_x != line->xnum) goto cleanup;
    hb_unicode_funcs_t *unicode = hb_unicode_funcs_get_default();
    for (unsigned i = 0; i < layout->num_cells; i++) {
        BidiCell *cell = layout->cells + i;
        cell->level = layout->levels[cell->first];
        cell->script = HB_SCRIPT_COMMON;
        for (unsigned j = cell->first; j < cell->first + cell->count; j++) {
            hb_script_t script = hb_unicode_script(unicode, layout->text[j]);
            if (script != HB_SCRIPT_COMMON && script != HB_SCRIPT_INHERITED && script != HB_SCRIPT_UNKNOWN) {
                cell->script = script;
                break;
            }
        }
    }
    // Common punctuation/spacing inherits a neighboring script in the same
    // embedding level. Direction and font boundaries still delimit runs.
    for (unsigned i = 0; i < layout->num_cells; i++) {
        BidiCell *cell = layout->cells + i;
        if (cell->script != HB_SCRIPT_COMMON) continue;
        if (i && layout->cells[i - 1].level == cell->level) cell->script = layout->cells[i - 1].script;
    }
    for (unsigned i = layout->num_cells; i > 1; i--) {
        BidiCell *cell = layout->cells + i - 2;
        const BidiCell *next = cell + 1;
        if (cell->script == HB_SCRIPT_COMMON && cell->level == next->level) cell->script = next->script;
    }
    ok = true;
cleanup:
    free(types);
    free(brackets);
    free(order);
    free(seen);
    if (ok) return true;
fail:
    free_bidi_layout(layout);
    return false;
}

index_type
bidi_logical_to_visual_x(const Line *line, index_type logical_x) {
    if (logical_x >= line->xnum) return line->xnum;
    BidiLayout layout;
    if (!bidi_layout(line, &layout)) return MIN(logical_x, line->xnum);
    index_type ans = logical_x;
    for (unsigned i = 0; i < layout.num_cells; i++) {
        const BidiCell *cell = layout.cells + i;
        if (logical_x >= cell->x && logical_x < cell->x + cell->width) {
            ans = cell->visual_x;
            break;
        }
    }
    free_bidi_layout(&layout);
    return ans;
}

bool
bidi_visual_to_logical_x(const Line *line, index_type visual_x, bool visual_left_half, index_type *logical_x, bool *logical_left_half) {
    BidiLayout layout;
    if (!bidi_layout(line, &layout)) return false;
    bool found = false;
    for (unsigned i = 0; i < layout.num_cells; i++) {
        const BidiCell *cell = layout.cells + i;
        if (visual_x >= cell->visual_x && visual_x < cell->visual_x + cell->width) {
            *logical_x = cell->x + (visual_x - cell->visual_x);
            *logical_left_half = (visual_left_half != FRIBIDI_LEVEL_IS_RTL(cell->level));
            found = true;
            break;
        }
    }
    free_bidi_layout(&layout);
    return found;
}
