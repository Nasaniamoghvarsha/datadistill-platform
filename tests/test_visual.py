"""Tests of the 13 Nov additions: name folding, label highlight, camera matching, overlap, diagrams. No models needed."""
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

sys.path.insert(0, str(Path(__file__).parent))
import _isolated  # noqa: E402,F401  (temporary library, before datadistill is imported)

from datadistill import diagrams, highlight, namefold, visual_speakers  # noqa: E402


class NameFold(unittest.TestCase):
    KNOWN = ["Marco Ellison", "Stefan Hartwig", "Torbjörn Bergström", "Hélène Dubois", "Åsa Lindqvist"]

    def test_accents_fold(self):
        self.assertEqual(namefold.fold("Åsa Lindqvist"), "asa lindqvist")
        self.assertEqual(namefold.fold("Hélène Çelik Ørsted"), "helene celik orsted")

    def test_heard_name_matches_the_known_spelling(self):
        self.assertEqual(namefold.match_known("Thuvjorn", self.KNOWN), "Torbjörn Bergström")
        self.assertEqual(namefold.match_known("Asa", self.KNOWN), "Åsa Lindqvist")
        self.assertEqual(namefold.match_known("Helene", self.KNOWN), "Hélène Dubois")

    def test_unknown_name_and_close_pairs_are_not_guessed(self):
        self.assertIsNone(namefold.match_known("Mark", self.KNOWN))
        # two similar names: no winner by a clear margin -> nobody
        self.assertIsNone(namefold.match_known("Stefan", ["Stefan Berg", "Stephan Berg"]))

    def test_spelling_corrected_in_text(self):
        turns = [{"text": "Then Stephan Hartvig will join."}]
        fixes = namefold.correct_spellings(turns, self.KNOWN, lambda w: w.lower() in {"then", "will", "join"})
        self.assertEqual(turns[0]["text"], "Then Stefan Hartwig will join.")
        self.assertEqual(turns[0]["original_text"], "Then Stephan Hartvig will join.")
        self.assertEqual({f["to"] for f in fixes}, {"Stefan", "Hartwig"})

    def test_pipeline_name_resolution_uses_the_screen_spelling(self):
        from collections import Counter
        from datadistill import speech_names
        known = ["Marco Ellison", "Torbjörn Bergström", "Åsa Lindqvist"]
        self.assertEqual(speech_names.resolve_known("Asa", known), "Åsa Lindqvist")
        self.assertEqual(speech_names.resolve_known("Thuvjorn", known), "Torbjörn Bergström")
        got = speech_names.assign({"S1": Counter({"Thuvjorn": 3}), "S2": Counter({"Marco": 3})}, known)
        self.assertEqual({k: v["name"] for k, v in got.items()}, {"S1": "Torbjörn Bergström", "S2": "Marco Ellison"})


