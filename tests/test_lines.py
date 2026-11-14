"""Repeated text: units, normalisation, footers and cross-references (no database: index() is fed directly)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

sys.path.insert(0, str(Path(__file__).parent))
import _isolated  # noqa: E402,F401  (temporary library, before datadistill is imported)

from datadistill import lines  # noqa: E402


class Units(unittest.TestCase):
    def test_same_text_after_normalising(self):
        a = lines.units("• Next steps and owners")[0][1]
        b = lines.units("NEXT STEPS AND OWNERS.")[0][1]
        self.assertEqual(lines.line_id(a), lines.line_id(b))

    def test_numbers_must_match(self):
        a = lines.units("42 sites upgraded")[0][1]
        b = lines.units("47 sites upgraded")[0][1]
        self.assertNotEqual(a, b)

    def test_short_fragments_skipped(self):
        self.assertEqual(lines.units("Agenda\n2/6\n116"), [])

    def test_long_lines_split_into_sentences(self):
        u = lines.units("The board approved the budget order for next year. Reports are due on July 1, 2027 for review.")
        self.assertEqual(len(u), 2)


class Index(unittest.TestCase):
    def setUp(self):
        docs = [{"id": 1, "name": "v1.pptx", "system": "A", "sha256": "a"}, {"id": 2, "name": "v2.pptx", "system": "B", "sha256": "b"},
                {"id": 3, "name": "copy.pptx", "system": "C", "sha256": "a"}]
        foot = "\nDoc 1/1551 Rev A Ericsson Internal"
        pages = [{"file_id": 1, "page": i, "text": t + foot} for i, t in enumerate(["Quarterly network review", "Risks and mitigations", "Next steps and owners"], 1)]
        pages += [{"file_id": 2, "page": 1, "text": "Risks and mitigations\nSupplier delay solved"}]
        pages += [{"file_id": 3, "page": 1, "text": "Quarterly network review"}]

        def q(sql, params=()):
            if "FROM files" in sql:
                return docs
            if "file_id=?" in sql:
                return [p for p in pages if p["file_id"] == params[0]]
            return [p for p in pages if p["file_id"] in params]
        self._q = lines.db.q
        lines.db.q = q

    def tearDown(self):
        lines.db.q = self._q

    def test_cross_references(self):
        idx = lines.index()
        pl = lines.page_lines(2, idx)
        risk = [x for x in pl[1] if x["text"] == "Risks and mitigations"][0]
        self.assertEqual([(o["file_id"], o["page"]) for o in risk["also_in"]], [(1, 2)])

    def test_exact_copies_not_counted(self):
        r = lines.repeated(lines.index())
        texts = [g["text"] for g in r["groups"]]
        self.assertNotIn("Quarterly network review", texts)   # only in v1 and its byte-identical copy
        self.assertIn("Risks and mitigations", texts)

    def test_footer_separate(self):
        r = lines.repeated(lines.index())
        self.assertEqual([g["text"] for g in r["footers"]], ["Doc 1/1551 Rev A Ericsson Internal"])


if __name__ == "__main__":
    unittest.main()
