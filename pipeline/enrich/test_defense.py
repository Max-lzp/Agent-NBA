"""领防人和分区 xFG。python3 -m unittest pipeline.enrich.test_defense"""
from __future__ import annotations

import unittest
from unittest import mock

from . import defense
from .official import load_game

FLAGG = 1642843
THOMPSON = 1641708
EASON = 1631106


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

    def _attach(self, lead):
        release = {"E1": {"t": 16.75, "source": "ball_left_hand", "shooter_box": [0, 0, 1, 1],
                          "defender": {"team": "HOU", "number": None, "box": [1, 1, 2, 2], "foot_gap_px": 70},
                          "lead": lead}}
        with mock.patch.object(defense, "load_release", return_value=release):
            doc = defense.attach(self._stream(), "flagg", load_game("0022500701"), "0022500701")
        return doc["possessions"][0]["events"][0]

    def test_unread_lead_says_unread_not_matchup(self):
        ev = self._attach({"defender": {"status": "ok", "team": "HOU", "number": None, "guard_seconds": 1.8,
                                        "votes": 1, "reads": 4, "candidate": "7", "box_at_release": [1, 1, 2, 2]}})
        self.assertEqual(ev["release"]["t"], 16.75)
        d = ev["official"]["defender"]
        self.assertEqual((d["source"], d["number"], d["status"]), ("cv_lead", "", "number_unread"))
        self.assertNotIn("name", d)
        self.assertEqual(ev["defense"]["matchup"]["person_id"], THOMPSON)

    def test_read_lead_gets_matchup_stats(self):
        ev = self._attach({"defender": {"status": "ok", "team": "HOU", "number": "17", "name": "Eason",
                                        "jersey": "17", "guard_seconds": 1.8, "votes": 7, "reads": 19}})
        d = ev["official"]["defender"]
        self.assertEqual((d["source"], d["name"], d["number"]), ("cv_lead", "Eason", "17"))
        self.assertIn("season", d)

    def test_short_guard_has_no_name(self):
        ev = self._attach({"defender": {"status": "short", "team": "HOU", "number": "17", "name": "Eason",
                                        "guard_seconds": 0.4}})
        self.assertEqual(ev["official"]["defender"]["status"], "short_guard")
        self.assertNotIn("name", ev["official"]["defender"])

    def test_stars_drop_half_per_rank(self):
        self.assertEqual([defense.stars(n) for n in (1, 2, 5, 9, 12)], [5.0, 4.5, 3.0, 1.0, 0.5])

    def test_team_rank_lower_def_rating_is_better(self):
        ranks = defense._team_rank()
        self.assertEqual(ranks[FLAGG]["pts"]["rank"], 1)
        season = defense._season()
        hou = [r["def_rating"] for pid, r in ranks.items() if "def_rating" in r and season[pid]["team"] == "HOU"]
        best = min(hou, key=lambda r: r["rank"])
        self.assertEqual(best["value"], min(r["value"] for r in hou))


if __name__ == "__main__":
    unittest.main()
