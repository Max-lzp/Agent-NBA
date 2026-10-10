"""分析师工具循环、输出结构和「查不到就不编」。不连 AWS。"""

from __future__ import annotations

import ast
import io
import json
import unittest
from pathlib import Path

import agent
import converse_loop
import grounding
import manifest
import season_data
import tools

ROOT = Path(__file__).resolve().parents[1]
CSV_DIR = ROOT / "data" / "season" / "2025-26"


class Missing(Exception):
    def __init__(self):
        super().__init__("NoSuchKey")
        self.response = {"Error": {"Code": "NoSuchKey"}}


class FakeS3:
    def __init__(self, objects=None):
        self.objects = dict(objects or {})
        self.puts = []

    def get_object(self, Bucket, Key):
        del Bucket
        if Key not in self.objects:
            raise Missing()
        body = self.objects[Key]
        if isinstance(body, (bytes, bytearray)):
            raw = body
        elif isinstance(body, str):
            raw = body.encode()
        else:
            raw = json.dumps(body).encode()
        return {"Body": io.BytesIO(raw)}

    def put_object(self, **kwargs):
        self.puts.append(kwargs)
        self.objects[kwargs["Key"]] = kwargs["Body"]

    def list_objects_v2(self, **kwargs):
        prefix = kwargs.get("Prefix") or ""
        keys = [key for key in self.objects if key.startswith(prefix)]
        return {"Contents": [{"Key": key} for key in keys], "IsTruncated": False}


def ctx_for(objects, job_id="job-1", key="structured/job-1.json"):
    media = tools.Media(FakeS3(objects), "media")
    return tools.Ctx(media=media, bucket="media", job_id=job_id, key=key)


def tool_message(calls):
    return {
        "role": "assistant",
        "content": [
            {"toolUse": {"toolUseId": f"t{index}", "name": name, "input": payload}}
            for index, (name, payload) in enumerate(calls)
        ],
    }


def text_message(payload: dict):
    return {"role": "assistant", "content": [{"text": json.dumps(payload, ensure_ascii=False)}]}


class SeasonSliceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = season_data.build_slice(CSV_DIR)

    def test_packaged_file_matches_builder(self):
        packaged = json.loads(season_data.SLICE_PATH.read_text(encoding="utf-8"))
        roundtrip = json.loads(json.dumps(self.doc, ensure_ascii=False))
        self.assertEqual(packaged, roundtrip)

    def test_only_rockets_and_mavericks(self):
        teams = {item["team"] for item in self.doc["players"]}
        self.assertEqual(teams, {"HOU", "DAL"})
        names = {item["player_name"] for item in self.doc["players"]}
        self.assertNotIn("A.J. Lawson", names)
        self.assertIn("Cooper Flagg", names)
        self.assertIn("Amen Thompson", names)

    def test_percent_scaled_and_missing_stays_null(self):
        flagg, _ = season_data.find_player(self.doc, "Flagg", "DAL")
        self.assertAlmostEqual(flagg["offense"]["three_point"]["fg_pct"], 29.5)
        adams, _ = season_data.find_player(self.doc, "Steven Adams", "HOU")
        three = adams["offense"]["three_point"]
        self.assertEqual(three["fga"], 0)
        self.assertIsNone(three["fg_pct"])
        sengun, _ = season_data.find_player(self.doc, "Sengun", "HOU")
        self.assertIsNone(sengun["profile"]["wingspan_cm"])
        self.assertEqual(sengun["profile"]["draft_overall_pick"], 16)
        finney, _ = season_data.find_player(self.doc, "Finney-Smith", "HOU")
        self.assertIsNone(finney["profile"]["draft_overall_pick"])
        self.assertEqual(finney["profile"]["draft_status"], "undrafted")
        self.assertIsNotNone(finney["profile"]["wingspan_cm"])
        holiday, _ = season_data.find_player(self.doc, "Aaron Holiday", "HOU")
        middle = holiday["defense"]["other_two_point_ge_six_ft"]
        self.assertIsNone(middle["normal_fg_pct"])
        self.assertIsNone(middle["diff_pct_points"])
        self.assertIsNotNone(middle["opponent_fg_pct"])
        blob = json.dumps(self.doc, ensure_ascii=False)
        self.assertNotIn("subjective", blob)
        self.assertNotIn("user_unverified", blob)
        self.assertNotIn("替补微波炉", blob)


