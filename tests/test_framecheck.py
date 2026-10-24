"""framecheck.py: objective checks on a proof frame and on the engine's words.

The author's eyes are the model's; these are the checks that need none. A
frame is judged from its pixels (flat, black, clipped, too dark) and the
engine's diagnostics are read for the objects a camera never frames: a hero
out of view is an error, a set mostly out of view is a camera pointing at
nothing. Everything here is a pure function over bytes and text.
"""

import io
import unittest

from PIL import Image

from ytstudio import framecheck


def png(pattern: str, size=(64, 36)) -> bytes:
    im = Image.new("RGB", size, (8, 8, 8))
    w, h = size
    if pattern == "varied":
        for x in range(w):
            for y in range(h):
                im.putpixel((x, y), ((x * 4) % 256, (y * 7) % 256, 120))
    elif pattern == "speck":
        im.putpixel((3, 3), (255, 255, 255))
    elif pattern == "clipped":
        # a blown-out window filling 40% of the frame over a lit room
        for x in range(w):
            for y in range(h):
                im.putpixel((x, y), (255, 255, 255) if x < int(w * 0.4)
                            else ((x * 3) % 160 + 40, (y * 5) % 160 + 40, 90))
    elif pattern == "dark":
        # detail is there, but the whole frame sits near black
        for x in range(w):
            for y in range(h):
                v = (x * 3 + y * 2) % 30
                im.putpixel((x, y), (v, v, v + 4))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


OFF_CAMERA = ("warning: object3D shot/{id} is drawn from 0.000 s to 2.967 s and is "
              "never inside the camera's view: its bounds lie wholly behind, beside "
              "or beyond the camera's frustum in every frame rendered, so only its "
              "shadows or reflections can show")


class Pixels(unittest.TestCase):

    def test_stats_report_clipped_and_dark_shares(self):
        st = framecheck.frame_stats(png("clipped"))
        self.assertGreater(st["clipped"], 0.3)
        self.assertLess(st["clipped"], 0.5)
        self.assertIn("mean", st)
        self.assertIn("std", st)
        self.assertAlmostEqual(framecheck.frame_stats(png("varied"))["clipped"], 0.0, places=1)

    def test_black_frame_is_an_error(self):
        found = framecheck.judge_frame(png("black"), 2.0, "sym")
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].startswith("error"))
        self.assertIn("black", found[0])
        self.assertIn("2s of sym", found[0])

    def test_varied_frame_passes(self):
        self.assertEqual(framecheck.judge_frame(png("varied"), 2.0, "sym"), [])

    def test_nearly_flat_frame_is_a_warning(self):
        found = framecheck.judge_frame(png("speck"), 2.0, "sym")
        self.assertTrue(found and found[0].startswith("warning"))
        self.assertIn("nearly flat", found[0])

    def test_clipped_exposure_is_a_warning_with_the_share(self):
        found = framecheck.judge_frame(png("clipped"), 1.5, "sym")
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].startswith("warning"))
        self.assertIn("clipped", found[0])
        self.assertIn("%", found[0])
        # the threshold is a parameter, not a constant buried in the check
        self.assertEqual(framecheck.judge_frame(png("clipped"), 1.5, "sym", clip_share=0.6), [])

    def test_too_dark_frame_is_a_warning_not_a_black_error(self):
        found = framecheck.judge_frame(png("dark"), 1.5, "sym")
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].startswith("warning"))
        self.assertIn("dark", found[0])

    def test_unreadable_bytes_are_a_warning(self):
        found = framecheck.judge_frame(b"not a png", 1.0, "sym")
        self.assertTrue(found[0].startswith("warning"))


