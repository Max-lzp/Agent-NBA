"""合成的官方数据 + 阿门片真实的清单和记分牌读数。python3 -m unittest pipeline.enrich.test_join"""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from .join import OUT, RESULTS, Clock, _fill_tracks, _side, _zone, enrich
from .official import calibrate_side, clock_seconds, load_game, normalize_play

PBP_V3 = {"game": {"gameId": "0022500491", "actions": [
    {"actionNumber": 400, "clock": "PT07M40.00S", "period": 3, "teamTricode": "HOU", "personId": 3,
     "playerName": "Smith", "actionType": "Missed Shot", "subType": "Jump Shot", "isFieldGoal": 1,
     "shotResult": "Missed", "shotValue": 3, "xLegacy": -220, "yLegacy": 40, "shotDistance": 22,
     "scoreHome": "", "scoreAway": "", "description": "MISS Smith 22' 3PT Jump Shot"},
    {"actionNumber": 401, "clock": "PT07M39.00S", "period": 3, "teamTricode": "HOU", "personId": 1,
     "playerName": "Capela", "actionType": "Rebound", "isFieldGoal": 0, "description": "Capela REBOUND (Off:1 Def:2)"},
    {"actionNumber": 410, "clock": "PT07M26.00S", "period": 3, "teamTricode": "HOU", "personId": 1,
     "playerName": "Capela", "actionType": "Made Shot", "subType": "Dunk", "isFieldGoal": 1,
     "shotResult": "Made", "shotValue": 2, "xLegacy": 0, "yLegacy": 5, "shotDistance": 1,
     "scoreHome": "63", "scoreAway": "62", "description": "Capela 1' Dunk (8 PTS) (Sengun 4 AST)"},
    {"actionNumber": 300, "clock": "PT09M00.00S", "period": 3, "teamTricode": "DAL", "personId": 4,
     "playerName": "Flagg", "actionType": "Turnover", "isFieldGoal": 0, "description": "outside the clip"},
]}}
BOX = {"boxScoreSummary": {
    "homeTeam": {"teamTricode": "DAL", "players": [
        {"personId": 4, "name": "Cooper Flagg", "familyName": "Flagg", "jerseyNum": "32"}]},
    "awayTeam": {"teamTricode": "HOU", "players": [
        {"personId": 1, "name": "Clint Capela", "familyName": "Capela", "jerseyNum": "30"},
        {"personId": 2, "name": "Alperen Sengun", "familyName": "Sengun", "jerseyNum": "28"},
        {"personId": 3, "name": "Jabari Smith Jr.", "familyName": "Smith", "jerseyNum": "10"}]},
}}


class JoinTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        (d / "0022500491_playbyplayv3.json").write_text(json.dumps(PBP_V3))
        (d / "0022500491_boxscoresummaryv3.json").write_text(json.dumps(BOX))
        cls.game = load_game("0022500491", d)
        cls.board = json.loads((RESULTS / "amen.scoreboard.json").read_text(encoding="utf-8"))
        cls.stream = json.loads((OUT / "amen.json").read_text(encoding="utf-8"))
        cls.out = enrich(copy.deepcopy(cls.stream), cls.board, cls.game, "0022500491")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def events(self):
        return [e for p in self.out["possessions"] for e in p["events"]]

    def test_clock_parsing(self):
        self.assertEqual(clock_seconds("PT07M26.00S"), 446.0)
        self.assertEqual(clock_seconds("7:26"), 446.0)
        clock = Clock(self.board)
        self.assertEqual(clock.period, 3)
        self.assertEqual(clock.at(0.0), 465.0)

    def test_made_shot_matched_by_score(self):
        shot = next(e for e in self.events() if e["type"] == "shot" and e.get("official", {}).get("made"))
        self.assertEqual(shot["actor"], {"team": "HOU", "number": "30", "name": "Capela", "track_id": 32})
        self.assertEqual(shot["official"]["assist"]["name"], "Sengun")
        self.assertEqual(shot["geometry"]["shot_zone"], "restricted")
        self.assertEqual(self.out["official"]["matched_by_score"], 1)

    def test_missed_shot_and_rebound_added(self):
        added = [e for e in self.events() if e["source"] == "official"]
        self.assertEqual(sorted(e["type"] for e in added), ["rebound", "shot"])
        miss = next(e for e in added if e["type"] == "shot")
        self.assertFalse(miss["official"]["made"])
        self.assertEqual(miss["actor"]["number"], "10")
        self.assertAlmostEqual(miss["t"], 5.0, delta=1.0)
        self.assertEqual(miss["geometry"]["shot_zone"], "corner3_l")

    def test_plays_outside_clip_ignored(self):
        self.assertNotIn(300, [p["action_number"] for p in self.out["official"]["plays"]])

    def test_possession_scores(self):
        p1 = self.out["possessions"][0]["official"]
        self.assertEqual(p1["score_start"], {"HOU": 60, "DAL": 63})
        self.assertEqual(self.out["possessions"][-1]["official"]["score_end"], {"HOU": 62, "DAL": 63})


