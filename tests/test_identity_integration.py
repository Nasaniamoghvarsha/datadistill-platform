"""The friend's speaker-stability and identity layers as joined to this pipeline (14 Nov). No models, no recordings."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).parent))
import _isolated  # noqa: E402,F401

from datadistill import diarization, identity, speaker_stability, speakers, speech_names, visual_speakers  # noqa: E402


def ob(sid, name, kind, a, b):
    return {"speaker": sid, "name": name, "kind": kind, "start": a, "end": b}


class Timeline(unittest.TestCase):
    def test_overlapping_windows_are_tiled_so_a_speaker_change_is_not_overlap(self):
        wins = [(i * 0.75, i * 0.75 + 1.5) for i in range(12)]            # 1.5 s windows, 0.75 s hop, as the voice model uses
        labels = ["a"] * 6 + ["b"] * 6
        tl = diarization.timeline_from_windows(wins, labels, 0.75)
        self.assertEqual([t["speaker"] for t in tl], ["a", "b"])
        self.assertLessEqual(tl[0]["end"], tl[1]["start"] + 1e-9)          # no two voices at once
        words = [{"word": " w", "start": 1.0, "end": 1.4}, {"word": " v", "start": 6.5, "end": 6.9}]
        ctx, diag = speaker_stability.annotate(words, tl)
        self.assertEqual(ctx, ["S1", "S2"])
        self.assertNotEqual(words[0]["attribution"]["status"], "overlap")

    def test_assignment_reports_overlap_and_partial_evidence(self):
        tl = [{"start": 0, "end": 4, "speaker": "a"}, {"start": 3, "end": 6, "speaker": "b"}]
        self.assertEqual(diarization.assignment(1, 2, tl)["speaker"], "a")
        self.assertEqual(diarization.assignment(3.2, 3.8, tl)["status"], "overlap")
        self.assertEqual(diarization.assignment(10, 11, tl)["status"], "no_voice_evidence")
        self.assertEqual(diarization.assignment(3.7, 5.0, tl)["speaker"], "b")                  # a covers only 23% of it: not a competitor
        self.assertEqual(diarization.assignment(5.0, 7.5, tl)["status"], "partial")             # b covers 40% of it: too little for a speaker

    def test_indexed_lookup_gives_the_same_answer_as_the_plain_one(self):
        tl = [{"start": i * 2.0, "end": i * 2.0 + 1.8, "speaker": "a" if i % 3 else "b"} for i in range(300)]
        for a in (0.5, 37.2, 301.0, 599.0):
            self.assertEqual(diarization.assignment(a, a + 0.4, tl), diarization.assignment(a, a + 0.4, tl[:63] + tl[63:]))

    def test_smoothing_never_fills_unresolved_words_with_a_neighbour(self):
        self.assertEqual(speakers._smooth(["S1", "S1", "S1", None, None, "S1", "S1", "S1"]), ["S1", "S1", "S1", None, None, "S1", "S1", "S1"])
        self.assertEqual(speakers._smooth(["S1", "S1", "S1", "S2", "S1", "S1", "S1"]), ["S1"] * 7)       # a flicker still goes

    def test_turn_summary(self):
        turns = [{"speaker": "S1", "words": [{"attribution": {"speaker": "S1"}}, {"attribution": {"speaker": None}}]},
                 {"speaker": "S2", "words": [{"attribution": {"speaker": "S2"}}]}, {"speaker": None, "words": [{"attribution": {"speaker": None}}]}]
        speakers.attribution_summary(turns)
        self.assertEqual([t["attribution"]["status"] for t in turns], ["contextual", "supported", "unresolved"])
        self.assertEqual(turns[0]["attribution"]["uncertain_word_count"], 1)


class Evidence(unittest.TestCase):
    KNOWN = ["Marco Ellison", "Tobbe Bergström"]

    def obs(self, text, sid="S1", speaker_turns=None):
        turns = speaker_turns or [{"speaker": sid, "start": 10.0, "end": 14.0, "text": text}]
        return speech_names.intro_observations(turns, self.KNOWN)

    def test_first_person_introductions_count_once(self):
        for text in ("Hi all, my name is Marco and I work with the platform team", "I'm Dalia from the design team.", "Sam here, thanks for joining",
                     "This is Jason."):
            o = self.obs(text)
            self.assertTrue(o and o[0]["kind"] == "self_introduction_first_person", text)

    def test_this_is_x_inside_a_longer_turn_is_weak(self):
        o = self.obs("So we want to present the people on the panel today and this is Thuvjorn, he will talk about the field work")
        self.assertTrue(o and all(x["kind"] == "self_introduction" for x in o))

    def test_a_degree_adverb_is_never_a_name(self):
        self.assertEqual(self.obs("this is quite"), [])

    def test_names_are_completed_to_the_full_name_on_screen(self):
        o = self.obs("my name is Marco")
        self.assertEqual(o[0]["name"], "Marco Ellison")

    def test_called_by_name_is_an_observation_with_a_time(self):
        turns = [{"speaker": "S1", "start": 0, "end": 5, "text": "that was the report, Tobbe Bergström, anything to add?"},
                 {"speaker": "S2", "start": 5.5, "end": 9, "text": "Yes, one thing."}]
        o = speech_names.address_observations(turns, self.KNOWN)
        self.assertEqual([(x["speaker"], x["name"], x["kind"]) for x in o], [("S2", "Tobbe Bergström", "addressed")])

    def test_exclusive_lit_label_becomes_highlight_observations(self):
        turns = [{"speaker": "S1", "start": 0, "end": 10, "text": "x"}, {"speaker": "S2", "start": 10, "end": 20, "text": "y"}]
        series = {"Ann": {t: 1.0 for t in range(0, 10)}, "Bo": {t: 1.0 for t in range(10, 20)}}
        o = visual_speakers.highlight_observations(turns, series)
        self.assertEqual({(x["speaker"], x["name"]) for x in o}, {("S1", "Ann"), ("S2", "Bo")})
        both = {"Ann": {t: 1.0 for t in range(0, 20)}, "Bo": {t: 1.0 for t in range(0, 20)}}      # two lit at once: not who speaks
        self.assertEqual(visual_speakers.highlight_observations(turns, both), [])


class Naming(unittest.TestCase):
    def test_motion_alone_never_names_a_voice(self):
        # the second video: two presenters in one room camera, names only from weak tile movement
        r = identity.resolve(["S1", "S2"], [ob("S1", "Stefan Hartwig", "tile_motion", 0, 60), ob("S2", "Johan A", "tile_motion", 60, 120)])
        self.assertTrue(all(v["name"] is None for v in r.values()))
        self.assertTrue(all(v["identity_status"] == "anonymous" for v in r.values()))

    def test_one_clear_introduction_is_enough_and_a_weak_second_name_does_not_block_it(self):
        r = identity.resolve(["S1"], [ob("S1", "Anders", "self_introduction_first_person", 5, 8),
                                      ob("S1", "Thuvjorn", "self_introduction", 300, 303), ob("S1", "Thuvjorn", "self_introduction", 310, 313)])
        self.assertEqual(r["S1"]["name"], "Anders")

    def test_a_weak_repeat_needs_three_observations_and_six_seconds(self):
        one = [ob("S1", "Ann", "self_introduction", 5, 8)]
        self.assertIsNone(identity.resolve(["S1"], one)["S1"]["name"])
        three = [ob("S1", "Ann", "self_introduction", 5, 8), ob("S1", "Ann", "self_introduction", 30, 33), ob("S1", "Ann", "self_introduction", 60, 63)]
        self.assertEqual(identity.resolve(["S1"], three)["S1"]["name"], "Ann")

    def test_one_label_lit_for_two_voices_is_a_shared_camera_not_a_person(self):
        runs = [ob("S1", "Stefan Hartwig", "highlight", t, t + 3) for t in (0, 20, 40)] + [ob("S2", "Stefan Hartwig", "highlight", t, t + 3) for t in (10, 30, 50)]
        r = identity.resolve(["S1", "S2"], runs)
        self.assertTrue(all(v["name"] is None and v["identity_status"] == "conflict" and v["candidate_name"] == "Stefan Hartwig" for v in r.values()))

    def test_captions_are_authoritative(self):
        r = identity.resolve(["S1"], [ob("S1", "Alex Leaver", "captions", 0, 2), ob("S1", "Someone", "self_introduction", 5, 8)])
        self.assertEqual(r["S1"]["name"], "Alex Leaver")


class SharedCamera(unittest.TestCase):
    """The second video: two presenters in one room camera whose tile is labelled with the room account."""

    def turns(self):
        out = []
        for k in range(8):                                        # S1 and S2 take turns for 20 s each, 5 s of silence between
            out.append({"speaker": "S1" if k % 2 == 0 else "S2", "start": k * 25.0, "end": k * 25.0 + 20.0, "text": "x"})
        return out

    def test_a_label_lit_while_people_talk_makes_both_voices_one_shared_camera(self):
        from datadistill import highlight
        total = 200
        lit = {t for tr in self.turns() for t in range(int(tr["start"]), int(tr["end"]))} | {21, 22}      # a little lit in the silence too
        series = {"Stefan Hartwig": {t: 1.0 for t in lit}, "Johan A": {5: 1.0, 6: 1.0}}
        dom = highlight.dominant_label(series, total, min_share=0.6)
        self.assertEqual(dom[0], "Stefan Hartwig")
        cam = visual_speakers.shared_camera(self.turns(), dom[1], total)
        self.assertEqual(set(cam["voices"]), {"S1", "S2"})
        self.assertGreater(cam["lit_in_speech"] - cam["lit_in_silence"], 0.25)

    def test_a_label_that_is_lit_all_the_time_is_a_background_not_a_camera(self):
        always = set(range(200))
        self.assertIsNone(visual_speakers.shared_camera(self.turns(), always, 200))                      # no difference speech / silence

    def test_two_labels_taking_turns_is_not_a_dominant_label(self):
        from datadistill import highlight
        series = {"A": {t: 1.0 for t in range(0, 100)}, "B": {t: 1.0 for t in range(100, 200)}}
        self.assertIsNone(highlight.dominant_label(series, 200))

    def test_one_voice_through_a_camera_is_reported_per_voice(self):
        turns = self.turns()
        lit = {t for tr in turns if tr["speaker"] == "S1" for t in range(int(tr["start"]), int(tr["end"]))}
        cam = visual_speakers.shared_camera(turns, lit, 200, min_in_speech=0.4)
        self.assertEqual(set(cam["voices"]), {"S1"})

    def test_a_line_is_said_through_the_camera_when_it_was_lit_for_most_of_it(self):
        lit = set(range(10, 20))
        self.assertTrue(visual_speakers.camera_of_turn({"start": 10.0, "end": 18.0}, lit))
        self.assertFalse(visual_speakers.camera_of_turn({"start": 15.0, "end": 30.0}, lit))


if __name__ == "__main__":
    unittest.main()
