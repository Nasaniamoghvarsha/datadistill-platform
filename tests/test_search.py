"""Search: how a query is understood (speaker questions in three languages, accents, misspelt words). No models needed."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).parent))
import _isolated  # noqa: E402,F401

from datadistill import search  # noqa: E402

SPEAKERS = ["Lisa Thomas", "Matt Hart", "Åsa Lindqvist", "Torbjörn Bergström", "Speaker 7"]


class Understand(unittest.TestCase):
    def test_what_did_x_say_about_y(self):
        self.assertEqual(search.understand("what did Matt Hart say about housing", SPEAKERS), ("housing", "Matt Hart"))
        self.assertEqual(search.understand("What did Lisa mention about the minutes?", SPEAKERS), ("the minutes", "Lisa Thomas"))

    def test_swedish_and_french_questions(self):
        self.assertEqual(search.understand("vad sa Åsa om budgeten", SPEAKERS), ("budgeten", "Åsa Lindqvist"))
        self.assertEqual(search.understand("vad sa Asa om budgeten", SPEAKERS)[1], "Åsa Lindqvist")          # accents ignored
        self.assertEqual(search.understand("qu'a dit Lisa sur le budget", SPEAKERS), ("le budget", "Lisa Thomas"))

    def test_name_colon_query(self):
        self.assertEqual(search.understand("Torbjörn: satellites", SPEAKERS), ("satellites", "Torbjörn Bergström"))

    def test_a_name_that_is_not_a_speaker_is_just_part_of_the_query(self):
        q = "what did Napoleon say about housing"
        self.assertEqual(search.understand(q, SPEAKERS), (q, None))
        self.assertEqual(search.understand("housing costs", SPEAKERS), ("housing costs", None))

    def test_close_spelling_of_a_speaker_name(self):
        self.assertEqual(search.understand("what did Thuvjorn say about satellites", SPEAKERS), ("satellites", "Torbjörn Bergström"))


class Words(unittest.TestCase):
    def test_tokens_ignore_accents(self):
        self.assertEqual(search.tok("Åsa Lindqvist på Ericsson, François"), ["asa", "lindqvist", "pa", "ericsson", "francois"])

    def test_unknown_words_are_extended_by_close_ones(self):
        vocab = ["renewable", "energy", "siting", "research", "facilities"]
        toks, added = search.expand(["renewible", "energy", "siteing"], vocab)
        self.assertIn("renewable", toks)
        self.assertIn("siting", toks)
        self.assertEqual(set(added), {"renewible", "siteing"})
        self.assertEqual(search.expand(["energy"], vocab), (["energy"], {}))             # a known word is left alone



class NoAnswerFloor(unittest.TestCase):
    """"jvm" is in no document: the semantic list used to fill the Documents tab with the nearest unrelated slides."""

    def setUp(self):
        import numpy as np
        from rank_bm25 import BM25Okapi
        texts = ["SD-WAN orchestration blogs", "FWA is an access technology", "Next steps and owners"]
        self.items = [{"type": "page", "name": f"deck{i}.pptx", "folder": "A", "file_id": i, "slide_uid": f"u{i}", "text": t,
                       "lines": [], "slide_text": t, "_tok": search.tok(t)} for i, t in enumerate(texts)]
        class M:                                   # every text is vaguely near every query: cosine 0.4 for all
            def encode(self, x, **k):
                return np.ones((len(x), 3)) / np.sqrt(3)
        self.M = M
        self.idx = {"items": self.items, "bm25": BM25Okapi([i["_tok"] for i in self.items]),
                    "emb": np.ones((3, 3)) / np.sqrt(3) * 0.4 ** 0.5, "vocab": sorted({w for i in self.items for w in i["_tok"]}), "speakers": []}
        self._saved = (search.get_index, search.model, search.reranker)

    def tearDown(self):
        search.get_index, search.model, search.reranker = self._saved

    def run_query(self, q, logit):
        import numpy as np
        class R:
            def predict(self, pairs, **k):
                return np.full(len(pairs), logit)
        search.get_index, search.model, search.reranker = (lambda: self.idx), self.M, (lambda: R())
        return search.search(q)["results"]

    def test_no_word_match_and_the_reranker_says_no_gives_no_results(self):
        self.assertEqual(self.run_query("jvm", -8.0), [])

    def test_a_hit_with_a_matching_word_stays_even_when_the_reranker_is_unsure(self):
        r = self.run_query("orchestration", -4.0)
        self.assertEqual([h["name"] for h in r], ["deck0.pptx"])

    def test_a_meaning_only_hit_stays_when_the_reranker_approves(self):
        self.assertTrue(self.run_query("jvm", 3.0))


class SlidesInRecordings(unittest.TestCase):
    def test_a_word_only_on_the_slide_shows_the_slide_line(self):
        it = {"lines": [{"t": 3.0, "seg_id": 7, "speaker": "Speaker 1", "text": "and this is what the thesis is about"}],
              "slide_text": "JVM (Java Virtual Machine) Performance" + chr(10) + "Resource efficiency"}
        h = search._snippet(it, {"jvm"}, None)
        self.assertTrue(h["on_slide"])
        self.assertEqual(h["snippet"], "JVM (Java Virtual Machine) Performance")
        self.assertIsNone(h["speaker"])

    def test_a_word_that_was_said_shows_what_was_said(self):
        it = {"lines": [{"t": 3.0, "seg_id": 7, "speaker": "Speaker 1", "text": "the jvm has a slow startup"}],
              "slide_text": "JVM (Java Virtual Machine) Performance"}
        h = search._snippet(it, {"jvm"}, None)
        self.assertNotIn("on_slide", h)
        self.assertEqual(h["speaker"], "Speaker 1")

    def test_the_documents_filter_also_searches_slides_shown_in_recordings(self):
        import numpy as np
        from rank_bm25 import BM25Okapi
        texts = ["JVM (Java Virtual Machine) Performance", "Next steps and owners", "Budget for the next year", "Risks and mitigations"]
        items = [{"type": "screen", "name": "talk.mp4", "folder": "A", "file_id": 1, "slide_uid": "s1", "t": 26.0, "seg_id": 5,
                  "text": texts[0], "slide_text": texts[0], "lines": [], "_tok": search.tok(texts[0])},
                 {"type": "page", "name": "deck.pptx", "folder": "A", "file_id": 2, "slide_uid": "p1", "page": 1,
                  "text": texts[1], "slide_text": texts[1], "lines": [], "_tok": search.tok(texts[1])}] + [
                 {"type": "page", "name": f"d{k}.pptx", "folder": "A", "file_id": 3 + k, "slide_uid": f"q{k}", "page": 1,
                  "text": t, "slide_text": t, "lines": [], "_tok": search.tok(t)} for k, t in enumerate(texts[2:])]

        class M:
            def encode(self, x, **k):
                return np.zeros((len(x), 3))
        idx = {"items": items, "bm25": BM25Okapi([i["_tok"] for i in items]), "emb": np.zeros((4, 3)),
               "vocab": ["jvm", "java", "next", "steps"], "speakers": []}
        saved = (search.get_index, search.model, search.reranker)
        search.get_index, search.model, search.reranker = (lambda: idx), M, (lambda: None)
        try:
            r = search.search("jvm", types={"page"})["results"]
        finally:
            search.get_index, search.model, search.reranker = saved
        self.assertEqual([(h["type"], h["name"]) for h in r], [("screen", "talk.mp4")])


if __name__ == "__main__":
    unittest.main()

