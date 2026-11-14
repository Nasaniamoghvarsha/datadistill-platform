"""Checks of the accuracy and question-test scoring (no models, no database).   python -m unittest discover tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

sys.path.insert(0, str(Path(__file__).parent))
import _isolated  # noqa: E402,F401  (temporary library, before datadistill is imported)

from datadistill.evaluate import der, name_scores, word_scores, words  # noqa: E402
from datadistill.questions import _has, _norm  # noqa: E402


class Normalise(unittest.TestCase):
    def test_numbers_written_out_match_digits(self):
        self.assertEqual(words("traffic grew by eighteen percent"), words("Traffic grew by 18%."))
        self.assertEqual(words("forty two sites"), ["42", "sites"])
        self.assertEqual(words("three hundred and five"), ["305"])
        self.assertEqual(words("two thousand"), ["2000"])
        self.assertEqual(words("1,000 users"), ["1000", "users"])

    def test_separate_numbers_stay_separate(self):
        self.assertEqual(words("one two three"), ["1", "2", "3"])
        self.assertEqual(words("twenty twelve"), ["20", "12"])

    def test_fillers_and_punctuation_ignored(self):
        self.assertEqual(words("Uh, so -- we, um, agreed!"), ["so", "we", "agreed"])
        self.assertIn("uh", words("uh yes", fillers=False))

    def test_spelling_variants(self):
        self.assertEqual(words("energy savings programme"), words("energy savings program"))
        self.assertEqual(words("my favourite"), words("my favorite"))

    def test_spoken_forms_and_contractions(self):
        self.assertEqual(words("we're gonna start"), words("we are going to start"))
        self.assertEqual(words("'cause I'm late"), words("because I am late"))

    def test_spelled_letters(self):
        self.assertEqual(words("a new T_V_ remote"), words("a new TV remote"))
        self.assertEqual(words("I I think"), ["i", "i", "think"])      # a stutter, not spelled letters

    def test_hyphens(self):
        self.assertEqual(words("kick-off"), words("kickoff"))
        self.assertEqual(words("television-related", join_hyphens=False), ["television", "related"])


class WordErrorRate(unittest.TestCase):
    def test_identical(self):
        self.assertEqual(word_scores("the plan is ready", "The plan is ready.")["wer"], 0)

    def test_counts_each_error_kind(self):
        s = word_scores("johan owns the rollout plan", "yuan owns rollout plan now")
        self.assertEqual((s["substituted"], s["deleted"], s["inserted"]), (1, 1, 1))
        self.assertAlmostEqual(s["wer"], 3 / 5)
        self.assertEqual(s["top_substitutions"][0][0], "johan → yuan")

    def test_content_words(self):
        s = word_scores("maria owns the supplier discussion", "maria owns the discussion")
        self.assertAlmostEqual(s["content_recall"], 3 / 4)
        self.assertEqual(s["content_precision"], 1)


def seg(spk, a, b):
    return {"speaker": spk, "start": a, "end": b, "text": "x"}


class WhoSpokeWhen(unittest.TestCase):
    def test_perfect_with_other_labels(self):
        ref = [seg("Anna", 0, 10), seg("Bo", 10, 20)]
        hyp = [seg("S2", 0, 10), seg("S1", 10, 20)]
        r = der(ref, hyp, collar=0)
        self.assertAlmostEqual(r["der"], 0, places=3)
        self.assertEqual(r["mapping"], {"S2": "Anna", "S1": "Bo"})

    def test_confusion_miss_false_alarm(self):
        ref = [seg("Anna", 0, 10), seg("Bo", 10, 20)]
        hyp = [seg("S1", 0, 15), seg("S2", 15, 18), seg("S2", 25, 27)]   # 5 s wrong person, 2 s missed, 2 s extra
        r = der(ref, hyp, collar=0)
        self.assertAlmostEqual(r["confusion"], 5 / 20, places=2)
        self.assertAlmostEqual(r["missed"], 2 / 20, places=2)
        self.assertAlmostEqual(r["false_alarm"], 2 / 20, places=2)
        self.assertAlmostEqual(r["der"], 9 / 20, places=2)

    def test_collar_forgives_boundaries(self):
        ref = [seg("Anna", 0, 10), seg("Bo", 10, 20)]
        hyp = [seg("S1", 0, 10.2), seg("S2", 10.2, 20)]
        self.assertGreater(der(ref, hyp, collar=0)["der"], 0)
        self.assertAlmostEqual(der(ref, hyp, collar=0.25)["der"], 0, places=3)

    def test_names(self):
        turns = [{"speaker": "S1", "speaker_name": "Anna Berg"}, {"speaker": "S2", "speaker_name": "Speaker 2"},
                 {"speaker": "S3", "speaker_name": "Carl"}]
        n = name_scores({"S1": "Anna", "S2": "Bo", "S3": "Dan"}, turns, [])
        self.assertEqual((n["right"], n["unnamed"], n["wrong"]), (1, 1, 1))
        n = name_scores({"S1": "Iain"}, [{"speaker": "S1", "speaker_name": "Ian"}], [])
        self.assertEqual((n["right"], n["spelled_differently"]), (1, 1))
        n = name_scores({"S1": "Dr. Torous", "S2": "Ms. Paulsen"},
                        [{"speaker": "S1", "speaker_name": "John Torous"}, {"speaker": "S2", "speaker_name": "Jessica Paulson"}], [])
        self.assertEqual((n["right"], n["spelled_differently"], n["wrong"]), (2, 1, 0))
        n = name_scores({"S1": "Dr. Jackson"}, [{"speaker": "S1", "speaker_name": "John Torous"}], [])
        self.assertEqual(n["wrong"], 1)

    def test_align_excerpt_in_multi_session_reference(self):
        import random
        from datadistill.evaluate import align
        rnd = random.Random(7)
        vocab = ["network", "antenna", "rollout", "supplier", "budget", "traffic", "region", "upgrade", "energy", "cooling",
                 "permits", "review", "quarter", "delivery", "capacity", "spectrum", "latency", "coverage", "planning", "costs"]
        def turns(session, start, n):
            out, t = [], start
            for i in range(n):
                text = " ".join(rnd.choice(vocab) for _ in range(12))
                out.append({"session": session, "start": t, "end": t + 5, "speaker": f"Dr. {'AB'[i % 2]}", "text": text})
                t += 6
            return out
        ref = turns(0, 600, 200) + turns(1, 500, 200)              # the clock restarts after a break
        excerpt = [t for t in ref if t["session"] == 1 and 1100 <= t["start"] < 1700]
        hyp = [{**t, "start": t["start"] - 1100, "end": t["end"] - 1100, "speaker": "S1"} for t in excerpt]
        cut, info = align(ref, hyp, duration=600)
        self.assertTrue(info["applied"])
        self.assertEqual(info["session"], 1)
        self.assertAlmostEqual(info["offset_s"], -1100, delta=1)
        self.assertEqual(len(cut), len(excerpt))

    def test_transcript_line_pattern(self):
        from datadistill.evaluate import _TS_LINE
        m = _TS_LINE.match("00:18:10 Ms. Paulsen: Hi, good morning. Jessica Paulsen.")
        self.assertEqual((m.group(1), m.group(2), m.group(3)[:2]), ("00:18:10", "Ms. Paulsen", "Hi"))
        m = _TS_LINE.match("00:19:40 There is an increasing demand: for mental health services")
        self.assertIsNone(m.group(2))
        m = _TS_LINE.match("01:02:03 LCDR Becker: This can largely be considered")
        self.assertEqual(m.group(2), "LCDR Becker")

    def test_line_speakers(self):
        from datadistill.evaluate import line_speakers
        ref = [seg("Anna", 0, 10), seg("Bo", 10, 20)]
        hyp = [{"speaker": "S1", "start": 1, "end": 9, "text": "one two three"},
               {"speaker": "S1", "start": 11, "end": 19, "text": "four five"},
               {"speaker": "S2", "start": 30, "end": 31, "text": "six"}]
        r = line_speakers(ref, hyp, {"S1": "Anna", "S2": "Bo"})
        self.assertEqual((r["words_right"], r["words_wrong"], r["words_without_reference_speech"]), (3, 2, 1))


class QuestionFacts(unittest.TestCase):
    def test_fact_found_with_number_words(self):
        self.assertTrue(_has(_norm("Consumption went down by 9% at night."), ["nine percent"]))

    def test_whole_words_only(self):
        self.assertFalse(_has(_norm("the antennas arrived"), "antenna"))
        self.assertTrue(_has(_norm("the antennas arrived"), ["antenna", "antennas"]))


class ScreenTextFilter(unittest.TestCase):
    """The OCR noise filter must keep real slide lines with words missing from its word list, and still drop noise."""

    @classmethod
    def setUpClass(cls):
        from datadistill import textutil
        cls.t = textutil
        cls.vocab = textutil.english_vocab()
        if not cls.vocab:
            raise unittest.SkipTest("search model word list not on disk")

    def test_word_forms_are_words(self):
        for w in ["optimisation", "rollout", "reordered", "offboarding", "stakeholders", "workstream", "organised"]:
            self.assertTrue(self.t.known_word(w, self.vocab), w)

    def test_fragments_are_not_words(self):
        for w in ["setter", "rotmons", "penbeck", "readin", "enghee", "tems", "vals"]:
            if w not in self.vocab:
                self.assertFalse(self.t.known_word(w, self.vocab), w)

    def test_lines(self):
        for line in ["Next: cooling optimisation", "Rollout reordered by traffic", "Johan: rollout plan"]:
            self.assertEqual(self.t.clean_screen_text(line), line)
        for noise in ["wo ot 8 ~ « — . ~ tame setter ee toe tems tan oa", "is) Ga vals ivf o. rotmons Heading 1 = & home & £ & QQ"]:
            self.assertEqual(self.t.clean_screen_text(noise), "")


if __name__ == "__main__":
    unittest.main()
