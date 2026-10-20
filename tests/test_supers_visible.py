"""AUTO-25: the stages that draw supers read the words, not the direction."""
import unittest

from ytstudio import keyframe, pilot, qa, vfx
from ytstudio.script import Shot

DIRECTION = "lower third, fades in at the second beat, left aligned"
WORDS = ["Altitude 4,200 m", "Day 3"]
SHOT = {"label": "1.1", "visual": "a ridge", "on_screen": DIRECTION,
        "on_screen_text": WORDS, "seconds": 4}


class Vfx(unittest.TestCase):
    def test_brief_shows_the_words_and_labels_the_direction(self):
        text = "\n".join(vfx.super_lines({**SHOT, "super": vfx.shot_super(SHOT)}))
        self.assertIn("Super (the words a viewer reads): Altitude 4,200 m Day 3", text)
        self.assertIn("Direction for the super (not text on screen): " + DIRECTION, text)
        self.assertNotIn("Super: " + DIRECTION, text)

    def test_old_screenplay_does_not_repeat_the_field(self):
        old = {"label": "1", "on_screen": "Hello"}
        lines = vfx.super_lines({**old, "super": vfx.shot_super(old)})
        self.assertEqual(len(lines), 1)
        self.assertIn("Hello", lines[0])

    def test_a_direction_differing_only_in_spacing_is_not_repeated(self):
        shot = {"on_screen": "2 + 3   =   5", "super": "2 + 3 = 5"}
        self.assertEqual(vfx.super_lines(shot), ["Super (the words a viewer reads): 2 + 3 = 5"])

    def test_no_super_no_lines(self):
        self.assertEqual(vfx.super_lines({"label": "1"}), [])


class Keyframe(unittest.TestCase):
    def test_supers_are_the_visible_words(self):
        facts = keyframe.script_facts({"sections": [{"heading": "h", "shots": [SHOT]}]})
        self.assertEqual(facts["supers"], ["Altitude 4,200 m Day 3"])
        shot = facts["sections"][0]["shots"][0]
        self.assertEqual(shot["super"], "Altitude 4,200 m Day 3")
        self.assertEqual(shot["direction"], DIRECTION)


class Qa(unittest.TestCase):
    def test_evidence_carries_the_words(self):
        f = qa.super_fields(SHOT)
        self.assertEqual(f["super"], "Altitude 4,200 m Day 3")
        self.assertEqual(f["direction"], DIRECTION)
        self.assertEqual(qa.super_fields({})["super"], "")


class Pilot(unittest.TestCase):
    def _traits(self, shot):
        return pilot.traits_of(shot, {}, section="s", cues=[], fx=set(),
                               first=False, last=False)

    def test_super_trait_needs_words(self):
        self.assertIn("super", self._traits(Shot.from_raw(SHOT)))
        self.assertNotIn("super", self._traits(Shot.from_raw({"visual": "x"})))


if __name__ == "__main__":
    unittest.main()