class ManifestAndOfficialTest(unittest.TestCase):
    def test_check_rejects_other_team(self):
        problems = manifest.check({
            "version": "0.1",
            "possessions": [{"possession_id": "p0", "offense_team": "BOS", "events": []}],
        })
        self.assertTrue(any("HOU/DAL" in item for item in problems))

    def test_clock_and_shot_quality(self):
        self.assertEqual(tools.clock_seconds("PT01M34.00S"), 94)
        self.assertEqual(tools.clock_seconds("1:34"), 94)
        self.assertIsNone(tools.clock_seconds(""))
        store = {
            "official/2025-26/games/0022500701_playbyplay.json": {
                "game": {"actions": [
                    {
                        "actionType": "3pt",
                        "period": 4,
                        "clock": "PT01M34.00S",
                        "teamTricode": "DAL",
                        "shotResult": "Made",
                        "description": "Flagg 26' 3PT",
                        "playerNameI": "C. Flagg",
                        "playerName": "Flagg",
                    },
                    {
                        "actionType": "2pt",
                        "period": 4,
                        "clock": "PT09M00.00S",
                        "teamTricode": "DAL",
                        "shotResult": "Missed",
                        "description": "outside the window",
                        "playerName": "Flagg",
                    },
                ]},
            },
            "official/2025-26/games/0022500701_shotquality.json": {
                "shots": [
                    {"period": 4, "gameClock": "PT01M34.00S", "teamAbbreviation": "DAL", "shotQuality": 0.504, "playerName": "Cooper Flagg"},
                    {"period": 4, "gameClock": "PT01M20.00S", "teamAbbreviation": "DAL", "playerName": "Cooper Flagg"},
                ],
            },
        }
        ctx = ctx_for(store, job_id="clip")
        listed = tools.read_official(ctx, {})
        self.assertFalse(listed["found"])
        self.assertIn("0022500701", listed["game_ids"])
        found = tools.read_official(ctx, {"game_id": "0022500701", "period": 4, "clock_lo_s": 90, "clock_hi_s": 100})
        self.assertEqual(len(found["plays"]), 1)
        self.assertEqual(found["plays"][0]["clock_s"], 94)
        self.assertEqual(found["plays"][0]["xfg_pct"], 50.4)
        whole_game = tools.read_official(ctx_for(store, job_id="clip"), {"game_id": "0022500701"})
        self.assertEqual(whole_game["plays"], [])
        self.assertIn("比赛钟", whole_game["note"])
        ctx.manifest = {
            "version": "0.1",
            "official": {"game_id": "0022500701", "period": 4, "clock_window_s": [100, 90]},
            "possessions": [{"possession_id": "p0", "offense_team": "DAL", "events": []}],
        }
        clipped = tools.read_official(ctx, {"game_id": "0022500701"})
        self.assertEqual([play["clock_s"] for play in clipped["plays"]], [94])
        bare = tools._attach_quality(
            {"kind": "shot", "period": 4, "clock_s": 80, "team": "DAL"},
            tools._quality_index(store["official/2025-26/games/0022500701_shotquality.json"]["shots"]),
        )
        self.assertNotIn("xfg_pct", bare)


class GroundingTest(unittest.TestCase):
    def test_filler_has_no_invented_facts(self):
        self.assertEqual(grounding.ground_text(grounding.FILLER["zh"], []), grounding.FILLER["zh"])
        self.assertEqual(grounding.ground_text(grounding.FILLER["en"], []), grounding.FILLER["en"])

    def test_missing_rate_is_not_zero(self):
        evidence = [{
            "source": "season:2025-26:HOU:Steven Adams",
            "data": {"player_name": "Steven Adams", "three_point": {"fgm": 0, "fga": 0, "fg_pct": None}},
        }]
        self.assertEqual(grounding.ground_text("Adams 的三分命中率是 0%。", evidence), "")
        kept = grounding.ground_text("Adams 三分出手 0 次。", evidence)
        self.assertIn("0", kept)
        self.assertIn("Adams", kept)


