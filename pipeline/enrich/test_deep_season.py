"""赛季深度数据查询。python3 -m unittest pipeline.enrich.test_deep_season"""
from __future__ import annotations

import unittest

from .deep_season import resolve, season_profile


class DeepSeasonTest(unittest.TestCase):
    def test_unknown_id_does_not_fall_through_to_jersey(self):
        by_id = {1641708: {"player_id": 1641708, "name": "Amen Thompson", "team": "HOU"}}
        jersey = {("HOU", "1"): [1641708]}
        self.assertIsNone(resolve(by_id, jersey, player_id=3, team="HOU", number="1", name="Thompson"))

    def test_shared_jersey_needs_a_last_name(self):
        by_id = {
            1: {"player_id": 1, "name": "Daniel Gafford", "team": "DAL"},
            2: {"player_id": 2, "name": "Tyler Smith", "team": "DAL"},
        }
        jersey = {("DAL", "21"): [1, 2]}
        self.assertIsNone(resolve(by_id, jersey, team="DAL", number="21", name=""))
        self.assertEqual(resolve(by_id, jersey, team="DAL", number="21", name="Gafford")["player_id"], 1)
        self.assertEqual(resolve(by_id, jersey, team="DAL", number="21", name="Smith")["player_id"], 2)

    def test_amen_and_durant_come_from_the_csv(self):
        amen = season_profile(team="HOU", number="1", name="Thompson")
        self.assertIsNotNone(amen)
        self.assertEqual(amen["player_id"], 1641708)
        self.assertAlmostEqual(amen["xfg"], 0.587, places=3)
        self.assertAlmostEqual(amen["fg"], 0.534, places=3)
        self.assertAlmostEqual(amen["fg_above"], -0.053, places=3)
        self.assertEqual(amen["tracked_fga"], 1044)
        self.assertLess(amen["gravity"], 0)
        self.assertGreater(amen["leverage"], 0)
        self.assertAlmostEqual(amen["baseline_xfg"], 0.4658, places=3)

        durant = season_profile(player_id=201142)
        self.assertAlmostEqual(durant["fg_above"], 0.087, places=3)
        self.assertLess(durant["quality_ppt"], 0)

    def test_flagg_shot_quality(self):
        flagg = season_profile(team="DAL", number="32", name="Flagg")
        self.assertEqual(flagg["player_id"], 1642843)
        self.assertAlmostEqual(flagg["xfg"], 0.481, places=3)
        self.assertAlmostEqual(flagg["fg_above"], -0.013, places=3)
