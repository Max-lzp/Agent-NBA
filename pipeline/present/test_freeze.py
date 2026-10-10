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


class SeasonZoneCardTest(unittest.TestCase):
    def test_card_scales_percent_and_hides_missing(self):
        shot = _shot()
        shot["shooterProfile"] = {
            "height_ft_in": "6-9", "height_cm": 205.74, "position_zh": "前锋",
            "draft_status": "drafted", "draft_year": 2025, "draft_round": 1, "draft_overall_pick": 1,
            "wingspan_cm": 213.36,
        }
        shot["defender"]["profile"] = {
            "height_ft_in": "6-8", "position_zh": "前锋", "draft_status": "undrafted",
        }
        shot["seasonZone"] = {
            "band": "near",
            "label": "近框",
            "defender_bands": [
                {"band": "three", "label": "三分", "opponent_fgm": 0, "opponent_fga": 0},
                {"band": "other_two", "label": "其余两分", "opponent_fgm": 4, "opponent_fga": 10, "opponent_fg_pct": 0.4},
                {"band": "near", "label": "近框", "opponent_fgm": 1, "opponent_fga": 2, "opponent_fg_pct": 0.5,
                 "opponents_normal_fg_pct_official": 0.64, "diff_from_opponents_normal_official": -0.039},
            ],
            "defender": {
                "player_id": 1, "opponent_fgm": 1, "opponent_fga": 2, "opponent_fg_pct": 0.5,
                "opponents_normal_fg_pct_official": 0.64, "diff_from_opponents_normal_official": -0.039,
            },
            "shooter": {"fgm": 0, "fga": 0, "fg_pct": 0},
        }
        text = "\n".join(row[1] for row in card_lines(_pack(), shot))
        self.assertIn("对手命中率，越低越好", text)
        self.assertIn("40.0%", text)
        self.assertIn("50.0%", text)
        self.assertIn("身高 6-9（205.74 cm）", text)
        self.assertIn("2025年第1轮第1顺位", text)
        self.assertIn("臂展 213.36 cm", text)
        self.assertIn("落选", text)
        self.assertIn("比平时 64.0% 低 3.9 个百分点", text)
        self.assertIn("无出手", text)
        self.assertLess(text.index("三分"), text.index("其余两分"))
        self.assertLess(text.index("其余两分"), text.index("近框"))
        self.assertNotIn("中距离", text)

    def test_derived_band_on_the_card_has_no_diff(self):
        shot = _shot()
        shot["seasonZone"] = {
            "band": "other_two",
            "label": "其余两分",
            "defender_bands": [
                {"band": "three", "label": "三分", "opponent_fgm": 3, "opponent_fga": 10, "opponent_fg_pct": 0.3},
                {"band": "other_two", "label": "其余两分", "opponent_fgm": 4, "opponent_fga": 10, "opponent_fg_pct": 0.4,
                 "diff_from_opponents_normal_official": -0.05, "opponents_normal_fg_pct_official": 0.45},
                {"band": "near", "label": "近框", "opponent_fgm": 5, "opponent_fga": 10, "opponent_fg_pct": 0.5},
            ],
            "defender": {
                "opponent_fgm": 4, "opponent_fga": 10, "opponent_fg_pct": 0.4,
                "diff_from_opponents_normal_official": -0.05, "opponents_normal_fg_pct_official": 0.45,
            },
        }
        text = "\n".join(row[1] for row in card_lines(_pack(), shot))
        self.assertNotIn("个百分点", text)
        self.assertIn("其余两分", text)


class LeadSideTest(unittest.TestCase):
    def test_defender_right_of_shooter_tags_the_away_right_side(self):
        self.assertEqual(lead_side([100, 0, 160, 200], [200, 0, 260, 200]), "right")

    def test_defender_left_of_shooter_tags_the_away_left_side(self):
        self.assertEqual(lead_side([200, 0, 260, 200], [100, 0, 160, 200]), "left")

    def test_no_lead_box_means_no_tag(self):
        self.assertIsNone(lead_side([100, 0, 160, 200], None))


if __name__ == "__main__":
    unittest.main()
