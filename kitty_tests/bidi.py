#!/usr/bin/env python
# License: GPLv3 Copyright: 2026

from kitty.fast_data_types import Screen, sprite_idx_to_pos, test_bidi_coordinates, test_bidi_layout, test_render_line, test_render_screen
from kitty.fonts.render import setup_for_testing

from .base import BaseTest, draw_multicell, parse_bytes
from .fonts import FontBaseTest


class BidiLayout(BaseTest):
    def layout(self, text):
        screen = Screen(None, 1, max(40, len(text) * 2))
        screen.draw(text)
        return screen, test_bidi_layout(screen.line(0))

    def visual(self, text):
        screen, result = self.layout(text)
        self.assertIsNotNone(result)
        normalized, cells = result
        chunks = [normalized[first : first + count] for x, width, visual_x, first, count, level, script in sorted(cells, key=lambda c: c[2])]
        self.ae(str(screen.line(0)).rstrip(), text)
        return ''.join(chunks).rstrip()

    def test_bidi_word_order(self):
        # Expected strings are written in visual (left-to-right cell) order.
        self.ae(self.visual('hello سلام دنیا end'), 'hello ایند مالس end')
        self.ae(self.visual('سلام دنیا hello'), 'ایند مالس hello')
        self.ae(self.visual('a العربية b'), 'a ةيبرعلا b')
        self.ae(self.visual('abc אבג דה xyz'), 'abc הד גבא xyz')

    def test_bidi_numbers_and_brackets(self):
        self.ae(self.visual('value = "سلام دنیا 123";'), 'value = "123 ایند مالس";')
        self.ae(self.visual('a سلام ۱۲۳ b'), 'a ۱۲۳ مالس b')
        self.ae(self.visual('a (سلام دنیا) b'), 'a (ایند مالس) b')

    def test_bidi_graphemes_and_wide_cells(self):
        self.ae(self.visual('a سَلام b'), 'a مالسَ b')
        self.ae(self.visual('a می\u200cروم b'), 'a موری\u200cم b')
        screen, result = self.layout('a سلام 😀界 b')
        normalized, cells = result
        self.ae(sum(cell[1] for cell in cells), screen.columns)
        positions = []
        for x, width, visual_x, first, count, level, script in cells:
            positions.extend(range(visual_x, visual_x + width))
            if normalized[first : first + count] in ('😀', '界'):
                self.ae(width, 2)
        self.ae(sorted(positions), list(range(screen.columns)))

    def test_bidi_coordinate_mapping(self):
        for text in ('سلام', 'abc سلام 123', 'a 😀 אבג'):
            screen, result = self.layout(text)
            normalized, cells = result
            for x, width, visual_x, first, count, level, script in cells:
                visual, logical, left = test_bidi_coordinates(screen.line(0), x, visual_x, True)
                self.ae(visual, visual_x)
                self.ae(logical, x)
                self.ae(left, not bool(level & 1))
                if width > 1:
                    visual, logical, left = test_bidi_coordinates(screen.line(0), x, visual_x + width - 1, False)
                    self.ae(logical, x + width - 1)
                    self.ae(left, bool(level & 1))

    def test_bidi_presentation_forms(self):
        original = 'hello ﺱﻼﻣ ﺪﻨﯾﺍ end'
        screen, result = self.layout(original)
        normalized, cells = result
        self.ae(normalized.rstrip(), 'hello سلام دنیا end')
        lam_alef = next(cell for cell in cells if normalized[cell[3] : cell[3] + cell[4]] == 'لا')
        self.ae(lam_alef[1], 1)
        self.ae(lam_alef[4], 2)
        self.ae(str(screen.line(0)).rstrip(), original)

    def test_bidi_fallback_rows(self):
        self.assertIsNone(self.layout('plain ASCII -> 123')[1])
        screen = Screen(None, 2, 40)
        draw_multicell(screen, 'سلام', scale=2, width=4)
        self.assertIsNone(test_bidi_layout(screen.line(0)))
        self.assertIsNone(self.layout('سلام \U0010eeee')[1])


