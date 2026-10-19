"""design.py: the design system the composer draws from, derived from the
documents rather than typed into the composer."""

import unittest

from ytstudio import design


PALETTE_PROSE = ("Fundo azul-ardósia escuro (#14213D), portas em creme (#F1E9D2), "
                 "seleção do jogador em azul (#3A86FF), 'carro/ganhar' em verde "
                 "(#2EC4B6), 'bode/perder' em coral (#EF476F), texto de apoio em "
                 "cinza-azulado (#8FA3C8)")


class PaletteFromProse(unittest.TestCase):
    def test_every_hex_is_found_with_its_label(self):
        swatches = design.parse_palette(PALETTE_PROSE)
        self.assertEqual([s.hex for s in swatches],
                         ["#14213D", "#F1E9D2", "#3A86FF", "#2EC4B6", "#EF476F", "#8FA3C8"])
        self.assertIn("creme", swatches[1].label.lower())

    def test_roles_are_assigned_from_the_words_around_each_hex(self):
        roles = design.roles(design.parse_palette(PALETTE_PROSE))
        self.assertEqual(roles["background"], "#14213D")
        self.assertEqual(roles["object"], "#F1E9D2")
        self.assertEqual(roles["choice"], "#3A86FF")
        self.assertEqual(roles["positive"], "#2EC4B6")
        self.assertEqual(roles["negative"], "#EF476F")
        self.assertEqual(roles["muted"], "#8FA3C8")

    def test_structured_palette_is_accepted_too(self):
        swatches = design.parse_palette([{"hex": "#101010", "use": "background"},
                                         {"hex": "#EEEEEE", "use": "text"}])
        self.assertEqual(design.roles(swatches)["background"], "#101010")
        self.assertEqual(design.roles(swatches)["ink"], "#EEEEEE")

    def test_roles_fall_back_by_position_when_words_say_nothing(self):
        roles = design.roles(design.parse_palette("#000000, #FFFFFF, #FF0000"))
        self.assertEqual(roles["background"], "#000000")
        self.assertEqual(roles["object"], "#FFFFFF")
        self.assertTrue(roles["ink"])          # always something to write with

    def test_lowercase_and_eight_digit_hexes_are_normalised(self):
        swatches = design.parse_palette("fundo (#1a2b3c), véu (#ffffff80)")
        self.assertEqual(swatches[0].hex, "#1A2B3C")
        self.assertEqual(swatches[1].hex, "#FFFFFF80")


class Universality(unittest.TestCase):
    """The tooling serves any film: nothing about one subject may live in the
    role rules, and each film's own nouns are learned from its documents."""

    def test_role_words_carry_no_subject_nouns(self):
        flat = " ".join(" ".join(ws) for ws in design.ROLE_WORDS.values())
        for noun in ("porta", "door", "bode", "goat", "carro", "car"):
            self.assertNotIn(noun, flat.split(), f"{noun!r} is one film's noun")

    def test_a_film_about_anything_else_still_gets_its_roles(self):
        prose = ("fundo (#111111), candidatos em branco (#EEEEEE), o eleito em azul "
                 "(#2255FF), 'aprovado/ganhar' em verde (#22AA55), 'reprovado/perder' "
                 "em vermelho (#CC2222), apoio (#888888)")
        r = design.roles(design.parse_palette(prose))
        self.assertEqual(r["choice"], "#2255FF")
        self.assertEqual(r["positive"], "#22AA55")
        self.assertEqual(r["negative"], "#CC2222")

    def test_the_films_own_nouns_are_learned_from_the_palette(self):
        d = design.Design.from_styles(DesignFromStyles.STYLES)
        self.assertIn("bode", d.words("negative"))
        self.assertIn("carro", d.words("positive"))
        self.assertIn("perder", d.words("negative"))     # the generic word stays too
        self.assertNotIn("bode", d.words("positive"))


class DesignFromStyles(unittest.TestCase):
    STYLES = {"styles": [
        {"name": "2d motion graphics", "fit": 9, "palette": PALETTE_PROSE,
         "typography": "Sans geométrica, números tabulares, extra-bold para 1/3 e 2/3",
         "motion": "4-8 s por beat, easing suave, câmera estática"},
        {"name": "tabletop", "fit": 7, "palette": "#000000"}]}

    def test_primary_treatment_is_the_first_one(self):
        d = design.Design.from_styles(self.STYLES)
        self.assertEqual(d.treatment, "2d motion graphics")
        self.assertEqual(d.role("background"), "#14213D")

    def test_missing_styles_gives_a_usable_default_and_says_so(self):
        d = design.Design.from_styles(None)
        self.assertTrue(d.role("background").startswith("#"))
        self.assertEqual(d.source, "fallback")

    def test_type_scale_is_monotone_and_rooted_in_the_frame_height(self):
        d = design.Design.from_styles(self.STYLES, height=1080)
        sizes = [d.size(k) for k in ("caption", "label", "body", "title", "display")]
        self.assertEqual(sizes, sorted(sizes))
        self.assertGreater(d.size("display"), d.size("caption") * 2)


