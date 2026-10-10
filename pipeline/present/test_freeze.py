"""定格卡与领防名签（#18）。python3 -m unittest pipeline.present.test_freeze"""
from __future__ import annotations

import unittest

from pipeline.present.freeze import card_lines, lead_side


def _shot() -> dict:
    return {
        "name": "弗拉格",
        "number": "32",
        "team": "DAL",
        "release": {"t": 5.0, "shooter_box": [100, 0, 160, 200]},
        "xfg": 0.5,
        "zone": "油漆区",
        "distance": 5,
        "defender": {
            "name": "伊森", "display": "伊森", "number": "17",
            "team": "HOU", "guard_seconds": 1.8,
        },
        "leadBox": [200, 0, 260, 200],
    }


def _pack() -> dict:
    return {
        "scorebug": [{
            "t": 0.0, "period": "第四节", "clock": "1:35",
            "away": "DAL", "home": "HOU", "awayScore": 103, "homeScore": 107,
            "awayName": "独行侠", "homeName": "火箭",
        }],
        "duration": 20.0,
    }


class FreezeCardTest(unittest.TestCase):
    def test_card_drops_the_scorebar(self):
        styles = [row[0] for row in card_lines(_pack(), _shot())]
        self.assertNotIn("score", styles)
        self.assertIn("head", styles)
        self.assertIn("off", styles)
        self.assertIn("def", styles)

    def test_card_keeps_shooter_and_defender(self):
        texts = [row[1] for row in card_lines(_pack(), _shot())]
        self.assertTrue(any("弗拉格" in t for t in texts))
        self.assertTrue(any("伊森" in t for t in texts))


class LeadSideTest(unittest.TestCase):
    def test_defender_right_of_shooter_tags_the_away_right_side(self):
        self.assertEqual(lead_side([100, 0, 160, 200], [200, 0, 260, 200]), "right")

    def test_defender_left_of_shooter_tags_the_away_left_side(self):
        self.assertEqual(lead_side([200, 0, 260, 200], [100, 0, 160, 200]), "left")

    def test_no_lead_box_means_no_tag(self):
        self.assertIsNone(lead_side([100, 0, 160, 200], None))


if __name__ == "__main__":
    unittest.main()
