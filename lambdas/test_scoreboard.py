import unittest

from ocr_scoreboard.parse import parse_scoreboard


def line(text: str, height: float, left: float) -> dict:
    return {
        "BlockType": "LINE",
        "Text": text,
        "Geometry": {"BoundingBox": {"Height": height, "Left": left}},
    }


class ScoreboardTests(unittest.TestCase):
    def test_reads_score_quarter_and_clock(self):
        parsed = parse_scoreboard(
            [
                line("Q4", 0.04, 0.4),
                line("01:34", 0.05, 0.55),
                line("103", 0.12, 0.2),
                line("107", 0.12, 0.7),
                line("12", 0.02, 0.1),
            ]
        )
        self.assertEqual(parsed["home"], 103)
        self.assertEqual(parsed["away"], 107)
        self.assertEqual(parsed["quarter"], "Q4")
        self.assertEqual(parsed["clock"], "01:34")
        self.assertGreater(parsed["ocr_confidence"], 0)

    def test_missing_pair_is_zero_confidence(self):
        parsed = parse_scoreboard([line("Q4", 0.04, 0.4), line("01:34", 0.05, 0.5)])
        self.assertEqual(parsed["ocr_confidence"], 0)
        self.assertIsNone(parsed["home"])


if __name__ == "__main__":
    unittest.main()