class LoopTest(unittest.TestCase):
    def setUp(self):
        season_data._CACHE = None
        self.flagg_pct = season_data.find_player(season_data.load_slice(), "Flagg", "DAL")[0]["offense"]["three_point"]["fg_pct"]

    def manifest(self):
        return {
            "version": "0.1",
            "jobId": "job-1",
            "gameId": "0022500701",
            "possessions": [{
                "possession_id": "p0",
                "offense_team": "DAL",
                "events": [{
                    "t": 4.2,
                    "type": "shot",
                    "actor": {"team": "DAL", "number": "32", "name": "Flagg"},
                    "confidence": 0.8,
                    "source": "official",
                }],
            }],
        }

    def objects(self):
        return {
            "structured/job-1.json": self.manifest(),
            "identity/job-1.json": {
                "pinned": [{"team": "DAL", "number": "32", "nameZh": "弗拉格", "role": "scorer"}],
                "firstSecond": ["弗拉格"],
                "matches": [{"t": 4.2, "team": "DAL", "number": "32", "name": "弗拉格"}],
            },
            "official/2025-26/games/0022500701_playbyplay.json": {
                "game": {"actions": [{
                    "actionType": "3pt",
                    "period": 4,
                    "clock": "PT01M34.00S",
                    "teamTricode": "DAL",
                    "shotResult": "Made",
                    "description": "Flagg 26' 3PT",
                    "playerName": "Flagg",
                    "playerNameI": "C. Flagg",
                }]},
            },
            "official/2025-26/games/0022500701_shotquality.json": {
                "shots": [{
                    "period": 4,
                    "gameClock": "PT01M34.00S",
                    "teamAbbreviation": "DAL",
                    "shotQuality": 0.504,
                    "playerName": "Cooper Flagg",
                }],
            },
        }

    def test_tool_loop_writes_grounded_analysis(self):
        seen = []

        def converse(messages, *, tool_choice):
            seen.append((tool_choice, messages))
            if len(seen) == 1:
                return tool_message([
                    ("validate_manifest", {}),
                    ("read_manifest", {}),
                    ("read_identity", {}),
                    ("read_official", {"game_id": "0022500701", "period": 4, "clock_lo_s": 90, "clock_hi_s": 100}),
                    ("read_season", {"name": "Flagg", "team": "DAL"}),
                ])
            self.assertEqual(tool_choice, {"auto": {}})
            blob = json.dumps(messages, ensure_ascii=False)
            self.assertIn("toolResult", blob)
            self.assertIn("50.4", blob)
            return text_message({
                "possessions": [{
                    "possession_id": "p0",
                    "commentary_zh": "弗拉格接到球。",
                    "commentary_en": "Flagg gets the ball.",
                    "sources": ["structured/job-1.json"],
                }],
                "shots": [{
                    "t": 4.2,
                    "freeze_zh": "弗拉格这球进了，预期命中率 50.4%。",
                    "freeze_en": "Flagg makes it, expected make rate 50.4 percent.",
                    "commentary_zh": f"赛季三分命中率 {self.flagg_pct}%。",
                    "commentary_en": f"Season three-point rate is {self.flagg_pct} percent.",
                    "sources": ["official/2025-26/games/0022500701_playbyplay.json"],
                }],
                "highlights": [
                    {"zh": "弗拉格这球进了，预期命中率 50.4%。", "en": "Flagg makes it, expected make rate 50.4 percent.", "sources": ["official/2025-26/games/0022500701_playbyplay.json"]},
                    {"zh": f"赛季三分命中率 {self.flagg_pct}%。", "en": f"Season three-point rate is {self.flagg_pct} percent.", "sources": ["season:2025-26:DAL:Cooper Flagg"]},
                    {"zh": grounding.FILLER["zh"], "en": grounding.FILLER["en"], "sources": []},
                ],
            })

        store = FakeS3(self.objects())
        result = agent.invoke(
            {"bucket": "media", "jobId": "job-1", "key": "structured/job-1.json"},
            s3=store,
            converse=converse,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["runtime"], "bedrock-agentcore")
        self.assertEqual(result["stream"]["gameId"], "0022500701")
        analysis = result["analysis"]
        self.assertEqual(len(analysis["highlights"]), 3)
        self.assertEqual(analysis["possessions"][0]["commentaryEn"], "Flagg gets the ball.")
        self.assertIn("50.4", analysis["shots"][0]["freezeZh"])
        self.assertIn(str(self.flagg_pct), analysis["shots"][0]["commentaryZh"])
        self.assertTrue(analysis["shots"][0]["sources"])
        self.assertEqual(analysis["model"], "global.anthropic.claude-sonnet-4-6")
        self.assertEqual(len(seen), 2)
        self.assertEqual(store.puts[0]["Key"], "analysis/job-1.json")

    def test_missing_data_is_not_invented(self):
        def converse(messages, *, tool_choice):
            del messages, tool_choice
            if not hasattr(converse, "called"):
                converse.called = True
                return tool_message([
                    ("validate_manifest", {}),
                    ("read_manifest", {}),
                    ("read_identity", {}),
                    ("read_official", {"game_id": "0000000000"}),
                    ("read_season", {"name": "ZZZ Unknown", "team": "DAL"}),
                ])
            return text_message({
                "possessions": [{
                    "possession_id": "p0",
                    "commentary_zh": "杜兰特三分命中率 41.2%，预期命中率 62。",
                    "commentary_en": "Durant shot 41.2 percent with an expected make rate of 62.",
                    "sources": ["official/made-up.json"],
                }],
                "shots": [{
                    "t": 9,
                    "freeze_zh": "杜兰特砍下 41.2 分。",
                    "freeze_en": "Durant scores 41.2.",
                    "commentary_zh": "",
                    "commentary_en": "",
                    "sources": [],
                }],
                "highlights": [
                    {"zh": "杜兰特本场得了 62 分。", "en": "Durant scored 62."},
                    {"zh": "命中率 41.2%。", "en": "The rate is 41.2 percent."},
                    {"zh": "神秘球员投进了。", "en": "Somebody made it."},
                ],
            })

        store = FakeS3({
            "structured/job-2.json": {
                "version": "0.1",
                "jobId": "job-2",
                "possessions": [{"possession_id": "p0", "offense_team": "HOU", "events": []}],
            },
        })
        result = agent.invoke(
            {"bucket": "media", "key": "structured/job-2.json"},
            s3=store,
            converse=converse,
        )
        self.assertTrue(result["ok"])
        blob = json.dumps(result["analysis"], ensure_ascii=False)
        for banned in ("41.2", "杜兰特", "Durant", "62", "神秘球员"):
            self.assertNotIn(banned, blob)
        self.assertEqual(result["analysis"]["highlights"], [{
            "zh": grounding.FILLER["zh"],
            "en": grounding.FILLER["en"],
            "sources": [],
        }])
        self.assertEqual(result["analysis"]["shots"], [])
        written = json.loads(store.puts[0]["Body"].decode())
        self.assertNotIn("41.2", json.dumps(written, ensure_ascii=False))

    def test_question_mode_does_not_overwrite_analysis(self):
        def converse(messages, *, tool_choice):
            del tool_choice
            if not any(item["role"] == "assistant" for item in messages):
                return tool_message([
                    ("read_manifest", {}),
                    ("read_official", {"game_id": "0022500701", "period": 4, "clock_lo_s": 90, "clock_hi_s": 100}),
                ])
            return text_message({
                "answer": {
                    "zh": "这球预期命中率 50.4%。",
                    "en": "The expected make rate is 50.4 percent.",
                    "sources": ["official/2025-26/games/0022500701_playbyplay.json"],
                },
            })

        store = FakeS3(self.objects())
        result = agent.invoke(
            {"bucket": "media", "jobId": "job-1", "question": "这一球的预期命中率是多少？"},
            s3=store,
            converse=converse,
        )
        self.assertEqual(result["mode"], "qa")
        self.assertIn("50.4", result["answer"]["zh"])
        self.assertTrue(result["answer"]["en"])
        self.assertTrue(result["answer"]["sources"])
        self.assertEqual(store.puts, [])

    def warm_manifest(self):
        return {
            "version": "0.1",
            "jobId": "warm-probe",
            "possessions": [{
                "possession_id": "p0",
                "offense_team": "DAL",
                "events": [{
                    "t": 3.0,
                    "type": "receive",
                    "actor": {"team": "DAL", "number": "13", "name": "N. Marshall"},
                }],
            }],
        }

    def test_chinese_question_fills_zh_with_canonical_name(self):
        def converse(messages, *, tool_choice):
            del tool_choice
            if not any(item["role"] == "assistant" for item in messages):
                return tool_message([("read_manifest", {})])
            return text_message({
                "answer": {
                    "zh": "",
                    "en": "He is involved in this clip.",
                    "sources": ["structured/warm-probe.json"],
                },
            })

        store = FakeS3({"structured/warm-probe.json": self.warm_manifest()})
        result = agent.invoke(
            {"bucket": "media", "jobId": "warm-probe", "question": "这段里马歇尔做了什么？"},
            s3=store,
            converse=converse,
        )
        answer = result["answer"]
        self.assertIn("马绍尔", answer["zh"])
        self.assertIn("接到球", answer["zh"])
        self.assertNotIn("马歇尔", answer["zh"])
        self.assertNotIn("歇", answer["zh"])
        self.assertEqual(answer["en"], "He is involved in this clip.")
        self.assertEqual(answer["sources"], ["structured/warm-probe.json"])
        self.assertEqual(store.puts, [])

    def test_wrong_marshall_spelling_is_kept_in_chinese(self):
        def converse(messages, *, tool_choice):
            del tool_choice
            if not any(item["role"] == "assistant" for item in messages):
                return tool_message([("read_manifest", {})])
            return text_message({
                "answer": {
                    "zh": "马歇尔把球传了出去。",
                    "en": "Marshall passes the ball.",
                },
            })

        store = FakeS3({"structured/warm-probe.json": self.warm_manifest()})
        result = agent.invoke(
            {"bucket": "media", "jobId": "warm-probe", "question": "这段里马歇尔做了什么？"},
            s3=store,
            converse=converse,
        )
        answer = result["answer"]
        self.assertIn("马绍尔", answer["zh"])
        self.assertIn("传了出去", answer["zh"])
        self.assertNotIn("马歇尔", answer["zh"])
        self.assertIn("Marshall", answer["en"])

    def test_pipeline_keeps_one_copy_of_the_same_highlight(self):
        def converse(messages, *, tool_choice):
            del messages, tool_choice
            return text_message({
                "highlights": [
                    {"zh": "没有更多看点", "en": "No further highlight."},
                    {"zh": "没有更多看点。", "en": "No further highlight"},
                    {"zh": "没有更多看点。", "en": "No further highlight."},
                ],
            })

        store = FakeS3({"structured/warm-probe.json": self.warm_manifest()})
        result = agent.invoke(
            {"bucket": "media", "jobId": "warm-probe", "key": "structured/warm-probe.json"},
            s3=store,
            converse=converse,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["analysis"]["highlights"]), 1)
        kept = result["analysis"]["highlights"][0]
        self.assertIn("没有更多看点", kept["zh"])
        self.assertEqual(kept["en"], "No further highlight.")

    def test_bad_manifest_skips_the_model(self):
        def converse(messages, *, tool_choice):
            del messages, tool_choice
            raise AssertionError("坏清单不应该再调模型")

        result = agent.invoke(
            b'{"bucket":"media","jobId":"job-3","key":"structured/job-3.json"}',
            s3=FakeS3({"structured/job-3.json": {"version": "0.9", "possessions": []}}),
            converse=converse,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "invalid_manifest")
        self.assertIsNone(result["stream"])


