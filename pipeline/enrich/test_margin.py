"""分差效率查询。python3 -m unittest pipeline.enrich.test_margin"""
from __future__ import annotations

import unittest

from .margin import bucket_of, load, pre_shot_margin, season_margin


class MarginTest(unittest.TestCase):
    def test_buckets(self):
        self.assertEqual(bucket_of(-10), "trail10")
        self.assertEqual(bucket_of(-6), "other")
        self.assertEqual(bucket_of(0), "close5")
        self.assertEqual(bucket_of(5), "close5")
        self.assertEqual(bucket_of(10), "lead10")

    def test_made_shot_uses_score_before_the_basket(self):
        play = {"kind": "shot", "made": True, "value": 2, "team": "DAL",
                "score_home": 80, "score_away": 90}
        self.assertEqual(pre_shot_margin(play, "DAL", "HOU"), 8)

    def test_flagg_close_game_comes_from_the_dataset(self):
        doc = load()
        self.assertEqual(doc["games"], 82)
        self.assertIn("Cooper Flagg", doc["players"])
        play = {"kind": "shot", "made": False, "value": 2, "team": "DAL", "person_id": 32,
                "name": "Flagg", "score_home": 100, "score_away": 98}
        players = {32: {"team": "DAL", "name": "Flagg", "full_name": "Cooper Flagg"}}
        got = season_margin(play, players, "HOU", "DAL")
        self.assertEqual(got["bucket"], "close5")
        self.assertEqual(got["bucket_zh"], "分差5分以内")
        self.assertAlmostEqual(got["ts"], 0.518, places=3)
        self.assertGreater(got["fga"], 500)
        self.assertAlmostEqual(got["team_ts"], 0.5521, places=4)

    def test_other_teams_are_left_alone(self):
        play = {"kind": "shot", "made": False, "team": "HOU", "person_id": 1,
                "name": "Smith", "score_home": 10, "score_away": 10}
        self.assertIsNone(season_margin(play, {1: {"team": "HOU", "name": "Smith"}}, "HOU", "DAL"))
