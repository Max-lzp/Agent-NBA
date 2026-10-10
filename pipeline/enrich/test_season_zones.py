"""赛季三区：缺失不显示、百分比乘 100、区域归属。python3 -m unittest pipeline.enrich.test_season_zones"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from .season_zones import (
    band_of, build_zone, diff_phrase, format_pct, load, profile_for, profile_line, shown_pct, zone_lines,
)

REPO = Path(__file__).resolve().parents[2]
FLAGG = 1642843
EASON = 1631106
SENGUN = 1630578
FINNEY = 1627827
NO_THREE = 203500


class FormatTest(unittest.TestCase):
    def test_percent_multiplies_by_100(self):
        self.assertEqual(format_pct(0.5), "50.0%")
        self.assertEqual(format_pct(0.6085011185682326), "60.9%")
        self.assertEqual(format_pct(0.6666666666666666), "66.7%")

    def test_missing_percent_is_not_zero(self):
        self.assertIsNone(format_pct(None))
        self.assertIsNone(format_pct(""))
        self.assertIsNone(shown_pct(None, 4))
        self.assertIsNone(shown_pct(0, 0))
        self.assertIsNone(shown_pct(0.0, None))
        self.assertNotEqual(format_pct(None) or "", "0%")
        self.assertNotEqual(format_pct("") or "", "0.0%")


class BandTest(unittest.TestCase):
    def test_distance_under_six_is_near_even_in_the_paint(self):
        self.assertEqual(band_of(distance_ft=5.35, value=2, shot_zone="paint"), "near")
        self.assertEqual(band_of(distance_ft=5.99, value=2, shot_zone="paint"), "near")

    def test_six_feet_and_farther_twos_are_other_twos(self):
        self.assertEqual(band_of(distance_ft=6, value=2, shot_zone="paint"), "other_two")
        self.assertEqual(band_of(distance_ft=12.97, value=2, shot_zone="paint"), "other_two")

    def test_threes_stay_threes(self):
        self.assertEqual(band_of(distance_ft=23.5, value=3, shot_zone="top3"), "three")
        self.assertEqual(band_of(distance_ft=22, value=3, shot_zone="corner3_l"), "three")

    def test_paint_without_distance_is_unknown(self):
        self.assertIsNone(band_of(distance_ft=None, value=2, shot_zone="paint"))

    def test_restricted_without_distance_is_near(self):
        self.assertEqual(band_of(distance_ft=None, value=2, shot_zone="restricted"), "near")

    def test_midrange_without_distance_is_other_two(self):
        self.assertEqual(band_of(distance_ft=None, value=2, shot_zone="midrange"), "other_two")


class TableTest(unittest.TestCase):
    def test_only_hou_dal_rows_are_loaded(self):
        data = load()
        self.assertEqual(len(data["offense"]), 39)
        self.assertEqual(len(data["profiles"]), 39)
        self.assertNotIn(1630639, data["offense"])
        self.assertEqual(len(data["defense"]), 39)

    def test_zero_attempts_keep_no_rate(self):
        stats = load()["offense"][NO_THREE]["bands"]["three"]
        self.assertEqual(stats.get("fga"), 0)
        self.assertNotIn("fg_pct", stats)
        text = "\n".join(zone_lines(build_zone(NO_THREE, None, "three", "HOU", "")))
        self.assertNotIn("0%", text)
        self.assertNotIn("0.0%", text)

    def test_flagg_near_compares_by_player_id(self):
        zone = build_zone(FLAGG, EASON, "near", "DAL", "HOU")
        text = "\n".join(zone["lines"])
        self.assertEqual(zone["band"], "near")
        self.assertEqual(zone["label"], "近框")
        self.assertIn("60.9%", text)
        self.assertIn("66.7%", text)
        self.assertIn("272/447", text)
        self.assertIn("102/153", text)
        self.assertIn("对手命中率，越低越好", text)
        self.assertIn("个百分点", text)
        self.assertLess(text.index("三分"), text.index("其余两分"))
        self.assertLess(text.index("其余两分"), text.index("近框"))
        self.assertNotIn("中距离", text)
        self.assertNotIn("player_name", json.dumps(zone))
        blob = json.dumps(zone, ensure_ascii=False)
        for banned in ("source_key", "tracking_games", "frequency_official", "user_", "five_way", "idol"):
            self.assertNotIn(banned, blob)

    def test_derived_middle_band_hides_the_normal_diff(self):
        zone = build_zone(FLAGG, EASON, "other_two", "DAL", "HOU")
        self.assertNotIn("diff_from_opponents_normal_official", zone["defender"])
        self.assertNotIn("opponents_normal_fg_pct_official", zone["defender"])
        forced = {
            "band": "other_two",
            "label": "其余两分",
            "defender": {
                "player_id": EASON,
                "opponent_fgm": 4,
                "opponent_fga": 10,
                "opponent_fg_pct": 0.4,
                "diff_from_opponents_normal_official": -0.05,
                "opponents_normal_fg_pct_official": 0.45,
            },
            "defender_bands": [
                {"band": "three", "label": "三分", "opponent_fgm": 3, "opponent_fga": 10, "opponent_fg_pct": 0.3},
                {"band": "other_two", "label": "其余两分", "opponent_fgm": 4, "opponent_fga": 10, "opponent_fg_pct": 0.4,
                 "diff_from_opponents_normal_official": -0.05},
                {"band": "near", "label": "近框", "opponent_fgm": 5, "opponent_fga": 10, "opponent_fg_pct": 0.5},
            ],
        }
        text = "\n".join(zone_lines(forced))
        self.assertNotIn("个百分点", text)
        self.assertIn("40.0%", text)
        self.assertIsNone(diff_phrase("other_two", forced["defender"]))

    def test_missing_rate_text_never_prints_zero_percent(self):
        zone = {
            "band": "near",
            "label": "近框",
            "defender": {"player_id": 1, "opponent_fgm": 1, "opponent_fga": 2},
            "defender_bands": [
                {"band": "three", "label": "三分", "opponent_fgm": 0, "opponent_fga": 0},
                {"band": "other_two", "label": "其余两分", "opponent_fgm": 1, "opponent_fga": 2},
                {"band": "near", "label": "近框", "opponent_fgm": 1, "opponent_fga": 2},
            ],
            "shooter": {"fgm": 0, "fga": 0, "fg_pct": 0},
        }
        text = "\n".join(zone_lines(zone))
        self.assertNotIn("0%", text)
        self.assertNotIn("0.0%", text)
        self.assertIn("无出手", text)
        self.assertIn("这一投近框（1/2）", text)
        self.assertNotIn("%", text)

    def test_wrong_team_does_not_match(self):
        self.assertIsNone(profile_for(FLAGG, "HOU"))
        zone = build_zone(FLAGG, None, "near", "HOU", "") or {}
        self.assertNotIn("shooter", zone)
        self.assertNotIn("60.9%", "\n".join(zone.get("lines") or []))

    def test_profile_omits_unverified_wingspan_and_user_columns(self):
        sengun = profile_for(SENGUN, "HOU")
        self.assertNotIn("wingspan_cm", sengun)
        self.assertNotIn("臂展", profile_line(sengun) or "")
        finney = profile_for(FINNEY, "HOU")
        line = profile_line(finney)
        self.assertIn("落选", line)
        self.assertIn("臂展", line)
        blob = json.dumps(finney, ensure_ascii=False)
        for banned in ("user", "source_key", "wingspan_status", "five_way", "idol", "jersey", "Finney", "scope"):
            self.assertNotIn(banned, blob)

    def test_drafted_profile_line(self):
        line = profile_line(profile_for(FLAGG, "DAL"))
        self.assertIn("身高 6-9（205.74 cm）", line)
        self.assertIn("前锋", line)
        self.assertIn("2025年第1轮第1顺位", line)
        self.assertIn("臂展 213.36 cm", line)
        self.assertNotIn("落选", line)


class ReplayFreezeScriptTest(unittest.TestCase):
    def test_browser_card_follows_the_same_rules(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node 不在环境里")
        script = r"""