class SideTest(unittest.TestCase):
    LABELLED = [normalize_play({"actionNumber": i, "period": 1, "clock": "PT05M00.00S", "isFieldGoal": 1,
                                "actionType": "3pt", "shotResult": "Missed", "xLegacy": x, "yLegacy": 30,
                                "area": area}) for i, (x, area) in enumerate(
        [(-225, "Right Corner 3"), (-210, "Right Corner 3"), (230, "Left Corner 3"), (-150, "Above the Break 3")])]

    def test_official_label_wins(self):
        self.assertEqual(self.LABELLED[0]["side"], "right")
        self.assertEqual(_side(self.LABELLED[0], "left"), "right")

    def test_calibrated_from_labels(self):
        self.assertEqual(calibrate_side(self.LABELLED), ("right", "official_calibrated"))
        unlabeled = dict(self.LABELLED[3], side="")
        self.assertEqual(_side(unlabeled, "right"), "right")

    def test_cdn_side_is_court_half_not_shooter_side(self):
        play = normalize_play({"actionNumber": 1, "period": 3, "clock": "PT07M25.00S", "isFieldGoal": 1,
                               "actionType": "2pt", "shotResult": "Made", "x": 19.3, "y": 46.6, "side": "left",
                               "xLegacy": 17, "yLegacy": 129, "area": "In The Paint (Non-RA)", "areaDetail": "8-16 Center"})
        self.assertEqual((play["side"], play["court_half"]), ("", "left"))

    def test_assumed_without_labels(self):
        plays = [dict(p, side="") for p in self.LABELLED]
        self.assertEqual(calibrate_side(plays), ("left", "assumed"))

    def test_zone_needs_side_for_wings_and_corners(self):
        self.assertEqual(_zone(-22.0, 4.0, 3, "right"), "corner3_r")
        self.assertIsNone(_zone(-22.0, 4.0, 3, ""))
        self.assertEqual(_zone(2.0, 25.0, 3, ""), "top3")
        self.assertEqual(_zone(0.0, 1.0, 2, ""), "restricted")


class FillTracksTest(unittest.TestCase):
    def test_official_name_spreads_along_track_and_assist(self):
        ref = lambda track, name="": {"team": "DAL", "number": "", "track_id": track, **({"name": name} if name else {})}
        stream = {"possessions": [{"events": [
            {"event_id": "E1", "t": 1.0, "type": "pass", "actor": ref(9), "target": ref(21)},
            {"event_id": "E2", "t": 2.0, "type": "pass", "actor": ref(21), "target": ref(27, "Christie")},
            {"event_id": "E3", "t": 3.0, "type": "pass", "actor": ref(27, "Christie"), "target": ref(21)},
            {"event_id": "E4", "t": 4.0, "type": "shot", "actor": {**ref(21), "name": "Flagg"}},
            {"event_id": "E5", "t": 5.0, "type": "receive", "actor": ref(21, "Klay")},
        ]}]}
        named = {21: {"team": "DAL", "number": "32", "name": "Flagg"}}
        assists = [(4.0, 21, {"team": "DAL", "number": "3", "name": "Davis"})]
        filled, conflicts = _fill_tracks(stream, named, assists)
        evs = stream["possessions"][0]["events"]
        self.assertEqual(evs[0]["target"]["name"], "Flagg")
        self.assertEqual(evs[1]["actor"]["name"], "Flagg")
        self.assertEqual(evs[2]["actor"]["name"], "Christie")      # 号码认出的传球人不被助攻者覆盖
        self.assertEqual(evs[4]["actor"]["name"], "Klay")          # 号码和官方打架：不改，记冲突
        self.assertEqual(conflicts, [{"event_id": "E5", "track_id": 21, "cv": "Klay", "official": "Flagg"}])
        self.assertEqual(filled, 3)


if __name__ == "__main__":
    unittest.main()