class Diagnostics(unittest.TestCase):

    def test_never_in_view_reads_ids_and_strips_the_include_prefix(self):
        lines = ["rendered on host", OFF_CAMERA.format(id="desk-leg0"),
                 OFF_CAMERA.format(id="hero-plane"), "warning: something else",
                 OFF_CAMERA.format(id="desk-leg0")]
        self.assertEqual(framecheck.never_in_view(lines), ["desk-leg0", "hero-plane"])
        self.assertEqual(framecheck.never_in_view("\n".join(lines)), ["desk-leg0", "hero-plane"])
        self.assertEqual(framecheck.never_in_view([]), [])

    def test_hero_is_a_naming_convention(self):
        self.assertTrue(framecheck.is_hero("hero-plane"))
        self.assertTrue(framecheck.is_hero("hero-plane-wing"))
        self.assertFalse(framecheck.is_hero("desk"))
        self.assertFalse(framecheck.is_hero("superhero-cape"))

    def test_symbol_objects_lists_the_object3d_ids_of_one_symbol(self):
        doc = ('<scene><symbols>'
               '<symbol id="world-a" width="1" height="1"><camera id="cam"/>'
               '<object3D id="floor" primitive="plane"/>\n'
               '<object3D id="hero-ball" primitive="sphere"><animate property="x"/></object3D>'
               '</symbol>'
               '<symbol id="world-b" width="1" height="1"><object3D id="other"/></symbol>'
               '</symbols></scene>')
        self.assertEqual(framecheck.symbol_objects(doc, "world-a"), ["floor", "hero-ball"])
        self.assertEqual(framecheck.symbol_objects(doc, "world-b"), ["other"])
        self.assertEqual(framecheck.symbol_objects(doc, "nope"), [])

    def test_a_hero_out_of_view_is_an_error(self):
        objects = ["floor", "wall", "desk", "hero-plane"]
        found = framecheck.view_findings(["hero-plane"], objects, symbol="world-a", at=1.5)
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].startswith("error"))
        self.assertIn("hero-plane", found[0])
        self.assertIn("1.5s of world-a", found[0])

    def test_set_objects_out_of_view_in_one_window_are_information(self):
        objects = ["floor", "wall", "desk", "hero-plane"]
        found = framecheck.view_findings(["wall"], objects, symbol="world-a", at=5)
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].startswith("note"))
        self.assertIn("1 of 3", found[0])
        self.assertIn("wall", found[0])
        self.assertEqual(framecheck.view_findings([], objects, symbol="world-a", at=5), [])

    def test_ids_the_symbol_does_not_own_are_ignored(self):
        found = framecheck.view_findings(["elsewhere"], ["floor"], symbol="world-a", at=5)
        self.assertEqual(found, [])

    def test_an_object_never_in_view_across_every_window_is_an_error(self):
        # The delivery gate fails closed on the engine's warning, so authoring
        # does too: a and b are out in every window; c and d were seen once.
        objects = ["a", "b", "c", "d", "hero-x"]
        per_window = [["a", "b", "c"], ["a", "b"], ["a", "b", "d"]]
        found = framecheck.never_framed(per_window, objects, symbol="w")
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].startswith("error"))
        self.assertIn("2 of 5", found[0])
        self.assertIn("a, b", found[0])
        self.assertIn("symbol w", found[0])
        self.assertIn("visible", found[0])           # the fix: cull or key visibility
        self.assertEqual(framecheck.never_framed([["c"], ["d"]], objects, symbol="w"), [])

    def test_most_of_the_set_never_framed_says_the_camera_points_at_nothing(self):
        found = framecheck.never_framed([["a", "b", "c"]], ["a", "b", "c", "d"], symbol="w")
        self.assertIn("pointing at nothing", found[0])
        found = framecheck.never_framed([["a"]], ["a", "b", "c", "d"], symbol="w")
        self.assertNotIn("pointing at nothing", found[0])

    def test_never_framed_needs_windows_and_objects(self):
        self.assertEqual(framecheck.never_framed([], ["a"], symbol="w"), [])
        self.assertEqual(framecheck.never_framed([["a"]], [], symbol="w"), [])

    def test_a_shot_window_names_the_shot_not_a_frame(self):
        found = framecheck.view_findings(["hero-plane"], ["hero-plane", "desk"],
                                         symbol="world-a", at=5.0, label="1.2")
        self.assertIn("shot 1.2", found[0])
        self.assertIn("world-a", found[0])

    def test_report_findings_reads_the_encode_envelope(self):
        # the engine's envelope: findings sit on each output, and some engines
        # put them on the result itself; diagnostics are not findings
        report = {"diagnostics": [{"code": "E12", "severity": "warning", "message": "include"}],
                  "result": {"outputs": [{"files": ["x.mp4"], "findings": [
                      {"code": "X-rs-scene-render-3D-OFF-CAMERA", "severity": "warning",
                       "message": OFF_CAMERA.format(id="desk")[len("warning: "):]}]}],
                      "findings": [{"severity": "note", "message": "fine"}]}}
        lines = framecheck.report_findings(report)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("warning: object3D shot/desk"))
        self.assertEqual(lines[1], "note: fine")
        self.assertEqual(framecheck.never_in_view(lines), ["desk"])
        self.assertEqual(framecheck.report_findings({}), [])
        self.assertEqual(framecheck.report_findings(None), [])


