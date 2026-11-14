"""Checks of the answer verification and source finding (no model needed)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

sys.path.insert(0, str(Path(__file__).parent))
import _isolated  # noqa: E402,F401  (temporary library, before datadistill is imported)

from datadistill.answer import sources, unsupported  # noqa: E402

LINES = [{"t": 10, "who": "Rose Lindgren", "kind": "line",
          "text": "We'd like to sell it for twenty five Euro with the profit aim of 50 million Euro from our sales."},
         {"t": 60, "who": "Iain", "kind": "line", "text": "Can you e-mail your slides as well? That's the FDA's policy."},
         {"t": 0, "who": "", "kind": "slide", "title": "Finance", "text": "Selling price 25 Euro. Production cost 12.50 Euro."}]


class Verify(unittest.TestCase):
    def test_numbers_must_be_in_the_record(self):
        self.assertEqual(unsupported("The profit aim is 15 million euro.", "What is the profit aim?", LINES), ["15"])
        self.assertEqual(unsupported("The profit aim is 50 million euro.", "What is the profit aim?", LINES), [])
        self.assertEqual(unsupported("It costs 12.50 euro to make.", "Cost?", LINES), [])

    def test_names_must_be_in_the_record(self):
        self.assertEqual(unsupported("Iain asked Rose for the slides.", "Who asked?", LINES), [])
        self.assertEqual(unsupported("Jessy asked for the slides.", "Who asked?", LINES), ["Jessy"])
        self.assertEqual(unsupported("It is FDA policy.", "Whose policy?", LINES), [])

    def test_sources(self):
        s = sources(LINES, "What is the profit aim?", "The profit aim is 50 million euro.")
        self.assertIn("profit aim of 50 million", s[0]["text"])
        s = sources(LINES, "What does it cost to produce?", "Production cost is 12.50 euro.")
        self.assertEqual(s[0]["kind"], "slide")


if __name__ == "__main__":
    unittest.main()