const fs = require("fs");
const vm = require("vm");
const sandbox = { console };
sandbox.window = sandbox;
vm.runInNewContext(fs.readFileSync(process.argv[1], "utf8"), sandbox);
const D = sandbox.DeepFreeze;
const fail = [];
const eq = (got, want, name) => { if (got !== want) fail.push(name + " got " + JSON.stringify(got)); };
eq(D.formatPct(null), "", "null pct");
eq(D.formatPct(""), "", "blank pct");
eq(D.formatPct(0.6666666666666666), "66.7%", "times 100");
eq(D.shownPct(0, 0), "", "zero attempts");
eq(D.shownPct(null, 4), "", "missing rate");
eq(D.shownPct(0.5, 4), "50.0%", "real rate");
eq(D.bandOf({ distance: 5.35, value: 2, zoneKey: "paint" }), "near", "paint under 6");
eq(D.bandOf({ distance: 6, value: 2, zoneKey: "paint" }), "other_two", "six feet");
eq(D.bandOf({ distance: 12.97, value: 2, zoneKey: "paint" }), "other_two", "amen paint");
eq(D.bandOf({ distance: 23, value: 3, zoneKey: "top3" }), "three", "three");
eq(D.bandOf({ distance: null, value: 2, zoneKey: "paint" }), null, "paint unknown");
eq(D.bandOf({ value: 2, zoneKey: "restricted" }), "near", "restricted");
const html = D.zoneHtml({
  band: "other_two",
  label: "其余两分",
  defender: { opponent_fgm: 4, opponent_fga: 10, opponent_fg_pct: 0.4, diff_from_opponents_normal_official: -0.05, opponents_normal_fg_pct_official: 0.45 },
  defender_bands: [
    { band: "three", label: "三分", opponent_fgm: 0, opponent_fga: 0 },
    { band: "other_two", label: "其余两分", opponent_fgm: 4, opponent_fga: 10, opponent_fg_pct: 0.4 },
    { band: "near", label: "近框", opponent_fgm: 5, opponent_fga: 10, opponent_fg_pct: 0.5 }
  ],
  shooter: { fgm: 0, fga: 0, fg_pct: 0 }
});
const threeCell = html.slice(html.indexOf("三分"), html.indexOf("其余两分"));
if (threeCell.includes("%")) fail.push("zero attempts showed a percent " + threeCell);
const shooterGone = html.includes("投篮人同区") && html.includes("0%");
if (shooterGone) fail.push("missing shooter rate showed 0%");
if (html.includes("个百分点")) fail.push("derived band showed diff");
if (!(html.indexOf("三分") < html.indexOf("其余两分") && html.indexOf("其余两分") < html.indexOf("近框"))) fail.push("order");
if (!html.includes("40.0%") || !html.includes("50.0%")) fail.push("missing scaled pct");
if (!html.includes("无出手")) fail.push("missing 无出手");
if (!html.includes("对手命中率，越低越好")) fail.push("missing label");
const undrafted = D.profileLine({ height_ft_in: "6-8", position: "Forward", draft_status: "undrafted" });
if (!undrafted.includes("落选") || undrafted.includes("臂展")) fail.push("undrafted profile " + undrafted);
const winged = D.profileLine({ height_cm: 205.74, height_ft_in: "6-9", position_zh: "前锋", draft_status: "drafted", draft_year: 2025, draft_round: 1, draft_overall_pick: 1, wingspan_cm: 213.36 });
if (!winged.includes("臂展 213.36 cm") || !winged.includes("2025年第1轮第1顺位")) fail.push("draft profile " + winged);
if (fail.length) { console.error(fail.join("\n")); process.exit(1); }
"""
        proc = subprocess.run([node, "-e", script, str(REPO / "web" / "replay-freeze.js")],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)


if __name__ == "__main__":
    unittest.main()
