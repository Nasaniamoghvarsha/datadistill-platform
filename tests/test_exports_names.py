"""Slide changes in the exports, the ASCII export, OCR-variant names and nicknames (no models, no video)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

sys.path.insert(0, str(Path(__file__).parent))
import _isolated  # noqa: E402,F401  (temporary library, before datadistill is imported)

from datadistill import namefold, package, screen_names  # noqa: E402


def screen(idx, uid, kind="slide", start=0.0):
    return {"idx": idx, "slide_uid": uid, "kind": kind, "t_start": start, "t_end": start + 10}


def turn(i, slide_idx, start):
    return {"idx": i, "start": start, "end": start + 2, "slide_idx": slide_idx}


class SlideChanges(unittest.TestCase):
    def test_same_slide_is_not_a_change(self):
        d = {"screens": [screen(0, "u1"), screen(1, "u1"), screen(2, "u2")],
             "turns": [turn(0, 0, 1), turn(1, 0, 4), turn(2, 1, 8), turn(3, 2, 12)]}
        ch = package.slide_changes(d)
        # screen 1 is only another detection of slide u1: no change at turn 2; the next real change is at turn 3
        self.assertEqual([(i, s["slide_uid"]) for i, s in ch], [(0, "u1"), (3, "u2")])

    def test_going_back_to_a_slide_is_a_change(self):
        d = {"screens": [screen(0, "a"), screen(1, "b"), screen(2, "a")],
             "turns": [turn(0, 0, 1), turn(1, 1, 5), turn(2, 2, 9)]}
        self.assertEqual([i for i, _ in package.slide_changes(d)], [0, 1, 2])

    def test_no_slide_between_two_slides_shows_nothing(self):
        d = {"screens": [screen(0, "a"), screen(1, None, "non_slide")], "turns": [turn(0, 0, 1), turn(1, 1, 5), turn(2, 1, 9)]}
        self.assertEqual([i for i, _ in package.slide_changes(d)], [0])

    def test_screens_without_an_id_are_told_apart_by_number(self):
        d = {"screens": [screen(0, None), screen(1, None)], "turns": [turn(0, 0, 1), turn(1, 1, 5)]}
        self.assertEqual(len(package.slide_changes(d)), 2)


class Ascii(unittest.TestCase):
    def test_accents_and_nordic_letters(self):
        self.assertEqual(package.to_ascii("Hägglund Björn Ångström Søren"), "Hagglund Bjorn Angstrom Soren")
        self.assertEqual(package.to_ascii("Æble Straße œuvre"), "AEble Strasse oeuvre")

    def test_typography(self):
        self.assertEqual(package.to_ascii("“quoted” it’s – a — b… • c"), '"quoted" it\'s - a -- b... * c')

    def test_unknown_characters_become_question_marks_and_output_is_ascii(self):
        out = package.to_ascii("日本 ok")
        self.assertEqual(out, "?? ok")
        self.assertTrue(out.isascii())


class OcrVariants(unittest.TestCase):
    def samples(self, names):
        return [{"t": float(i), "labels": [{"name": n}]} for i, n in enumerate(names)]

    def test_blurred_surname_endings_are_one_person(self):
        s = self.samples(["Torbjörn Bergström"] * 4 + ["Torbjörn Bergstrem", "Torbjörn Bergstrim"])
        mapping, seen = screen_names.canonical_names(s)
        self.assertEqual({mapping[n] for n in ("Torbjörn Bergström", "Torbjörn Bergstrem", "Torbjörn Bergstrim")}, {"Torbjörn Bergström"})
        self.assertEqual(seen["Torbjörn Bergström"], 6)

    def test_same_first_name_different_people_stay_apart(self):
        s = self.samples(["Jason Bowsza"] * 3 + ["Jason Hayes"] * 3)
        mapping, _ = screen_names.canonical_names(s)
        self.assertNotEqual(mapping["Jason Bowsza"], mapping["Jason Hayes"])

    def test_different_first_names_stay_apart(self):
        self.assertFalse(screen_names._ocr_variant("Anders Lindqvist", "Anton Lindqvist"))


class Nicknames(unittest.TestCase):
    known = ["Torbjörn Bergström", "Stefan Hartwig", "Karin Berg"]

    def test_swedish_short_form(self):
        self.assertEqual(namefold.match_known("Tobbe", self.known), "Torbjörn Bergström")

    def test_english_short_form(self):
        self.assertEqual(namefold.match_known("Steve", ["Stephen Hale", "Anna Berg"]), "Stephen Hale")

    def test_two_people_for_one_nickname_is_not_guessed(self):
        self.assertIsNone(namefold.match_known("Kalle", ["Karl Persson", "Carl Nilsson"]))

    def test_unrelated_word_matches_nothing(self):
        self.assertIsNone(namefold.match_known("Table", self.known))


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------- documents: pictures, page comparison
import time  # noqa: E402

from datadistill import db, dedup  # noqa: E402

db.init()


def add_doc(name, pages, sha=None, kind="document"):
    fid = db.ex("INSERT INTO files(system,path,name,kind,size,sha256,status,meta,added_at) VALUES (?,?,?,?,?,?,?,?,?)",
                ("S", f"S/{name}", name, kind, 10, sha or name, "processed", "{}", time.time()))
    for n, (uid, text) in enumerate(pages, 1):
        db.ex("INSERT INTO pages(file_id,page,slide_uid,text,text_norm) VALUES (?,?,?,?,?)", (fid, n, uid, text, text.lower()))
    return fid


class Compare(unittest.TestCase):
    def rows(self, res):
        return [(r["state"], r["a_page"], r["b_page"]) for r in res["rows"]]

    def test_same_edited_removed_added_moved(self):
        a = add_doc("cmp_a.pdf", [("u1", "agenda for the week"), ("u2", "budget is 40 million for the network"), ("u3", "risks"),
                                  ("u4", "closing remarks and thanks to everyone"), ("u5", "appendix")])
        b = add_doc("cmp_b.pdf", [("u1", "agenda for the week"), ("x2", "budget is 45 million for the network"), ("u5", "appendix"),
                                  ("u3", "risks"), ("n1", "a completely new page about hiring plans")])
        res = dedup.compare_documents(a, b)
        got = {(s, ap, bp) for s, ap, bp in self.rows(res)}
        self.assertIn(("same", 1, 1), got)
        self.assertIn(("edited", 2, 2), got)                          # 40 -> 45 million: the same page, edited
        self.assertTrue(any(s == "moved" for s, _, _ in got))         # risks / appendix swapped places
        self.assertIn(("only_a", 4, None), got)                       # closing remarks are gone
        self.assertIn(("only_b", None, 5), got)                       # the hiring page is new
        self.assertEqual(res["counts"]["edited"], 1)
        self.assertEqual(res["counts"]["only_a"], 1)
        self.assertEqual(res["counts"]["only_b"], 1)

    def test_identical_documents(self):
        pages = [("i1", "one page"), ("i2", "two page")]
        a, b = add_doc("same_a.pdf", pages, sha="x"), add_doc("same_b.pdf", pages, sha="x")
        res = dedup.compare_documents(a, b)
        self.assertTrue(res["identical_files"])
        self.assertEqual(res["same_pct"], 100)

    def test_a_video_cannot_be_compared(self):
        v = add_doc("cmp_v.mp4", [], kind="video")
        d = add_doc("cmp_d.pdf", [("z1", "text")])
        with self.assertRaises(ValueError):
            dedup.compare_documents(v, d)

    def test_family_lists_removed_pages(self):
        base = [("f1", "alpha beta"), ("f2", "gamma delta"), ("f3", "epsilon zeta")]
        a = add_doc("fam_v1.pptx", base)
        b = add_doc("fam_v2.pptx", [base[0], base[2], ("f4", "eta theta")])
        fams = [f for f in dedup.families() if {v["id"] for v in f["versions"]} >= {a, b}]
        self.assertEqual(len(fams), 1)
        v2 = next(v for v in fams[0]["versions"] if v["id"] == b)
        self.assertEqual(v2["removed"], [2])


class Pictures(unittest.TestCase):
    def test_picture_files_are_documents(self):
        from pathlib import Path as P

        from datadistill import library
        for n in ("a.png", "b.JPG", "c.webp", "d.tiff"):
            self.assertEqual(library.kind_of(P(n)), "document")
        self.assertEqual(library.kind_of(P("a.mp4")), "video")
        self.assertEqual(library.kind_of(P("a.zip")), "other")

    def test_old_picture_rows_become_documents(self):
        fid = db.ex("INSERT INTO files(system,path,name,kind,size,sha256,status,meta,added_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    ("S", "S/old shot.PNG", "old shot.PNG", "other", 5, "h", "processed", "{}", time.time()))
        db.init()
        r = db.file_row(fid)
        self.assertEqual((r["kind"], r["status"]), ("document", "new"))

    def test_a_picture_is_read_as_one_page_and_matches_its_copy(self):
        from PIL import Image, ImageDraw

        from datadistill import docs
        from datadistill.config import DATA_DIR
        d = DATA_DIR / "S"
        d.mkdir(parents=True, exist_ok=True)
        for name in ("shot1.png", "shot2.png"):                       # the same screenshot saved twice (different file names)
            im = Image.new("RGB", (900, 500), "white")
            ImageDraw.Draw(im).text((60, 200), "NETWORK ROLLOUT PLAN FOR Q3", fill="black")
            im.save(d / name)
        ids = []
        for name in ("shot1.png", "shot2.png"):
            ids.append(db.ex("INSERT INTO files(system,path,name,kind,size,sha256,status,meta,added_at) VALUES (?,?,?,?,?,?,?,?,?)",
                             ("S", f"S/{name}", name, "document", 1, name, "new", "{}", time.time())))
        info = [docs.process_document(i) for i in ids]
        self.assertEqual([x["pages"] for x in info], [1, 1])
        uids = [db.q1("SELECT slide_uid FROM pages WHERE file_id=?", (i,))["slide_uid"] for i in ids]
        self.assertTrue(uids[0] and uids[0] == uids[1])               # one stored slide for both pictures


class Disposition(unittest.TestCase):
    def test_header_survives_any_file_name(self):
        from datadistill import api
        h = api._disp("Möte med Åsa – “Q3”.zip")["Content-Disposition"]
        h.encode("latin-1")                                            # an HTTP header must be latin-1
        self.assertIn("filename*=UTF-8''M%C3%B6te", h)
        self.assertIn('filename="Mote med Asa - "Q3".zip"'.replace('"Q3"', "\"Q3\"".replace('"', "'")), h)
        self.assertTrue(h.startswith("attachment;"))
        self.assertTrue(api._disp("x.pdf", inline=True)["Content-Disposition"].startswith("inline;"))


class QuarantineDeleteBody(unittest.TestCase):
    def test_the_page_sends_a_json_object_and_it_is_accepted(self):
        """The Quarantine page posts {"typed": name}. The endpoint used to expect a bare string, so "Delete now" always failed with 422."""
        from fastapi.testclient import TestClient

        from datadistill import api
        r = TestClient(api.app).post("/api/m365/quarantine/999999/delete", json={"typed": "x.mp4"})
        self.assertEqual(r.status_code, 409)                          # understood, refused (unknown item), not "unprocessable"


class PeopleSearch(unittest.TestCase):
    """Searching a name gives the person: everything they said, per recording."""

    @classmethod
    def setUpClass(cls):
        import json as _j
        from datadistill import people
        cls.people = people
        fid = db.ex("INSERT INTO files(system,path,name,kind,size,sha256,status,meta,added_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    ("P", "P/ppl.mp4", "ppl.mp4", "video", 1, "ppl", "processed", "{}", time.time()))
        meta = {"S1": {"name": "Maria Lindqvist", "talk_time_s": 12.0}, "S2": {"name": "Maria Santos", "talk_time_s": 5.0}, "S3": {"talk_time_s": 3.0}}
        db.ex("INSERT INTO recordings(file_id, manifest, speakers, participants) VALUES (?,?,?,?)", (fid, "{}", _j.dumps(meta), "[]"))
        for i, (sp, tx) in enumerate([("S1", "the budget is approved"), ("S2", "thank you Maria Lindqvist"), ("S1", "next we hire"), ("S3", "ok")]):
            db.ex("INSERT INTO segments(file_id, idx, start, end, speaker, text, slide_idx) VALUES (?,?,?,?,?,?,?)", (fid, i, i * 5.0, i * 5.0 + 4, sp, tx, None))
        cls.fid = fid

    def test_a_full_name_finds_one_person_with_their_lines(self):
        r = self.people.find("maria lindqvist")
        self.assertEqual([p["name"] for p in r], ["Maria Lindqvist"])
        self.assertEqual(r[0]["lines"], 2)

    def test_a_first_name_shared_by_two_people_finds_both(self):
        self.assertEqual({p["name"] for p in self.people.find("maria")}, {"Maria Lindqvist", "Maria Santos"})

    def test_a_small_typo_still_finds_the_person(self):
        self.assertEqual([p["name"] for p in self.people.find("lindqwist")], ["Maria Lindqvist"])

    def test_something_that_is_not_a_name_finds_nobody(self):
        self.assertEqual(self.people.find("budget"), [])

    def test_unnamed_voices_are_found_only_when_typed_exactly(self):
        self.assertEqual(self.people.find("speaker 3")[0]["name"], "Speaker 3")
        self.assertEqual(self.people.find("speak"), [])

    def test_detail_has_only_this_persons_lines_and_what_others_say_about_them(self):
        d = self.people.detail("Maria Lindqvist", self.fid)
        self.assertEqual([ln["text"] for ln in d["lines"]], ["the budget is approved", "next we hire"])
        self.assertEqual([m["text"] for m in d["mentions"]], ["thank you Maria Lindqvist"])
