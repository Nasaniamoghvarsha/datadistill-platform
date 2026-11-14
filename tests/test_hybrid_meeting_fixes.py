"""Checks of the fixes found on the real hybrid Teams recording (13 Nov). No models, no video needed."""
import sys
import unittest
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

sys.path.insert(0, str(Path(__file__).parent))
import _isolated  # noqa: E402,F401  (temporary library, before datadistill is imported)

from datadistill import slides, soundtrack, speech_names  # noqa: E402
from datadistill.docs import _is_font_table  # noqa: E402
from datadistill.speakers import merge_same_voice  # noqa: E402


class Names(unittest.TestCase):
    def test_my_name_is_beats_this_is(self):
        t = lambda s, a, b, txt: {"speaker": s, "start": a, "end": b, "text": txt}  # noqa: E731
        turns = [t("S7", 0, 5, "all right my name is Alex and with me here I have Sam"),
                 t("S7", 300, 304, "So this is Erikk,"), t("S7", 305, 307, "and I will switch back to the large screen")]
        intros = speech_names.find_intros(turns)
        self.assertGreater(intros["S7"]["Alex"], intros["S7"]["Erikk"])
        self.assertEqual(speech_names.assign(intros, [])["S7"]["name"], "Alex")

    def test_degree_adverbs_are_never_a_name(self):
        mk = lambda txt: [{"speaker": "S1", "start": 0, "end": 3, "text": txt}]  # noqa: E731
        for phrase in ("this is quite", "this is quite short, I think", "this is rather, I'm sure", "so this is pretty. I can do it"):
            self.assertEqual(dict(speech_names.find_intros(mk(phrase)).get("S1", {})), {}, phrase)
        self.assertEqual(dict(speech_names.find_intros(mk("this is tom"))["S1"]), {"Tom": 1})     # a real lower-case intro still works

    def test_tie_stays_unnamed(self):
        self.assertEqual(speech_names.assign({"S1": Counter({"Alex": 1, "Erik": 1})}, []), {})

    def test_who_calls_a_name_is_not_that_person(self):
        t = lambda s, txt: {"speaker": s, "start": 0, "end": 1, "text": txt}  # noqa: E731
        turns = [t("S7", "Jonathan Cabral, maybe he wants to pick up on that point. Jonathan, anything?"),
                 t("S7", "Jonathan, are you able to add to that?"), t("S7", "Thank you, Lisa."),
                 t("S2", "I'm Jonathan, and I agree.")]
        calls = speech_names.addressed_by(turns, ["Jonathan Colman", "Lisa Thomas"])
        self.assertEqual(calls["S7"]["Jonathan Colman"], 2)
        self.assertEqual(calls["S7"]["Lisa Thomas"], 1)
        self.assertNotIn("Jonathan Colman", calls.get("S2", {}))      # a self-mention is not a call


class Screens(unittest.TestCase):
    def test_teams_window_is_not_a_slide(self):
        self.assertTrue(slides.is_app_window("@& = Microsoft Teama Edit View Window Help\nRecording has started."))
        self.assertTrue(slides.is_app_window("Crm Peogle Rama React view Rooms «opt More «Camera ie Step warn"))
        self.assertFalse(slides.is_app_window("Field Service - Current Deployments\nBasic Deployments"))
        self.assertFalse(slides.is_app_window("Training Portfolio\nLearning with technology\nOfferings"))

    def test_font_table_page_skipped(self):
        junk = "Characters for Embedded characters: !\"#$%&'()*+,./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        self.assertTrue(_is_font_table(junk))
        self.assertFalse(_is_font_table("FWA – Key characteristics\nFixed wireless access over 4G and 5G"))


class Voices(unittest.TestCase):
    def test_split_voice_merged_different_people_kept(self):
        rng = np.random.default_rng(0)
        a, b = rng.normal(size=192), rng.normal(size=192)          # two different people
        def around(c, n, noise):
            return np.array([c + noise * rng.normal(size=192) for _ in range(n)])
        emb = np.vstack([around(a, 30, 0.5), around(a + 0.2 * rng.normal(size=192), 30, 0.5), around(b, 30, 0.5)])
        lab = np.array([0] * 30 + [1] * 30 + [2] * 30)              # person a split in clusters 0 and 1
        out = merge_same_voice(emb, lab)
        self.assertEqual(len(set(out[:60])), 1)
        self.assertNotEqual(out[0], out[-1])


class Soundtrack(unittest.TestCase):
    def test_music_without_pauses_vs_speech_with_pauses(self):
        sr = soundtrack.SR
        t = np.arange(0, 60 * sr) / sr
        music = 0.3 * np.sin(2 * np.pi * 220 * t) + 0.1 * np.sin(2 * np.pi * 330 * t)
        speech = music.copy()
        for s in range(0, 60, 1):                                   # a pause every second, like between words
            speech[int((s + 0.6) * sr):int((s + 1.0) * sr)] = 0.001
        self.assertEqual(soundtrack.regions(music.astype(np.float32)), [(0.0, 60.0)])
        self.assertEqual(soundtrack.regions(speech.astype(np.float32)), [])
        self.assertTrue(soundtrack.is_soundtrack(10, 20, [(0.0, 60.0)]))
        self.assertFalse(soundtrack.is_soundtrack(70, 80, [(0.0, 60.0)]))


class SummaryTimes(unittest.TestCase):
    def test_time_without_hour_found_in_the_right_hour(self):
        from datadistill import summary
        turn = lambda t, who, txt: {"start": t, "end": t + 5, "speaker_name": who, "text": txt}  # noqa: E731
        turns = [turn(26 * 60, "Alex", "the radio masts in the field need power"),
                 turn(2 * 3600 + 26 * 60 + 40, "Speaker 1", "so in 24 hours you deliver a tech concept to the jury")]
        res = {"discussion": [{"point": "Teams deliver a tech concept to the jury in 24 hours", "time": "26:36",
                               "speakers": ["Speaker 1", "Maria"]}]}
        out, removed = summary.check(res, turns, 3 * 3600, ["Alex", "Speaker 1", "Maria"])
        self.assertEqual(removed, [])
        self.assertEqual(out["discussion"][0]["time"], "02:26:40")
        self.assertEqual(out["discussion"][0]["speakers"], ["Speaker 1"])   # Maria does not speak there


if __name__ == "__main__":
    unittest.main()
