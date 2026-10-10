"""python3 -m unittest scripts.fallback.test_scoreboard_film"""

from __future__ import annotations

import unittest

from scripts.fallback.scoreboard_film import label_at, scorebug


class ScoreboardFilmTest(unittest.TestCase):
    def test_label_follows_the_clock_without_a_player_name(self):
        reads = [
            {"t": 0.0, "period": "4TH", "clock": "1:52", "scores": [103, 107], "teams": ["DAL", "HOU"]},
            {"t": 2.0, "period": "4TH", "clock": "1:50", "scores": [103, 107], "teams": ["DAL", "HOU"]},
        ]
        self.assertEqual(label_at(reads, 1.0, "DAL", "HOU"), "第四节  1:52  独行侠 103  火箭 107")
        self.assertEqual(label_at(reads, 2.1, "DAL", "HOU"), "第四节  1:50  独行侠 103  火箭 107")
        self.assertEqual(label_at(reads, -1, "DAL", "HOU"), "")
        self.assertNotIn("弗拉格", label_at(reads, 1.0, "DAL", "HOU"))

    def test_scorebug_keeps_a_change(self):
        reads = [
            {"t": 0.0, "period": "4TH", "clock": "1:52", "scores": [103, 107], "teams": ["DAL", "HOU"]},
            {"t": 1.0, "period": "4TH", "clock": "1:52", "scores": [103, 107], "teams": ["DAL", "HOU"]},
            {"t": 2.0, "period": "4TH", "clock": "1:48", "scores": [106, 107], "teams": ["DAL", "HOU"]},
        ]
        rows = scorebug(reads, "DAL", "HOU")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["awayScore"], 106)
        self.assertEqual(rows[0]["period"], "第四节")


if __name__ == "__main__":
    unittest.main()
