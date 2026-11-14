"""Checks of the 12 Nov fixes for real Zoom recordings (no models, no video needed)."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

sys.path.insert(0, str(Path(__file__).parent))
import _isolated  # noqa: E402,F401  (temporary library, before datadistill is imported)

from datadistill import slides, speech_names  # noqa: E402
from datadistill.screen_names import drop_labels_in_shared, parse_name  # noqa: E402
from datadistill.speakers import snap_changes  # noqa: E402
from datadistill.summary import formal_votes  # noqa: E402


def words(text, starts):
    return [{"word": " " + w, "start": s, "end": s + 0.3} for w, s in zip(text.split(), starts)]


class Labels(unittest.TestCase):
    def test_zoom_icons_removed(self):
        self.assertEqual(parse_name("Rob Aloise Z"), "Rob Aloise")
        self.assertEqual(parse_name("Donna Hamzy Carroccia Z"), "Donna Hamzy Carroccia")
        self.assertEqual(parse_name("Omari Lewis &"), "Omari Lewis")
        self.assertEqual(parse_name("Jason B"), "Jason B")            # a last initial stays

    def test_roles_after_the_name_removed(self):
        self.assertEqual(parse_name("Jason Hayes First Select..."), "Jason Hayes")
        self.assertEqual(parse_name("Matt Hart Executive Director"), "Matt Hart")

    def test_two_people_with_one_first_name_stay_apart(self):
        from datadistill.screen_names import canonical_names
        c = {"Jason B": 31, "Jason": 20, "Jason H": 17, "Jason Bowsza": 13, "Jason Bow": 5, "Wayne": 5, "Wayne Haber": 30}
        m, seen = canonical_names([{"labels": [{"name": n}]} for n, k in c.items() for _ in range(k)])
        self.assertEqual(m["Jason B"], "Jason Bowsza")
        self.assertEqual(m["Jason Bow"], "Jason Bowsza")
        self.assertEqual(m["Jason H"], "Jason H")           # not glued to "Jason B"
        self.assertEqual(m["Jason"], "Jason")               # could be either Jason: not guessed
        self.assertEqual(m["Wayne"], "Wayne Haber")

    def test_labels_inside_shared_content_dropped(self):
        lab = lambda name, x: {"name": name, "x": x, "y": 400, "w": 80, "h": 14, "W": 1280, "H": 720}  # noqa: E731
        samples = [{"t": float(t), "labels": [lab("Craig OKR Deck", 300), lab("Lisa Thomas", 1180)]} for t in range(0, 100, 5)]
        periods = [(0.0, 100.0, "share", (0, 0, 1100, 720))]
        _, keep = drop_labels_in_shared(samples, ["Craig OKR Deck", "Lisa Thomas"], periods)
        self.assertEqual(keep, ["Lisa Thomas"])


class SpeakerChanges(unittest.TestCase):
    def test_flip_inside_a_phrase_is_undone(self):
        w = words("there and Alita will need some slots as well. So we can", [0, .35, .7, 1.05, 1.4, 1.75, 2.1, 2.45, 2.8, 3.6, 3.95, 4.3])
        self.assertEqual(set(snap_changes(w, list("AAABBBAAAAAA"))), {"A"})

    def test_real_change_at_a_pause_stays(self):
        w = words("that is all. Thanks Lisa yes", [0, .35, .7, 1.6, 1.95, 2.3])
        self.assertEqual(snap_changes(w, list("AAABBB")), list("AAABBB"))

    def test_change_moves_to_the_sentence_end(self):
        w = words("we should go to the next item. Okay sure", [0, .35, .7, 1.05, 1.4, 1.75, 2.1, 2.45, 2.8])
        self.assertEqual(snap_changes(w, list("AAAABBBBB")), list("AAAAAAABB"))


class NamesFromSpeech(unittest.TestCase):
    KNOWN = ["Lisa Thomas", "Matt Hart", "Jonathan Colman", "Caitlin Palmer", "Jason Bowsza", "Jason Hayes"]

    def test_name_here_only_at_a_turn_start(self):
        t = lambda s, a, b, txt: {"speaker": s, "start": a, "end": b, "text": txt}  # noqa: E731
        mid = [t("S1", 0, 5, "our East Coast Greenway Gap Closure"), t("S1", 5.2, 8, "Project here. We'd like funding.")]
        self.assertEqual(dict(speech_names.find_intros(mid)), {})
        start = [t("S1", 0, 5, "Thanks everyone."), t("S2", 6, 8, "Sam here. Quick question.")]
        self.assertEqual(speech_names.find_intros(start)["S2"]["Sam"], 1)

    def test_resolve(self):
        self.assertEqual(speech_names.resolve_known("John Coleman", self.KNOWN), "Jonathan Colman")
        self.assertEqual(speech_names.resolve_known("matt", self.KNOWN), "Matt Hart")
        self.assertIsNone(speech_names.resolve_known("Jason", self.KNOWN))       # two Jasons: ambiguous

    def test_handover_and_thanks(self):
        t = lambda s, a, b, txt: {"speaker": s, "start": a, "end": b, "text": txt}  # noqa: E731
        turns = [t("S1", 0, 5, "if matt or any staff want to chime in"), t("S2", 6, 9, "I can lead off, Lisa."),
                 t("S3", 10, 12, "So moved, Madam Chair."), t("S1", 13, 15, "Was that John Coleman?"),
                 t("S2", 16, 20, "Caitlin, anything else you want to add?"), t("S4", 21, 25, "Just one thing.")]
        a = speech_names.find_addressed(turns, self.KNOWN)
        self.assertEqual(a["S2"]["Matt Hart"], 1)
        self.assertEqual(a["S3"]["Jonathan Colman"], 1)
        self.assertEqual(a["S4"]["Caitlin Palmer"], 1)

    def test_assign_rules(self):
        from collections import Counter
        addressed = {"S6": Counter({"Matt Hart": 1}), "S7": Counter({"Caitlin Palmer": 3}), "S4": Counter({"Jason Hayes": 2})}
        current = {"S6": (None, None), "S7": ("Pauline Yoder", "screen (motion)"), "S4": ("Jason", "said their name")}
        r = speech_names.assign_addressed(addressed, current)
        self.assertNotIn("S6", r)                                     # one vote is not enough
        self.assertEqual(r["S7"]["name"], "Caitlin Palmer")           # strong evidence beats a tile match
        self.assertEqual(r["S4"]["name"], "Jason Hayes")              # first name completed


class Votes(unittest.TestCase):
    def test_minutes_and_adjourn(self):
        t = lambda a, txt: {"start": a, "end": a + 3, "text": txt}  # noqa: E731
        turns = [t(12, "We'll start with a motion to approve the minutes of September 10th."),
                 t(24, "So moved, Madam Chair."), t(45, "motion by John Coleman, seconded by Jason Hayes to adopt the minutes"),
                 t(58, "all those in favor please say aye."), t(70, "abstentions that's unanimous so motion passes"),
                 t(5000, "Seeing none, I would entertain a motion to adjourn."), t(5040, "Okay, then we are adjourned.")]
        v = formal_votes(turns)
        self.assertEqual(len(v), 2)
        self.assertIn("approve the minutes of September 10th: passed unanimously", v[0]["decision"])
        self.assertIn("moved by John Coleman", v[0]["decision"])
        self.assertTrue(v[1]["decision"].startswith("Motion to adjourn"))

    def test_no_votes_in_ordinary_talk(self):
        self.assertEqual(formal_votes([{"start": 1, "end": 3, "text": "we agreed to move the deck to Friday"}]), [])


class Layout(unittest.TestCase):
    def test_strip_vs_gallery(self):
        L = np.zeros((slides.GH, slides.GW), bool)
        L[:, -8:] = True                                              # moving tile strip on the right
        self.assertEqual(slides._strip(L)[:2], ("share", "right"))
        G = np.zeros((slides.GH, slides.GW), bool)
        G[5:15, 5:20] = G[5:15, 30:45] = G[25:35, 10:25] = G[25:35, 50:65] = True   # faces spread over a gallery
        self.assertEqual(slides._strip(G)[0], "meeting")
        self.assertEqual(slides._strip(np.zeros_like(L))[0], "still")


if __name__ == "__main__":
    unittest.main()
