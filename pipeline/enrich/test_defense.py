"""防守人兜底和分区 xFG。python3 -m unittest pipeline.enrich.test_defense"""
from __future__ import annotations

import unittest
from unittest import mock

from . import defense
from .official import load_game

FLAGG = 1642843
THOMPSON = 1641708


class DefenseTest(unittest.TestCase):
    def test_matchup_outer_player_is_the_shooter(self):
        guards = defense._matchups("0022500701")[FLAGG]
        self.assertEqual(guards[0]["person_id"], THOMPSON)
        self.assertEqual(sum(g["points"] for g in guards), 34)

    def test_zone_chart_counts_makes_separately(self):
        chart = defense.zone_chart(FLAGG)
        self.assertEqual(len(chart["games"]), 4)
        for zone in chart["zones"].values():
            self.assertLessEqual(zone["fgm"], zone["fga"])
            if zone["fga"] < chart["min_shots"]:
                self.assertIsNone(zone["xfg"])

    def _stream(self):
        return {"official": {"on_court": {"HOU": [{"jersey": "1", "name": "Thompson", "full_name": "Amen Thompson"}]}},
                "possessions": [{"events": [{"event_id": "E1", "t": 17.0, "type": "shot",
                                             "official": {"person_id": FLAGG, "made": True}}]}]}

    def test_unread_number_falls_back_to_matchup(self):
        release = {"E1": {"t": 16.75, "source": "ball_left_hand", "shooter_box": [0, 0, 1, 1],
                          "defender": {"team": "HOU", "number": None, "box": [1, 1, 2, 2], "foot_gap_px": 70}}}
        with mock.patch.object(defense, "load_release", return_value=release):
            doc = defense.attach(self._stream(), "flagg", load_game("0022500701"), "0022500701")
        ev = doc["possessions"][0]["events"][0]
        self.assertEqual(ev["release"]["t"], 16.75)
        self.assertEqual(ev["defense"]["nearest"]["source"], "cv")
        self.assertEqual(ev["official"]["defender"]["source"], "matchup")
        self.assertEqual(ev["official"]["defender"]["name"], "Thompson")

    def test_read_number_wins_over_matchup(self):
        release = {"E1": {"t": 16.75, "source": "ball_left_hand", "shooter_box": [0, 0, 1, 1],
                          "defender": {"team": "HOU", "number": "17", "name": "Eason", "box": [1, 1, 2, 2]}}}
        with mock.patch.object(defense, "load_release", return_value=release):
            doc = defense.attach(self._stream(), "flagg", load_game("0022500701"), "0022500701")
        d = doc["possessions"][0]["events"][0]["official"]["defender"]
        self.assertEqual((d["source"], d["name"]), ("cv", "Eason"))
        self.assertIn("vs_shooter", d)


if __name__ == "__main__":
    unittest.main()