class LabelHighlight(unittest.TestCase):
    def test_active_speaker_needs_exactly_one_lit_label(self):
        series = {"Ann": {0: 1.0, 1: 1.0, 2: 0.0, 3: 0.9}, "Bo": {0: 0.0, 1: 0.7, 2: 0.0, 3: 0.0}}
        self.assertEqual(highlight.active_speaker(series), {0: "Ann", 3: "Ann"})      # second 1: both lit -> not trusted

    def _frames(self, lit, seconds=60, hue=(190, 120, 120)):
        """Two name labels; `lit` = {second: label name} for the seconds a lavender chip sits behind that label."""
        import cv2
        tmp = Path(tempfile.mkdtemp())
        frames = []
        for t in range(seconds):
            img = np.zeros((120, 400, 3), np.uint8)
            for x, name in ((20, "Ann Lee"), (220, "Bo Ek")):
                if name in (lit.get(t) if isinstance(lit.get(t), tuple) else (lit.get(t),)):
                    cv2.rectangle(img, (x - 8, 20), (x + 80, 50), hue, -1)
                cv2.putText(img, name, (x, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
            path = tmp / f"f{t:03d}.png"
            cv2.imwrite(str(path), img)
            frames.append(path)
        return frames

    SAMPLES = [{"t": 0.0, "labels": [{"name": "Ann Lee", "x": 20, "y": 25, "w": 70, "h": 18},
                                      {"name": "Bo Ek", "x": 220, "y": 25, "w": 60, "h": 18}]}]

    def test_colour_is_learned_from_labels_that_switch_on_and_off(self):
        lit = {t: "Bo Ek" for t in (8, 9, 10, 11, 20, 21, 22, 23, 40, 41, 42, 43)}
        lit.update({t: "Ann Lee" for t in (14, 15, 16, 17, 30, 31, 32, 33, 50, 51, 52, 53)})
        series, info = highlight.run(self._frames(lit), self.SAMPLES)
        self.assertIsNotNone(info["colour"], info)
        act = highlight.active_speaker(series)
        self.assertTrue(all(act.get(t) == "Bo Ek" for t in (9, 10, 21, 22, 41, 42)))
        self.assertTrue(all(act.get(t) == "Ann Lee" for t in (15, 16, 31, 32, 51, 52)))
        self.assertFalse(any(t in act for t in (0, 3, 25, 36)))

    def test_a_colour_that_is_just_there_is_not_a_speaker_highlight(self):
        # one label sits on a coloured background for the whole recording (a yellow slide, a photo): not a highlight
        series, info = highlight.run(self._frames({t: "Bo Ek" for t in range(60)}), self.SAMPLES)
        self.assertEqual(series, {})
        self.assertIsNone(info["colour"])

    def test_a_colour_that_is_learned_but_never_exclusive_is_rejected(self):
        # both labels light up together every time: that is background colour changing, not who is speaking
        both = ("Ann Lee", "Bo Ek")
        lit = {t: both for t in (8, 9, 10, 11, 20, 21, 22, 23, 40, 41, 42, 43)}
        series, info = highlight.run(self._frames(lit), self.SAMPLES)
        self.assertEqual(series, {})
        self.assertIn("rejected", info)
        self.assertIn("at once", info["rejected"])

    def test_a_label_lit_almost_all_the_time_is_reported_as_dominant_not_as_a_highlight(self):
        lit = {t: "Bo Ek" for t in range(60) if t not in (5, 6, 7, 30, 31, 55)}                  # lit 90% of the time
        lit.update({10: "Ann Lee", 11: "Ann Lee", 12: "Ann Lee"})                                  # the other label lights up rarely
        series, info = highlight.run(self._frames(lit), self.SAMPLES)
        self.assertEqual(series, {})                                                                # not a speaker highlight
        self.assertEqual(info.get("dominant"), "Bo Ek")
        self.assertGreater(len(info["dominant_series"]), 45)

    def test_plausible_needs_two_labels_that_switch_and_nobody_lit_most_of_the_time(self):
        mk = lambda ts: {t: 1.0 for t in ts}  # noqa: E731
        good = {"A": mk([5, 6, 7, 20, 21, 30, 31, 32]), "B": mk([10, 11, 12, 40, 41, 50, 51])}
        self.assertTrue(highlight.plausible(good, 60)[0])
        self.assertFalse(highlight.plausible({"A": mk(range(50)), "B": mk([55])}, 60)[0])
        self.assertFalse(highlight.plausible({"A": mk([5, 6, 7, 20, 21, 30]), "B": mk([5, 6, 7, 20, 21, 30])}, 60)[0])
        self.assertFalse(highlight.plausible({}, 60)[0])


def _act(on, n=120, seed=0):
    rng = np.random.default_rng(seed)
    return {s: float(rng.normal(0.05, 0.02) + (0.5 if s in on else 0.0)) for s in range(n)}


class Cameras(unittest.TestCase):
    def turns(self):
        out = []
        for i in range(12):                                   # S1 speaks seconds 0-9, 20-29, ..., S2 the others
            out.append({"speaker": "S1", "start": i * 20, "end": i * 20 + 10, "text": "a"})
            out.append({"speaker": "S2", "start": i * 20 + 10, "end": i * 20 + 20, "text": "b"})
        return out

    def test_voice_matches_the_tile_that_moves_with_it(self):
        s1 = {s for t in self.turns() if t["speaker"] == "S1" for s in range(int(t["start"]), int(t["end"]))}
        s2 = {s for t in self.turns() if t["speaker"] == "S2" for s in range(int(t["start"]), int(t["end"]))}
        ents = {"cam1@0": {"activity": _act(s1, 240, 1), "room": False, "tile": "cam1@0"},
                "cam2@0": {"activity": _act(s2, 240, 2), "room": False, "tile": "cam2@0"}}
        m = visual_speakers.camera_matches(self.turns(), ents)
        self.assertEqual(m["S1"]["entity"], "cam1@0")
        self.assertEqual(m["S2"]["entity"], "cam2@0")

    def test_two_voices_on_one_face_are_one_person(self):
        s_all = {s for t in self.turns() for s in range(int(t["start"]), int(t["end"]))}
        turns = [{"speaker": "S1", "start": i * 40, "end": i * 40 + 10, "text": "a"} for i in range(6)] + \
                [{"speaker": "S3", "start": i * 40 + 20, "end": i * 40 + 30, "text": "c"} for i in range(6)] + \
                [{"speaker": "S2", "start": i * 40 + 10, "end": i * 40 + 20, "text": "b"} for i in range(6)]
        on = {s for t in turns if t["speaker"] in ("S1", "S3") for s in range(int(t["start"]), int(t["end"]))}
        ents = {"cam1@0": {"activity": _act(on, 240, 3), "room": False, "tile": "cam1@0"}}
        pairs = visual_speakers.same_face_pairs(turns, ents)
        self.assertEqual({(a, b) for a, b, _ in pairs}, {("S1", "S3")})
        self.assertTrue(s_all)

    def test_overlap_from_two_lit_labels(self):
        chip = {"Ann": {5: 1.0, 6: 1.0, 7: 1.0, 8: 0.0, 12: 1.0}, "Bo": {6: 0.9, 7: 0.8, 8: 1.0, 12: 1.0}}
        secs = visual_speakers.overlap_seconds(chip)
        self.assertEqual(sorted(secs), [6, 7])                                  # second 12 alone is noise (needs 2 in a row)
        turns = [{"speaker": "S1", "speaker_name": "Ann", "start": 4, "end": 9, "text": "x"},
                 {"speaker": "S2", "speaker_name": "Cy", "start": 6, "end": 8, "text": "y"},
                 {"speaker": "S0", "speaker_name": "Music / video sound", "start": 6, "end": 8, "text": "la"},
                 {"speaker": "S3", "speaker_name": "Dee", "start": 20, "end": 25, "text": "z"}]
        self.assertEqual(visual_speakers.mark_overlap(turns, {6: ["Ann", "Bo"], 7: ["Ann", "Bo"]}), 1)
        self.assertEqual(turns[0]["overlap_with"], ["Bo"])
        self.assertTrue(all("overlap_with" not in x for x in turns[1:]))        # not one of the two, or music: never marked

    def test_mouths_of_photos_do_not_count_as_overlap(self):
        act = {s: 0.9 for s in range(10, 16)}
        tiles = {"A": {"kind": "live", "motion": 3.0, "name": "A", "activity": act},
                 "B": {"kind": "photo", "motion": 0.5, "name": "B", "activity": act},
                 "C": {"kind": "live", "motion": 0.9, "name": "C", "activity": act}}
        self.assertEqual(visual_speakers.overlap_seconds({}, tiles), {})
        tiles["B"] = {"kind": "live", "motion": 2.5, "name": "B", "activity": act}
        self.assertEqual(sorted(visual_speakers.overlap_seconds({}, tiles)), list(range(10, 16)))


class Tiles(unittest.TestCase):
    def test_a_small_photo_is_a_photo_even_if_the_face_reading_jitters(self):
        from collections import defaultdict

        from datadistill import faces
        rng = np.random.default_rng(1)

        def tile(motion, jaws):
            return {"name": "X", "samples": {s: [float(j)] * 4 for s, j in enumerate(jaws)}, "faces": defaultdict(list),
                    "motion": {s: [motion] for s in range(len(jaws))}, "box_w": [150] * len(jaws), "n_frames": len(jaws),
                    "n_face": len(jaws), "multi": 0}
        jitter = rng.uniform(0, 0.2, 200)                      # a photo whose jawOpen reading wobbles between 0 and 0.2
        out = faces.finish({"photo": tile(0.56, jitter), "cam": tile(3.2, jitter)}, 200)["tiles"]
        self.assertEqual(out["photo"]["kind"], "photo")
        self.assertEqual(out["cam"]["kind"], "live")


class Diagrams(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "benchmarks" / "diagrams"))
        import make_test_diagrams as m
        cls.m = m
        m.OUT = Path(tempfile.mkdtemp())
        cls.cases = m.CASES
        for name, (title, boxes, edges) in m.CASES.items():
            m.make_pdf(name, title, boxes, edges)
            m.make_pptx(name, title, boxes, edges)

    def _edges(self, d):
        label = {n["id"]: n["label"] for n in d["nodes"]}
        out = {(label[e["from"]], label[e["to"]]) for e in d["edges"]}
        out |= {(label[e["to"]], label[e["from"]]) for e in d["edges"] if not e["directed"]}     # a plain line has no direction
        return out

    def _truth(self, name):
        _, boxes, edges = self.cases[name]
        out = set()
        for a, b, lab, st in edges:
            out.add((boxes[a][0], boxes[b][0]))
            if st == "<->" or st == "--":
                out.add((boxes[b][0], boxes[a][0]))
        return out

    def test_pptx_connectors(self):
        from pptx import Presentation
        for name in self.cases:
            d = diagrams.from_pptx_slide(Presentation(str(self.m.OUT / f"{name}.pptx")).slides[0])
            self.assertTrue(self._truth(name) <= self._edges(d), name)
            self.assertEqual(len(d["nodes"]), len(self.cases[name][1]))

    def test_pdf_vector_geometry(self):
        import pdfplumber
        for name in self.cases:
            with pdfplumber.open(self.m.OUT / f"{name}.pdf") as pdf:
                d = diagrams.from_pdf_page(pdf.pages[0])
            self.assertTrue(self._truth(name) <= self._edges(d), name)
        self.assertIn("flowchart", diagrams.from_pdf_page(pdfplumber.open(self.m.OUT / "flowchart.pdf").pages[0])["mermaid"])

    def test_a_table_is_not_a_diagram(self):
        import pdfplumber
        from reportlab.lib import colors
        from reportlab.pdfgen import canvas
        from reportlab.platypus import Table, TableStyle
        path = self.m.OUT / "table.pdf"
        c = canvas.Canvas(str(path), pagesize=(720, 405))
        t = Table([["Section", "Topic", "Description"], ["Chapter 1", "Delivering learning", "How learning emerges"],
                   ["Chapter 2", "Enabling strategies", "What makes the model work"]], colWidths=[120, 200, 300])
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 1, colors.black), ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey)]))
        t.wrapOn(c, 600, 300)
        t.drawOn(c, 50, 200)
        c.showPage()
        c.save()
        with pdfplumber.open(path) as pdf:
            self.assertIsNone(diagrams.from_pdf_page(pdf.pages[0]))

    def test_vision_answer_is_checked_against_the_slide_text(self):
        raw = {"nodes": [{"label": "Browser"}, {"label": "API Gateway"}, {"label": "Database"}, {"label": "Quantum Cache"}],
               "edges": [{"from": "Browser", "to": "API Gateway", "label": "HTTPS"}, {"from": "API Gateway", "to": "Database", "label": "SQL"},
                         {"from": "API Gateway", "to": "Quantum Cache"}]}
        d = diagrams.check(raw, "Web architecture Browser HTTPS API Gateway SQL Database", "vision")
        self.assertEqual([n["label"] for n in d["nodes"]], ["Browser", "API Gateway", "Database"])    # the invented box is dropped
        self.assertEqual(len(d["edges"]), 2)
        self.assertEqual(d["dropped"], 2)                                                           # the box and the arrow to it
        self.assertIn("Browser -> API Gateway (HTTPS)", d["text"])

    def test_a_text_slide_does_not_become_a_diagram_by_vision(self):
        text = "Global Ericsson Program 140 Volunteers 30 Countries 29 Languages Technology Partners"
        raw = {"nodes": [{"label": "Global Ericsson Program"}, {"label": "140 Volunteers"}],
               "edges": [{"from": "Global Ericsson Program", "to": "140 Volunteers"}]}
        self.assertIsNone(diagrams.check(raw, text, "vision"))                      # 2 boxes, 1 arrow: not a diagram
        raw = {"nodes": [{"label": "Technology"}, {"label": "Partners"}, {"label": "Countries"}, {"label": "Languages"}],
               "edges": [{"from": "Technology", "to": "Partners"}, {"from": "Partners", "to": "Technology"}]}
        self.assertIsNone(diagrams.check(raw, text, "vision"))                      # two of four boxes joined: not enough


if __name__ == "__main__":
    unittest.main()


class AvatarsInTheSamples(unittest.TestCase):
    def test_camera_off_initials_do_not_break_the_highlight_search(self):
        """A recording with a camera-off participant (initials in a circle) crashed with KeyError: 'x' after the voices were found."""
        from datadistill import highlight
        samples = [{"t": 0.0, "labels": [{"name": "Anna Berg", "x": 100, "y": 50, "w": 120, "h": 20, "box": [0, 0, 1, 1]},
                                         {"name": "@KH", "box": [5, 5, 50, 50], "W": 1920, "H": 1080, "avatar": True}]}]
        sl = highlight.slots(samples)
        self.assertEqual([s["name"] for s in sl], ["Anna Berg"])