class NamesTest(unittest.TestCase):
    def test_roster_matches_zh_names_file(self):
        doc = json.loads((ROOT / "data" / "names" / "zh-names.json").read_text(encoding="utf-8"))
        expected = tuple(
            (item["team"], item["name_en"], item["name_zh"], item["alias_zh"])
            for item in doc["players"]
        )
        self.assertEqual(season_data.ROSTER, expected)

    def test_marshall_alias_and_ambiguous_surnames(self):
        def alias(name):
            person = season_data.match_name(name)
            return None if person is None else person["alias_zh"]

        self.assertEqual(alias("马歇尔"), "马绍尔")
        self.assertEqual(alias("马绍尔"), "马绍尔")
        self.assertEqual(alias("Marshall"), "马绍尔")
        self.assertEqual(alias("N. Marshall"), "马绍尔")
        self.assertEqual(alias("Naji Marshall"), "马绍尔")
        self.assertIsNone(alias("Thompson"))
        self.assertIsNone(alias("Smith"))
        self.assertEqual(alias("Amen Thompson"), "阿门")
        self.assertEqual(alias("K. Thompson"), "汤普森")
        self.assertEqual(alias("Klay Thompson"), "汤普森")
        self.assertEqual(alias("Jabari Smith"), "小史密斯")
        self.assertEqual(alias("T. Smith"), "史密斯")
        self.assertEqual(alias("Finney-Smith"), "芬尼-史密斯")
        self.assertEqual(alias("Dorian Finney-Smith"), "芬尼-史密斯")

    def test_tools_use_canonical_zh_name(self):
        ctx = ctx_for({
            "structured/warm-probe.json": {
                "version": "0.1",
                "possessions": [{
                    "possession_id": "p0",
                    "offense_team": "DAL",
                    "events": [{
                        "t": 1.2,
                        "type": "receive",
                        "actor": {"team": "DAL", "number": "13", "name": "马歇尔"},
                    }],
                }],
            },
        }, job_id="warm-probe", key="structured/warm-probe.json")
        actor = tools.read_manifest(ctx, {})["possessions"][0]["events"][0]["actor"]
        self.assertEqual(actor["name"], "马绍尔")
        self.assertEqual(actor["name_zh"], "马绍尔")
        self.assertEqual(actor["name_en"], "Naji Marshall")
        season = tools.read_season(ctx_for({}), {"name": "N. Marshall", "team": "DAL"})
        self.assertEqual(season["player_name"], "Naji Marshall")
        self.assertEqual(season["name_zh"], "马绍尔")

    def test_near_name_is_rewritten_and_other_names_stay_out(self):
        evidence = [{"source": "s", "data": {"player": "N. Marshall", "type": "receive"}}]
        text = grounding.ground_text("马歇尔在这段里接到球。", evidence)
        self.assertIn("马绍尔", text)
        self.assertNotIn("马歇尔", text)
        self.assertEqual(grounding.ground_text("杜兰特在这段里接到球。", evidence), "")
        klay = [{"source": "s", "data": {"player_name": "Klay Thompson"}}]
        self.assertIn("汤普森", grounding.ground_text("汤普森出手。", klay))
        self.assertEqual(grounding.ground_text("阿门出手。", klay), "")
        finney = [{"source": "s", "data": {"player_name": "Dorian Finney-Smith"}}]
        self.assertIn("芬尼-史密斯", grounding.ground_text("芬尼-史密斯出手。", finney))
        self.assertEqual(grounding.ground_text("史密斯出手。", finney), "")


class ColdStartTest(unittest.TestCase):
    def test_entrypoint_does_not_import_heavy_packages(self):
        root = Path(__file__).resolve().parent
        banned = {"boto3", "botocore", "bedrock_agentcore", "strands", "cv2"}
        for path in root.glob("*.py"):
            if path.name.startswith("test_"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    self.assertNotIn(name.split(".")[0], banned, path.name)
        requirements = (root / "requirements.txt").read_text(encoding="utf-8")
        for line in requirements.splitlines():
            code = line.split("#", 1)[0]
            self.assertNotIn("bedrock-agentcore", code)
            self.assertNotIn("strands", code)


if __name__ == "__main__":
    unittest.main()