class Bands(unittest.TestCase):
    def test_three_bands_cover_the_frame_without_overlap(self):
        bands = design.bands(1080)
        self.assertEqual(bands.super.top, 0)
        self.assertEqual(bands.super.bottom, bands.stage.top)
        self.assertEqual(bands.stage.bottom, bands.caption.top)
        self.assertEqual(bands.caption.bottom, 1080)

    def test_bands_scale_with_the_frame(self):
        self.assertEqual(design.bands(2160).caption.top, design.bands(1080).caption.top * 2)

    def test_a_box_entering_the_caption_band_is_a_collision(self):
        bands = design.bands(1080)
        self.assertTrue(design.collides((100, 700, 300, 200), bands.caption))   # y 700..900
        self.assertFalse(design.collides((100, 300, 300, 200), bands.caption))  # y 300..500


class Captions(unittest.TestCase):
    TAKE = {"at": 10.0, "seconds": 6.0, "text": "um dois três quatro cinco seis sete oito",
            "words": [{"text": w, "start": i * 0.75, "end": i * 0.75 + 0.6}
                      for i, w in enumerate("um dois três quatro cinco seis sete oito".split())]}

    def test_chunks_respect_the_limit_and_cover_every_word(self):
        chunks = design.caption_chunks(self.TAKE, limit=14)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c.text) <= 14 for c in chunks))
        self.assertEqual(" ".join(c.text for c in chunks), self.TAKE["text"])

    def test_chunk_times_come_from_the_words_offset_by_the_take(self):
        first = design.caption_chunks(self.TAKE, limit=14)[0]
        self.assertAlmostEqual(first.start, 10.0)
        self.assertGreater(first.end, first.start)

    def test_a_take_without_word_timings_is_split_proportionally(self):
        take = {"at": 0.0, "seconds": 4.0, "text": "a b c d e f g h", "words": []}
        chunks = design.caption_chunks(take, limit=5)
        self.assertEqual(chunks[0].start, 0.0)
        self.assertAlmostEqual(chunks[-1].end, 4.0, places=2)


class Figures(unittest.TestCase):
    def test_fractions_percentages_and_numbers_are_found_in_order(self):
        found = design.figures("1/3 e 2/3; 50%; 79–87%; ~10.000 cartas")
        self.assertEqual([f.literal for f in found],
                         ["1/3", "2/3", "50%", "79–87%", "10.000"])
        self.assertAlmostEqual(found[0].value, 1 / 3)
        self.assertAlmostEqual(found[2].value, 0.5)
        self.assertAlmostEqual(found[3].value, 0.83, places=2)   # a range is its midpoint
        self.assertEqual(found[4].value, 10000)

    def test_no_figures_is_an_empty_list_not_an_error(self):
        self.assertEqual(design.figures("Troca?"), [])


if __name__ == "__main__":
    unittest.main()


class TokensForTheDocument(unittest.TestCase):
    """What the authoring agent gets from the design: <styles> tokens by role,
    and the band numbers its findings are checked against."""

    def test_tokens_are_named_by_role_and_carry_the_films_hexes(self):
        d = design.Design.from_styles(DesignFromStyles.STYLES)
        tokens = d.tokens()
        self.assertEqual(tokens["--background"], "#14213D")
        self.assertEqual(tokens["--choice"], "#3A86FF")
        self.assertEqual(tokens["--negative"], "#EF476F")
        self.assertEqual(set(tokens), {"--background", "--object", "--choice", "--positive",
                                       "--negative", "--muted", "--accent", "--ink"})

    def test_tokens_always_exist_even_without_styles(self):
        tokens = design.Design.from_styles(None).tokens()
        self.assertTrue(all(v.startswith("#") for v in tokens.values()))

    def test_band_findings_name_nodes_that_enter_the_caption_band(self):
        doc = b'''<scene version="1.1"><project width="1920" height="1080" fps="30" duration="5"/>
        <composition>
          <shape id="ok" shape="rect" x="0" y="300" width="100" height="100"/>
          <shape id="low" shape="rect" x="0" y="800" width="100" height="100"/>
          <layer id="sup" asset="t" x="0" y="40"/>
        </composition></scene>'''
        findings = design.band_findings(doc, height=1080)
        self.assertTrue(any("low" in f and "caption" in f for f in findings))
        self.assertFalse(any("'ok'" in f for f in findings))

    def test_band_findings_skip_what_a_layout_group_will_place(self):
        doc = b'''<scene version="1.1"><composition>
          <group id="g" layout="row"><shape id="child" shape="rect" y="900" height="50"/></group>
        </composition></scene>'''
        self.assertEqual(design.band_findings(doc, height=1080), [])
