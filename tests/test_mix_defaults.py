"""AUTO-44: the mix keeps the narration intelligible by default."""
from __future__ import annotations

import unittest

from tests import test_build_audio as base


class MixDefaults(base.AudioFromSound):
    def _mix(self, sound=None, **kw):
        sound = sound or base._sound(self.cues())
        data, manifest = self.builder(voice=self.voice, sound=sound, **kw).build()
        return base._checked(data).find("audioMix"), manifest

    def test_master_is_integrated_by_default(self):
        mix, _ = self._mix()
        self.assertEqual(mix.find("master").get("normalize"), "integrated")

    def test_every_non_narration_bus_ducks_under_the_narration(self):
        mix, _ = self._mix()
        buses = {b.get("id"): b for b in mix.findall("bus")}
        self.assertIn("effects", buses)
        others = [b for i, b in buses.items() if i != "narration"]
        self.assertTrue(others)
        for b in others:
            self.assertEqual(b.get("duckUnder"), "narration", b.get("id"))
        self.assertIsNone(buses["narration"].get("duckUnder"))

    def test_dynamic_without_a_reason_stays_integrated_and_says_so(self):
        mix, manifest = self._mix(normalize="dynamic")
        self.assertEqual(mix.find("master").get("normalize"), "integrated")
        self.assertTrue(any("dynamic" in f for f in manifest["findings"]),
                        manifest["findings"])

    def test_dynamic_with_a_reason_is_honoured(self):
        mix, _ = self._mix(normalize="dynamic", normalize_reason="a live-room recording")
        self.assertEqual(mix.find("master").get("normalize"), "dynamic")

    def test_the_sheet_can_ask_for_dynamic_with_a_reason(self):
        sound = base._sound(self.cues())
        sound["normalize"] = {"mode": "dynamic", "reason": "a live-room recording"}
        mix, _ = self._mix(sound=sound)
        self.assertEqual(mix.find("master").get("normalize"), "dynamic")


if __name__ == "__main__":
    unittest.main()