class BidiRendering(FontBaseTest):
    def draw(self, text):
        screen = Screen(None, 1, 60)
        parse_bytes(screen, text.encode())
        return screen, screen.line(0)

    def pixels(self, rendered):
        return [self.sprites[sprite_idx_to_pos(cell[0] & 0x7FFFFFFF, setup_for_testing.xnum, setup_for_testing.ynum)] for cell in rendered]

    def test_bidi_visual_row_and_cache(self):
        self.set_options({'bidi_rendering': True})
        screen, line = self.draw('a \u05d0\u05d1\u05d2 b')
        original = str(line)
        result = test_render_line(line, True)
        logical_sprites = [line.sprite_at(x) for x in range(screen.columns)]
        self.assertNotEqual([cell[0] for cell in result], logical_sprites)
        self.ae(str(line), original)
        count = len(self.sprites)
        self.ae(test_render_line(line, True), result)
        self.ae(len(self.sprites), count)
        self.ae([line.sprite_at(x) for x in range(screen.columns)], logical_sprites)
        self.set_options({'bidi_rendering': True, 'force_ltr': True})
        bypass = test_render_line(line, True)
        self.ae([cell[0] for cell in bypass], [line.sprite_at(x) for x in range(screen.columns)])

    def test_bidi_colors_and_redraw(self):
        self.set_options({'bidi_rendering': True})
        screen, line = self.draw('a \x1b[31;44mאב\x1b[32;45mג\x1b[0m b')
        result = test_render_line(line, True)
        normalized, cells = test_bidi_layout(line)
        for logical_x, width, visual_x, first, count, level, script in cells:
            self.ae(result[visual_x][1], line.cursor_from(logical_x).fg)
            self.ae(result[visual_x][2], line.cursor_from(logical_x).bg)
        screen.cursor.x = 2
        screen.draw('ד')
        changed = test_render_line(line, True)
        self.assertNotEqual(result, changed)

    def test_bidi_disabled_and_ascii(self):
        for options in ({}, {'bidi_rendering': True}, {'bidi_rendering': True, 'force_ltr': True}):
            self.set_options(options)
            screen, line = self.draw('abc -> 123')
            result = test_render_line(line, True)
            self.ae([cell[0] for cell in result], [line.sprite_at(x) for x in range(screen.columns)])
        self.set_options({})
        screen, line = self.draw('a سلام b')
        result = test_render_line(line, True)
        self.ae([cell[0] for cell in result], [line.sprite_at(x) for x in range(screen.columns)])

    def test_bidi_arabic_reshaping(self):
        self.set_options({'bidi_rendering': True})
        # These Neovim forms are in logical order but shaped for an LTR grid.
        # Without lam-alef compression, reshaping must match the base letters.
        _, base = self.draw('a دنیا b')
        base_pixels = self.pixels(test_render_line(base, True))
        _, shaped = self.draw('a ﺪﻨﯾﺍ b')
        self.ae(self.pixels(test_render_line(shaped, True)), base_pixels)
        _, compressed = self.draw('a ﺱﻼﻣ b')
        before = str(compressed)
        result = test_render_line(compressed, True)
        self.ae(len(result), len(compressed))
        self.ae(str(compressed), before)
        self.assertTrue(any(any(self.pixels(result)[x]) for x in range(2, 5)))

    def test_bidi_cache_eviction(self):
        self.set_options({'bidi_rendering': True})
        screen, line = self.draw('first אבג')
        first = test_render_line(line, True)
        for i in range(40):
            other, other_line = self.draw(f'row {i} אבג')
            test_render_line(other_line, True)
        self.ae(test_render_line(line, True), first)

    def test_bidi_viewport_updates(self):
        self.set_options({'bidi_rendering': True, 'pixel_scroll': False})
        screen = Screen(None, 2, 30, 20)
        parse_bytes(screen, 'a سلام b\r\nc دنیا d\r\nnext'.encode())
        rendered = test_render_screen(screen)
        self.ae(rendered[0], test_render_line(screen.line(0), True))
        screen.scroll(1, True)
        rendered = test_render_screen(screen)
        self.ae(rendered[0], test_render_line(screen.historybuf.line(0), True))
        screen.scroll(1000, False)
        screen.resize(2, 40)
        rendered = test_render_screen(screen)
        self.ae(len(rendered[0]), 40)
        self.ae(rendered[0], test_render_line(screen.line(0), True))
        parse_bytes(screen, b'\x1b[?1049h')
        screen.draw('a العربية b')
        self.ae(test_render_screen(screen)[0], test_render_line(screen.line(0), True))

    def test_bidi_paused_rendering(self):
        self.set_options({'bidi_rendering': True, 'pixel_scroll': False})
        screen, line = self.draw('a سلام b')
        before = test_render_screen(screen)
        self.assertTrue(screen.pause_rendering(True, 5000))
        screen.cursor.x = 2
        screen.draw('دنیا')
        self.ae(test_render_screen(screen), before)
        screen.pause_rendering(False)
        self.assertNotEqual(test_render_screen(screen), before)

    def test_bidi_mirrored_brackets(self):
        self.set_options({'bidi_rendering': True})
        screen, line = self.draw('a אבג (דה) b')
        rendered = self.pixels(test_render_line(line, True))
        text, cells = test_bidi_layout(line)
        mirrored = [cell for cell in cells if text[cell[3] : cell[3] + cell[4]] == ')' and cell[5] % 2]
        self.ae(len(mirrored), 1)
        other, bracket = self.draw('(')
        expected = self.pixels(test_render_line(bracket, True))[0]
        self.ae(rendered[mirrored[0][2]], expected)

    def test_bidi_pixel_scroll_viewport(self):
        self.set_options({'bidi_rendering': True, 'pixel_scroll': True})
        screen = Screen(None, 2, 30, 20)
        screen.draw('a سلام b')
        rendered = test_render_screen(screen)
        self.ae(len(rendered), screen.lines + 1)
        self.ae(rendered[1], test_render_line(screen.line(0), True))

    def test_bidi_private_use_and_emoji_sprites(self):
        self.set_options({'bidi_rendering': True})
        screen, line = self.draw('a \uf013  سلام 😀 b')
        rendered = test_render_line(line, True)
        text, cells = test_bidi_layout(line)
        for x, width, visual_x, first, count, level, script in cells:
            if text[first : first + count] in ('\uf013', '😀'):
                self.ae([cell[0] for cell in rendered[visual_x : visual_x + width]], [line.sprite_at(i) for i in range(x, x + width)])