class Grid(unittest.TestCase):
    """AUTO-110: a labelled grid drawn on a copy of a proof frame, so a
    reviewer can say where a finding is (`C4`). A measuring aid only: the
    copy is new bytes, the frame itself is never touched."""

    def test_the_overlay_is_a_new_png_of_the_same_size(self):
        frame = png("varied", size=(320, 180))
        before = bytes(frame)
        out = framecheck.grid_overlay(frame)
        self.assertEqual(frame, before)                      # the input is untouched
        self.assertTrue(out.startswith(b"\x89PNG"))
        clean, grid = Image.open(io.BytesIO(frame)), Image.open(io.BytesIO(out))
        self.assertEqual(grid.size, clean.size)
        self.assertNotEqual(list(grid.convert("RGB").getdata()),
                            list(clean.convert("RGB").getdata()))
        self.assertEqual(framecheck.grid_overlay(frame), out)  # the same copy every time

    def test_48_cells_named_a1_to_h6_tile_the_frame(self):
        self.assertEqual((framecheck.GRID_COLS, framecheck.GRID_ROWS), (8, 6))
        cells = framecheck.grid_cells((1920, 1080), 8, 6)
        self.assertEqual(len(cells), 48)
        self.assertEqual(set(cells), set(framecheck.GRID_CELLS))
        self.assertEqual(len(framecheck.GRID_CELLS), 48)
        self.assertEqual({c[0] for c in cells}, set("ABCDEFGH"))      # columns, left to right
        self.assertEqual({c[1:] for c in cells}, {str(r) for r in range(1, 7)})  # rows, top down
        self.assertEqual(cells["A1"], (0, 0, 240, 180))
        self.assertEqual(cells["H6"], (1680, 900, 1920, 1080))
        self.assertEqual(cells["C3"], (480, 360, 720, 540))
        # exactly: the areas add up to the frame and no two cells overlap
        self.assertEqual(sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in cells.values()),
                         1920 * 1080)
        rects = list(cells.values())
        for i, a in enumerate(rects):
            for b in rects[i + 1:]:
                self.assertFalse(min(a[2], b[2]) > max(a[0], b[0])
                                 and min(a[3], b[3]) > max(a[1], b[1]), (a, b))

    def test_an_odd_frame_still_tiles_exactly(self):
        cells = framecheck.grid_cells((1001, 557))
        self.assertEqual(sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in cells.values()),
                         1001 * 557)

    def test_cells_merge_into_one_pixel_rectangle(self):
        self.assertEqual(framecheck.cells_rect(["C3", "C4"], (1920, 1080)),
                         (480, 360, 720, 720))
        self.assertIsNone(framecheck.cells_rect([], (1920, 1080)))
        self.assertIsNone(framecheck.cells_rect(["Z9"], (1920, 1080)))


class ChecklistSettings(unittest.TestCase):
    """AUTO-143: a film may cut between settings, so the checklist scopes room
    and light to one setting and says a planned change of setting is no
    break. It is rendered in three prompts (the vfx frame review, the
    keyframe style frame, the author's brief), none of which knows symbols."""

    def item(self, key):
        return next(text for k, text, _ in framecheck.CHECKLIST if k == key)

    def test_light_and_continuity_are_scoped_to_one_setting(self):
        self.assertIn("the light rig stays the same within one setting", self.item("light"))
        continuity = self.item("continuity")
        self.assertIn("the same hero, grade and camera language throughout", continuity)
        self.assertIn("room and light stay the same within one setting", continuity)
        self.assertIn("a planned change of setting is not a break", continuity)
        self.assertNotIn("in every frame", self.item("light"))

    def test_no_rendering_names_a_symbol_or_a_world(self):
        from ytstudio import keyframe, sdkkit, vfx
        text = framecheck.checklist_text()
        for word in ("symbol", "world"):
            self.assertNotIn(word, text.lower())
        for prompt in (vfx.REVIEW_ROLE, keyframe.REVIEW_ROLE, sdkkit.BRIEF):
            self.assertIn(text, prompt)
            self.assertIn("a planned change of setting is not a break", prompt)


if __name__ == "__main__":
    unittest.main()
