"""精彩回放：合成比赛证明不写死答案；考试片官方数据用来对卡片数字。"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import highlights

ROOT = Path(__file__).resolve().parents[1]
EXAM = Path("/tmp/exam")
ANSWER = ROOT / "docs" / "exam" / "answer-key.json"
PACK = Path("/home/ubuntu/.cursor/projects/workspace/uploads/exam-pack_1440.tgz")


def _clock(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}"


def synthetic_inputs():
    """另一场完全虚构的比赛，号码和预期命中率都和考试片无关。"""
    playbyplay = {"game": {"actions": [
        {
            "actionNumber": 10, "period": 1, "clock": "PT01M12.00S",
            "timeActual": "2026-01-01T00:10:00Z", "teamTricode": "AAA", "personId": 9001,
            "playerNameI": "A. Alpha", "playerName": "Alpha", "actionType": "2pt",
            "shotResult": "Missed", "shotDistance": 5.5, "isFieldGoal": 1,
            "description": "MISS A. Alpha 6' Shot - blocked", "blockPersonId": 9002,
        },
        {
            "actionNumber": 11, "period": 1, "clock": "PT01M12.00S",
            "timeActual": "2026-01-01T00:10:00Z", "teamTricode": "BBB", "personId": 9002,
            "playerNameI": "B. Beta", "playerName": "Beta", "actionType": "block",
            "description": "B. Beta BLOCK (1 BLK)",
        },
        {
            "actionNumber": 12, "period": 1, "clock": "PT01M04.00S",
            "timeActual": "2026-01-01T00:10:08Z", "teamTricode": "AAA", "personId": 9001,
            "playerNameI": "A. Alpha", "actionType": "3pt", "shotResult": "Made",
            "shotDistance": 25.5, "area": "Above the Break 3", "isFieldGoal": 1,
            "description": "A. Alpha 26' 3PT",
        },
        {
            "actionNumber": 13, "period": 1, "clock": "PT01M04.00S",
            "timeActual": "2026-01-01T00:10:11Z", "teamTricode": "BBB", "personId": 9002,
            "playerNameI": "B. Beta", "actionType": "foul", "subType": "personal",
            "descriptor": "shooting", "foulDrawnPersonId": 9001,
            "description": "B. Beta shooting personal FOUL",
        },
        {
            "actionNumber": 14, "period": 1, "clock": "PT01M04.00S",
            "timeActual": "2026-01-01T00:10:40Z", "teamTricode": "AAA", "personId": 9001,
            "actionType": "freethrow", "shotResult": "Made",
            "description": "A. Alpha Free Throw 1 of 1",
        },
    ]}}
    reads = []
    for t in range(0, 15, 2):
        clock = 80 - t
        reads.append({
            "t": t, "period": 1, "clock": _clock(clock),
            "shot_clock": max(1, 14 - t), "scores": [10, 8],
        })
    reads += [
        {"t": 16, "period": 1, "clock": "1:04", "shot_clock": 0.4, "scores": [10, 8]},
        {"t": 18.5, "period": 1, "clock": "1:04", "shot_clock": 24, "scores": [10, 11]},
        {"t": 21, "period": 1, "clock": "1:04", "shot_clock": 24, "scores": [10, 11]},
    ]
    quality = {"shots": [
        {"playerId": 9001, "period": 1, "gameClock": "1:12", "shotQuality": 0.55, "teamAbbreviation": "AAA"},
        {"playerId": 9001, "period": 1, "gameClock": "1:04", "shotQuality": 0.42, "teamAbbreviation": "AAA"},
    ]}
    dash = {
        "parameters": {"MeasureType": "Base", "TeamID": 0},
        "resultSets": [{
            "headers": ["PLAYER_ID", "GP", "BLK", "BLKA", "FG3M", "FG3A", "FG3_PCT", "STL"],
            "rowSet": [
                [9002, 10, 20, 1, 0, 0, None, 8],
                [9001, 10, 1, 4, 30, 100, 0.3, 2],
            ],
        }],
    }
    matchups = {"boxScoreMatchups": {"homeTeam": {"players": [{
        "personId": 9001,
        "matchups": [{
            "personId": 9002,
            "jerseyNum": "3",
            "statistics": {
                "matchupMinutes": "1:10", "matchupMinutesSort": 70,
                "playerPoints": 3,
                "matchupFieldGoalsMade": 1, "matchupFieldGoalsAttempted": 2,
                "matchupFieldGoalsPercentage": 0.5,
                "matchupThreePointersMade": 1, "matchupThreePointersAttempted": 1,
                "matchupThreePointersPercentage": 1,
                "matchupBlocks": 1, "shootingFouls": 1,
                "helpFieldGoalsAttempted": 0, "helpFieldGoalsPercentage": 0.0,
            },
        }],
    }]}}}
    boxscore = {"game": {
        "officials": [{"personId": 1, "name": "Referee", "jerseyNum": "30"}],
        "awayTeam": {"teamTricode": "AAA", "players": [{
            "personId": 9001, "jerseyNum": "1", "firstName": "A.", "familyName": "Alpha",
            "statistics": {"blocks": 0, "blocksReceived": 1},
        }]},
        "homeTeam": {"teamTricode": "BBB", "players": [{
            "personId": 9002, "jerseyNum": "3", "firstName": "B.", "familyName": "Beta",
            "statistics": {"blocks": 1, "blocksReceived": 0},
        }]},
    }}
    return {
        "board": {"reads": reads, "away": "AAA", "home": "BBB"},
        "playbyplay": playbyplay,
        "shotquality": quality,
        "matchups": matchups,
        "boxscore": boxscore,
        "roster_stats": [dash],
        "duration": 22,
    }


class SyntheticTests(unittest.TestCase):
    def test_finds_block_and_three_and_one_from_data(self):
        doc = highlights.build_highlights(**synthetic_inputs(), season_dir=Path("/tmp/no-season-here"))
        kinds = [item["type"] for item in doc["highlights"]]
        self.assertEqual(kinds, ["block", "and1_3"])
        block, three = doc["highlights"]
        self.assertLess(block["sourceSeconds"]["key"], three["sourceSeconds"]["key"])
        self.assertGreaterEqual(block["replay"]["end"] - block["replay"]["start"], 10)
        self.assertGreaterEqual(three["replay"]["end"] - three["replay"]["start"], 10)
        self.assertEqual(block["replay"]["start"], 22)
        block_text = "\n".join(block["card"]["lines"])
        three_text = "\n".join(three["card"]["lines"])
        self.assertIn("55.0%", block_text)
        self.assertIn("5.5 英尺", block_text)
        self.assertIn("20", block_text)
        self.assertIn("场均 2.0", block_text)
        self.assertIn("Beta", block_text)
        self.assertIn("42.0%", three_text)
        self.assertIn("25.5 英尺", three_text)
        self.assertIn("30.0%", three_text)
        self.assertIn("1/2（50.0%）", three_text)
        self.assertIn("三分 1/1（100.0%）", three_text)
        self.assertNotIn("（0.0%）", three_text)
        self.assertNotIn("0/0", block_text + three_text)
        self.assertNotIn("臂展", block_text + three_text)
        self.assertNotIn("防守距离", block_text + three_text)
        self.assertNotIn("队内", block_text + three_text)
        self.assertNotIn("0星", block_text + three_text)
        joined = json.dumps(doc["highlights"], ensure_ascii=False)
        self.assertNotIn("1631095", joined)
        self.assertNotIn("1631108", joined)
        for item in doc["highlights"]:
            blob = "".join(item["commentary"]["zh"]) + "".join(item["commentary"]["en"])
            for stat in item["card"]["stats"]:
                self.assertIn(stat["display"], item["card"]["lines"][-1] + "\n" + "\n".join(item["card"]["lines"]))
            self.assertTrue(item["commentary"]["zh"])
            self.assertTrue(item["commentary"]["en"])
            self.assertIn(item["highlight"] if False else item["type"], ("block", "and1_3"))
            self.assertNotIn("（0.0%）", blob)

    def test_identity_mismatch_hides_name_and_skips_referee(self):
        data = synthetic_inputs()
        # 裁判贴着投篮人，另一名对方球员比官方盖帽人更近。
        tags = []
        for t in [i * 0.5 for i in range(0, 44)]:
            tags.append({"t": t, "players": [
                {"team": "AAA", "jersey": "1", "name": "Alpha", "x": 100, "y": 200, "w": 40, "h": 100},
                {"team": "", "jersey": "30", "name": "Referee", "referee": True, "x": 120, "y": 190, "w": 36, "h": 110},
                {"team": "BBB", "jersey": "9", "name": "Gamma", "x": 160, "y": 200, "w": 40, "h": 100},
                {"team": "BBB", "jersey": "3", "name": "Beta", "x": 360, "y": 180, "w": 40, "h": 100},
            ]})
        data["identity"] = {"tags": tags}
        doc = highlights.build_highlights(**data, season_dir=Path("/tmp/no-season-here"))
        block = doc["highlights"][0]
        text = "\n".join(block["card"]["lines"]) + "".join(block["commentary"]["zh"])
        self.assertIn("防守人", text)
        self.assertNotIn("Beta", text)
        self.assertNotIn("Gamma", text)
        self.assertNotIn("Referee", text)
        self.assertFalse(block["people"]["defender"]["named"])
        self.assertIs(block["people"]["defender"]["agrees"], False)
        self.assertEqual(block["type"], "block")

    def test_identity_agreement_keeps_official_name(self):
        data = synthetic_inputs()
        tags = []
        for t in [i * 0.5 for i in range(0, 44)]:
            tags.append({"t": t, "players": [
                {"team": "AAA", "jersey": "1", "name": "Alpha", "x": 100, "y": 200, "w": 40, "h": 100},
                {"team": "", "jersey": "30", "name": "Referee", "referee": True, "x": 110, "y": 190, "w": 30, "h": 100},
                {"team": "BBB", "jersey": "3", "name": "Beta", "x": 150, "y": 200, "w": 40, "h": 100},
                {"team": "BBB", "jersey": "9", "name": "Gamma", "x": 400, "y": 200, "w": 40, "h": 100},
            ]})
        data["identity"] = {"tags": tags}
        doc = highlights.build_highlights(**data, season_dir=Path("/tmp/no-season-here"))
        text = "\n".join(doc["highlights"][0]["card"]["lines"])
        self.assertIn("Beta", text)
        self.assertNotIn("Referee", text)
        self.assertNotIn("Gamma", text)
        self.assertTrue(doc["highlights"][0]["people"]["defender"]["named"])
        self.assertIs(doc["highlights"][0]["people"]["defender"]["agrees"], True)

    def test_render_appends_muted_replays(self):
        work = Path(tempfile.mkdtemp(prefix="hl-"))
        video = work / "src.mp4"
        subprocess.run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=0x14202a:s=640x360:d=22",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=22",
            "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", str(video),
        ], check=True)
        out = work / "out.mp4"
        doc = highlights.render_highlights(
            video, synthetic_inputs()["board"], synthetic_inputs()["playbyplay"],
            shotquality=synthetic_inputs()["shotquality"],
            matchups=synthetic_inputs()["matchups"],
            roster_stats=synthetic_inputs()["roster_stats"],
            season_dir=Path("/tmp/no-season-here"),
            out_video=out, out_json=work / "out.json", work_dir=work / "parts",
            preset="ultrafast", duration=22,
        )
        self.assertTrue(out.is_file())
        self.assertGreaterEqual(doc["outputDuration"], 22 + 20 - 0.5)
        for item in doc["highlights"]:
            self.assertGreaterEqual(item["replay"]["end"] - item["replay"]["start"], 9.8)
        replay = work / "parts" / "replay-0.mp4"
        probe = subprocess.run([
            "ffmpeg", "-hide_banner", "-i", str(replay), "-af", "volumedetect", "-f", "null", "-",
        ], capture_output=True, text=True)
        self.assertIn("mean_volume: -", probe.stderr)
        mean = [line for line in probe.stderr.splitlines() if "mean_volume" in line][-1]
        volume = float(mean.split("mean_volume:")[1].split(" dB")[0])
        self.assertLess(volume, -50)
        shutil.rmtree(work, ignore_errors=True)

    def test_team_rank_stars_follow_rotation_cutoff(self):
        def row(pid, name, team, gp, minutes, pts, fg, blk, ts, defense):
            return pid, name, team, gp, minutes, pts, fg, blk, ts, defense

        base_headers = ["PLAYER_ID", "PLAYER_NAME", "TEAM_ABBREVIATION", "GP", "MIN", "PTS", "FG_PCT", "BLK", "STL", "DREB"]
        adv_headers = ["PLAYER_ID", "PLAYER_NAME", "TEAM_ABBREVIATION", "GP", "MIN", "TS_PCT", "DEF_RATING"]
        people = [
            row(9101, "High", "ZZZ", 40, 1200, 800, 0.5, 20, 0.62, 108),
            row(9102, "Low", "ZZZ", 40, 1200, 400, 0.4, 40, 0.50, 114),
            row(9103, "Short", "ZZZ", 8, 200, 80, 0.6, 10, 0.70, 100),
        ]
        base = {
            "parameters": {"MeasureType": "Base", "TeamID": 99},
            "resultSets": [{"headers": base_headers, "rowSet": [
                [pid, name, team, gp, minutes, pts, fg, blk, 10, 100] for pid, name, team, gp, minutes, pts, fg, blk, _ts, _d in people
            ]}],
        }
        advanced = {
            "parameters": {"MeasureType": "Advanced", "TeamID": 99},
            "resultSets": [{"headers": adv_headers, "rowSet": [
                [pid, name, team, gp, minutes, ts, defense] for pid, name, team, gp, minutes, _pts, _fg, _blk, ts, defense in people
            ]}],
        }
        ranks = highlights.load_team_ranks([base, advanced], Path("/tmp/no-season-here"))
        self.assertNotIn(9103, ranks)
        self.assertEqual(ranks[9101]["ts_pct"]["rank"], 1)
        self.assertEqual(ranks[9101]["ts_pct"]["stars"], 5.0)
        self.assertEqual(ranks[9102]["ts_pct"]["rank"], 2)
        self.assertEqual(ranks[9102]["ts_pct"]["stars"], 4.5)
        self.assertEqual(ranks[9101]["def_rating"]["rank"], 1)
        self.assertEqual(ranks[9102]["blk"]["rank"], 1)
        self.assertEqual(ranks[9101]["pts"]["of"], 2)


def _ensure_exam():
    if (EXAM / "official" / "0022500356_playbyplay.json").is_file():
        return True
    if not PACK.is_file():
        return False
    EXAM.mkdir(parents=True, exist_ok=True)
    subprocess.run(["tar", "-xzf", str(PACK), "-C", str(EXAM)], check=True)
    return (EXAM / "official" / "0022500356_playbyplay.json").is_file()


def _exam_board():
    key = json.loads(ANSWER.read_text(encoding="utf-8"))
    reads = []
    for frame in key["frames"]:
        shot = frame.get("shot_clock")
        try:
            shot_clock = float(shot)
        except (TypeError, ValueError):
            shot_clock = None
        reads.append({
            "t": frame["t"],
            "period": key["game"]["period"],
            "clock": frame["clock"],
            "shot_clock": shot_clock,
        })
    return {"reads": reads, "away": "HOU", "home": "DAL"}, key


class ExamTests(unittest.TestCase):
    def setUp(self):
        if not _ensure_exam() or not ANSWER.is_file():
            self.skipTest("没有考试片官方包，跳过对照")

    def _doc(self, identity=None):
        board, key = _exam_board()
        official = EXAM / "official"
        return highlights.build_highlights(
            board,
            official / "0022500356_playbyplay.json",
            shotquality=official / "0022500356_shotqualityshotlog.json",
            matchups=official / "0022500356_boxscorematchupsv3.json",
            boxscore=official / "0022500356_boxscore.json",
            duration=key["clip"]["duration_sec"],
            identity=identity,
        ), key

    def test_exam_block_and_and_one_cards(self):
        doc, _key = self._doc()
        kinds = [item["type"] for item in doc["highlights"]]
        self.assertEqual(kinds, ["block", "and1_3"], json.dumps(
            [{"type": item["type"], "clock": item["clock"], "key": item["sourceSeconds"]} for item in doc["highlights"]],
            ensure_ascii=False,
        ))
        block, three = doc["highlights"]
        self.assertLess(block["sourceSeconds"]["key"], 10)
        self.assertGreater(three["sourceSeconds"]["key"], 12)
        self.assertLess(three["sourceSeconds"]["key"], 18)
        block_text = "\n".join(block["card"]["lines"])
        three_text = "\n".join(three["card"]["lines"])
        # 这些是标准答案里的官方数字，用来验收，不写进 highlights.py。
        for needle in ("17.7%", "8.95", "53.6%", "58.9%", "5.3", "小史密斯", "克里斯蒂"):
            self.assertIn(needle, block_text, block_text)
        for needle in ("30.0%", "24.85", "40.4%", "37.4%", "36.7%", "35.8%", "1.0", "180/446", "105/281", "1/2", "1/1", "4 分"):
            self.assertIn(needle, three_text, three_text)
        self.assertIn("71", block_text)
        self.assertIn("0.9", block_text)
        self.assertIn("41", block_text)
        self.assertNotIn("臂展", block_text + three_text)
        self.assertNotIn("防守距离", block_text + three_text)
        self.assertNotIn("44:44", block_text + three_text)
        self.assertNotIn("Butler", block_text + three_text)
        self.assertNotIn("卡佩拉", block_text + three_text)
        for item in (block, three):
            text = "\n".join(item["card"]["lines"])
            self.assertIn("队内进攻", text)
            self.assertIn("队内防守", text)
            self.assertNotIn("0星", text)
            starred = [stat["stars"] for stat in item["card"]["stats"] if stat.get("stars")]
            self.assertGreaterEqual(len(starred), 2)
            self.assertTrue(all(value >= 0.5 for value in starred))
        for item in (block, three):
            spoken = "".join(item["commentary"]["zh"]) + " " + "".join(item["commentary"]["en"])
            self.assertLessEqual(len(item["commentary"]["zh"]), 2)
            self.assertLessEqual(len(item["commentary"]["en"]), 2)
            for stat in item["card"]["stats"]:
                if stat["key"] in {"xfg", "distance", "fg3_pct", "zone_fg_pct", "defense_fg"}:
                    self.assertIn(stat["display"], spoken)
        self.assertEqual(block["people"]["defender"]["personId"], 1631095)
        self.assertEqual(block["people"]["shooter"]["personId"], 1631108)
        self.assertEqual([event["highlight"] for event in doc["events"]], ["block", "and1_3"])
        self.assertGreaterEqual(block["replay"]["end"] - block["replay"]["start"], 10)

    def test_exam_identity_disagreement_and_cards(self):
        tags = []
        for t in [i * 0.5 for i in range(0, 48)]:
            tags.append({"t": t, "players": [
                {"team": "DAL", "jersey": "00", "name": "克里斯蒂", "x": 400, "y": 300, "w": 50, "h": 140},
                {"team": "", "jersey": "30", "name": "Butler", "referee": True, "x": 420, "y": 280, "w": 40, "h": 150},
                {"team": "HOU", "jersey": "30", "name": "卡佩拉", "x": 470, "y": 300, "w": 55, "h": 150},
                {"team": "HOU", "jersey": "10", "name": "小史密斯", "x": 700, "y": 280, "w": 48, "h": 150},
            ]})
        doc, _key = self._doc({"tags": tags})
        block = doc["highlights"][0]
        text = "\n".join(block["card"]["lines"]) + "".join(block["commentary"]["zh"])
        self.assertIn("防守人", text)
        self.assertNotIn("小史密斯", text)
        self.assertNotIn("卡佩拉", text)
        self.assertNotIn("Butler", text)
        self.assertIn("17.7%", text)
        self.assertFalse(block["people"]["defender"]["named"])

        board, key = _exam_board()
        official = EXAM / "official"
        # 官方盖帽人离得最近，裁判更近但不能当防守人。
        agree = []
        for t in [i * 0.5 for i in range(0, 48)]:
            agree.append({"t": t, "players": [
                {"team": "DAL", "jersey": "00", "name": "克里斯蒂", "x": 400, "y": 300, "w": 50, "h": 140},
                {"team": "", "jersey": "30", "name": "Butler", "referee": True, "x": 410, "y": 280, "w": 36, "h": 150},
                {"team": "HOU", "jersey": "10", "name": "小史密斯", "x": 460, "y": 300, "w": 48, "h": 150},
                {"team": "HOU", "jersey": "30", "name": "卡佩拉", "x": 760, "y": 300, "w": 55, "h": 150},
            ]})
        agreed = highlights.build_highlights(
            board, official / "0022500356_playbyplay.json",
            shotquality=official / "0022500356_shotqualityshotlog.json",
            matchups=official / "0022500356_boxscorematchupsv3.json",
            boxscore=official / "0022500356_boxscore.json",
            duration=key["clip"]["duration_sec"],
            identity={"tags": agree},
        )
        agreed_text = "\n".join(agreed["highlights"][0]["card"]["lines"])
        self.assertIn("小史密斯", agreed_text)
        self.assertNotIn("Butler", agreed_text)
        self.assertNotIn("卡佩拉", agreed_text)

        stills = Path("/tmp/highlight-stills")
        stills.mkdir(parents=True, exist_ok=True)
        frames = list(EXAM.glob("f_*.jpg"))
        for item, name in ((doc["highlights"][0], "block"), (doc["highlights"][1], "and1")):
            key_t = item["sourceSeconds"]["key"]
            frame = min(frames, key=lambda path: abs(float(path.stem.split("_", 1)[1]) - key_t))
            highlights.burn_card(frame, item, stills / f"{name}.jpg")
            self.assertTrue((stills / f"{name}.jpg").is_file())


if __name__ == "__main__":
    unittest.main()
