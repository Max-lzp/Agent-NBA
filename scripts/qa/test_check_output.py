"""成片自检。探测作业 warm-probe、qwen-flagg 的字段形状来自 inference/chain.py 和 pipeline-map。

    python3 -m unittest scripts.qa.test_check_output
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.qa.check_output import (
    Fetched,
    MEDIA_BUCKET,
    assess_dynamodb,
    assess_probe,
    assess_scoreboard,
    build_checks,
    build_parser,
    clock_seconds,
    main,
    official_score_at,
    parse_clips_js,
    period_number,
    render_markdown,
    iter_official_plays,
)


def exam_game() -> dict:
    """第四节 1:52 前后。10 人在场，比分先是 100-107，再到 103-107。"""
    roster = [
        (1, "HOU", "1", "Thompson"),
        (2, "HOU", "28", "Sengun"),
        (3, "HOU", "7", "Durant"),
        (4, "HOU", "10", "Smith"),
        (5, "HOU", "30", "Capela"),
        (6, "DAL", "32", "Flagg"),
        (7, "DAL", "5", "Russell"),
        (8, "DAL", "3", "Davis"),
        (9, "DAL", "31", "Thompson"),
        (10, "DAL", "7", "Powell"),
    ]
    players = {
        pid: {"team": team, "jersey": jersey, "name": name, "full_name": name}
        for pid, team, jersey, name in roster
    }
    plays = []
    for index, (pid, team, _jersey, _name) in enumerate(roster, start=1):
        plays.append({
            "action_number": index,
            "period": 4,
            "clock_s": 700 - index,
            "person_id": pid,
            "assist_id": None,
            "kind": "rebound",
            "sub_type": "",
            "team": team,
            "score_away": None,
            "score_home": None,
        })
    plays.append({
        "action_number": 50,
        "period": 4,
        "clock_s": 200,
        "person_id": 6,
        "assist_id": None,
        "kind": "shot",
        "sub_type": "",
        "team": "DAL",
        "score_away": 100,
        "score_home": 107,
    })
    plays.append({
        "action_number": 60,
        "period": 4,
        "clock_s": 120,
        "person_id": 6,
        "assist_id": 8,
        "kind": "shot",
        "sub_type": "",
        "team": "DAL",
        "score_away": 103,
        "score_home": 107,
    })
    return {"players": players, "plays": plays, "away": "DAL", "home": "HOU"}


def quiet_game() -> dict:
    """warm-probe 那种比分不跳的短片：第二节，80-77 一直没变。"""
    players = {}
    for pid in range(1, 6):
        players[pid] = {"team": "HOU", "jersey": str(pid), "name": f"H{pid}"}
    for pid in range(6, 11):
        players[pid] = {"team": "DAL", "jersey": str(pid), "name": f"D{pid}"}
    return {
        "players": players,
        "away": "DAL",
        "home": "HOU",
        "plays": [{
            "action_number": 1,
            "period": 2,
            "clock_s": 460.0,
            "person_id": 1,
            "assist_id": None,
            "kind": "shot",
            "sub_type": "",
            "team": "HOU",
            "score_away": 80,
            "score_home": 77,
        }],
    }


def warm_stream() -> dict:
    return {
        "version": "0.1",
        "jobId": "warm-probe",
        "gameId": "0022500356",
        "possessions": [{
            "possession_id": "p0",
            "offense_team": "HOU",
            "events": [
                {"t": 0.2, "type": "receive", "actor": {"name": "持球人", "team": "HOU"}, "source": "cv", "confidence": 0.4},
                {"t": 1.4, "type": "pass", "actor": {"name": "持球人", "team": "HOU"}, "target": {"name": "持球人", "team": "HOU"}, "source": "cv", "confidence": 0.4},
                {"t": 3.1, "type": "pass", "actor": {"name": "持球人", "team": "HOU"}, "target": {"name": "持球人", "team": "HOU"}, "source": "cv", "confidence": 0.4},
            ],
        }],
    }


def warm_scorebug() -> list[dict]:
    rows = []
    for index, second in enumerate((29, 28, 27, 26, 25, 24)):
        rows.append({
            "t": round(index * 0.8, 3),
            "period": "第二节",
            "clock": f"7:{second:02d}",
            "away": "DAL",
            "home": "HOU",
            "awayScore": 80,
            "homeScore": 77,
            "awayName": "独行侠",
            "homeName": "火箭",
        })
    return rows


def exam_scorebug() -> list[dict]:
    return [{
        "t": 0.0,
        "period": "第四节",
        "clock": "1:52",
        "away": "DAL",
        "home": "HOU",
        "awayScore": 103,
        "homeScore": 107,
        "awayName": "独行侠",
        "homeName": "火箭",
    }]


QWEN_NAMES = [
    ("HOU", "1", "阿门"),
    ("HOU", "28", "申京"),
    ("HOU", "7", "杜兰特"),
    ("HOU", "10", "史密斯"),
    ("HOU", "30", "卡佩拉"),
    ("DAL", "32", "弗拉格"),
    ("DAL", "5", "拉塞尔"),
    ("DAL", "3", "戴维斯"),
]


def qwen_identity(extra: list | None = None) -> dict:
    votes = []
    matches = []
    names = []
    for index, (team, number, name) in enumerate(QWEN_NAMES, start=1):
        votes.append({
            "id": index,
            "number": number,
            "nameZh": name,
            "team": team,
            "ballots": 4,
            "samples": 6,
            "color": "white" if team == "HOU" else "dark",
            "source": "qwen",
            "rosterName": name,
        })
        matches.append({"t": 0.4, "team": team, "number": number, "name": name})
        names.append(name)
    if extra:
        votes.extend(extra)
    return {
        "jobId": "qwen-flagg",
        "service": "bedrock",
        "model": "qwen.qwen3-vl-235b-a22b",
        "bedrockCalls": 513,
        "rekognitionCalls": 400,
        "textractCalls": 0,
        "identitySeconds": 113.0,
        "firstSecond": names,
        "pinned": [],
        "votes": votes,
        "reads": [],
        "matches": matches,
    }


def probe(duration: str = "5.2") -> dict:
    return {
        "streams": [
            {"codec_type": "video", "width": 1280, "height": 720},
            {"codec_type": "audio", "codec_name": "aac", "channels": 2},
        ],
        "format": {"duration": duration, "size": "2000000"},
    }


def http_ok(url: str, kind: str) -> dict:
    size = 12000 if kind == "page" else 2_000_000
    return {"status": 200, "size": size, "url": url, "error": ""}


def warm_fetched() -> Fetched:
    urls = {
        "root": "https://d2vzd2batw1eqe.cloudfront.net/",
        "readme": "https://d2vzd2batw1eqe.cloudfront.net/readme",
        "broadcast": "https://d2vzd2batw1eqe.cloudfront.net/assets/warm-probe-broadcast.mp4",
        "en": "https://d2vzd2batw1eqe.cloudfront.net/assets/warm-probe-en.mp4",
        "zh": "https://d2vzd2batw1eqe.cloudfront.net/assets/warm-probe-zh.mp4",
    }
    return Fetched(
        http={key: http_ok(url, "page" if key in {"root", "readme"} else "mp4") for key, url in urls.items()},
        probes={key: probe() for key in ("broadcast", "en", "zh")},
        job={"jobId": "warm-probe", "status": "DONE", "eventCount": 3, "analysisMode": "structured", "modelUsed": "lambda"},
        stream=warm_stream(),
        identity={
            "jobId": "warm-probe",
            "service": "bedrock",
            "model": "qwen.qwen3-vl-235b-a22b",
            "bedrockCalls": 0,
            "identitySeconds": 0,
            "firstSecond": ["阿门"],
            "votes": [{
                "id": 1, "number": "1", "nameZh": "阿门", "team": "HOU",
                "ballots": 3, "samples": 4, "source": "qwen",
            }],
            "matches": [{"t": 0.4, "team": "HOU", "number": "1", "name": "阿门"}],
        },
        scorebug=warm_scorebug(),
        game=quiet_game(),
        game_id="0022500356",
        stem="warm-probe",
    )


def qwen_fetched(identity: dict | None = None) -> Fetched:
    fetched = warm_fetched()
    fetched.stem = "qwen-flagg"
    fetched.game = exam_game()
    fetched.game_id = "0022500701"
    fetched.scorebug = exam_scorebug()
    fetched.identity = identity or qwen_identity()
    fetched.stream = {
        "version": "0.1",
        "jobId": "qwen-flagg",
        "gameId": "0022500701",
        "possessions": [{
            "possession_id": "p0",
            "offense_team": "DAL",
            "events": [
                {"t": 0.4, "type": "receive", "actor": {"name": "弗拉格", "team": "DAL", "number": "32"}, "source": "qwen", "confidence": 0.7},
                {"t": 8.0, "type": "shot", "actor": {"name": "弗拉格", "team": "DAL", "number": "32"}, "source": "official", "confidence": 0.8,
                 "official": {"made": True, "value": 3}},
            ],
        }],
    }
    fetched.job = {"jobId": "qwen-flagg", "status": "DONE", "eventCount": 2, "analysisMode": "structured"}
    return fetched


class CheckOutputTest(unittest.TestCase):
    def test_parser_defaults_do_not_publish(self):
        args = build_parser().parse_args(["warm-probe"])
        self.assertFalse(args.publish)
        self.assertEqual(args.profile, "nba")
        self.assertEqual(args.region, "us-east-1")
        self.assertEqual(args.media_bucket, MEDIA_BUCKET)
        self.assertEqual(args.clip, "")

    def test_period_and_clock(self):
        self.assertEqual(period_number("第四节"), 4)
        self.assertEqual(period_number("4TH"), 4)
        self.assertEqual(period_number(4), 4)
        self.assertEqual(clock_seconds("1:52"), 112)
        self.assertEqual(clock_seconds("PT01M52.00S"), 112)

    def test_official_score_uses_the_latest_play(self):
        plays = iter_official_plays(exam_game())
        self.assertEqual(official_score_at(plays, 4, 112)[0], (103, 107))
        self.assertEqual(official_score_at(plays, 4, 150)[0], (100, 107))

    def test_warm_probe_report_can_submit(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            code = main(
                ["warm-probe", "--out", tmp],
                fetcher=lambda _args: warm_fetched(),
                publisher=lambda *_args: calls.append(_args),
            )
            text = Path(tmp, "warm-probe.md").read_text(encoding="utf-8")
            payload = json.loads(Path(tmp, "warm-probe.json").read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        self.assertEqual(calls, [])
        self.assertTrue(text.startswith("可以提交。"))
        self.assertEqual(payload["conclusion"], "可以提交。")
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["published"])
        structured = next(item for item in payload["checks"] if item["name"] == "structured")
        self.assertIn("3 条事件", structured["detail"])
        self.assertIn("出手 0 条", structured["detail"])
        self.assertNotIn("shot", [event["type"] for event in warm_stream()["possessions"][0]["events"]])

    def test_publish_is_opt_in_and_failure_stays_in_json(self):
        def publisher(_html, _args):
            return {"ok": False, "detail": "上传失败"}

        with tempfile.TemporaryDirectory() as tmp:
            code = main(
                ["warm-probe", "--out", tmp, "--publish"],
                fetcher=lambda _args: warm_fetched(),
                publisher=publisher,
            )
            text = Path(tmp, "warm-probe.md").read_text(encoding="utf-8")
            payload = json.loads(Path(tmp, "warm-probe.json").read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        self.assertTrue(text.startswith("可以提交。"))
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["published"])
        self.assertEqual(payload["publishDetail"], "上传失败")

    def test_http_and_job_failures_do_not_raise(self):
        fetched = warm_fetched()
        fetched.http["broadcast"] = {
            "status": 404,
            "size": 0,
            "url": "https://d2vzd2batw1eqe.cloudfront.net/assets/warm-probe-broadcast.mp4",
            "error": "",
        }
        fetched.http["root"] = {"status": 500, "size": 0, "url": "https://d2vzd2batw1eqe.cloudfront.net/", "error": ""}
        fetched.job = {"jobId": "warm-probe", "status": "FAILED", "eventCount": 0}
        checks = build_checks("warm-probe", fetched)
        text = render_markdown("warm-probe", checks)
        self.assertTrue(text.startswith("有问题："))
        self.assertIn("建议：", text.splitlines()[0])
        self.assertIn("404", text)
        self.assertIn("FAILED", text)
        self.assertIn("500", text)

    def test_empty_fetch_is_a_report(self):
        checks = build_checks("warm-probe", Fetched(stem="warm-probe"))
        text = render_markdown("warm-probe", checks)
        self.assertEqual(len(checks), 13)
        self.assertTrue(text.startswith("有问题："))
        self.assertIn("记分牌", text)

    def test_qwen_flagg_eight_names_are_on_court(self):
        checks = build_checks("qwen-flagg", qwen_fetched())
        names = next(item for item in checks if item["name"] == "names-on-court")
        duplicate = next(item for item in checks if item["name"] == "names-duplicate")
        self.assertTrue(names["ok"], names["detail"])
        self.assertTrue(duplicate["ok"])
        self.assertIn("513", names["detail"])
        self.assertIn("113", names["detail"])
        self.assertIn("第 1 秒 8 个名字", names["detail"])
        self.assertIn("qwen.qwen3-vl-235b-a22b", names["detail"])

    def test_same_name_on_two_tracks(self):
        identity = qwen_identity([{
            "id": 99,
            "number": "32",
            "nameZh": "弗拉格",
            "team": "DAL",
            "ballots": 2,
            "samples": 4,
            "source": "qwen",
        }])
        checks = build_checks("qwen-flagg", qwen_fetched(identity))
        duplicate = next(item for item in checks if item["name"] == "names-duplicate")
        text = render_markdown("qwen-flagg", checks)
        self.assertFalse(duplicate["ok"])
        self.assertIn("99", duplicate["detail"])
        self.assertTrue(text.startswith("有问题："))
        self.assertIn("两条轨迹", text.splitlines()[0])

    def test_name_not_on_court(self):
        identity = qwen_identity([{
            "id": 11,
            "number": "8",
            "nameZh": "泰特",
            "team": "HOU",
            "ballots": 3,
            "samples": 4,
            "source": "qwen",
        }])
        checks = build_checks("qwen-flagg", qwen_fetched(identity))
        names = next(item for item in checks if item["name"] == "names-on-court")
        self.assertFalse(names["ok"])
        self.assertIn("泰特", names["detail"])
        self.assertIn("不在场上", names["detail"])

    def test_roster_name_without_name_zh_is_not_written(self):
        identity = {
            "jobId": "qwen-flagg",
            "bedrockCalls": 513,
            "identitySeconds": 113,
            "firstSecond": [],
            "votes": [{"id": 1, "number": "32", "rosterName": "Flagg", "team": "DAL", "source": "qwen"}],
            "matches": [],
        }
        from scripts.qa.check_output import assess_names

        rows = assess_names(identity, "", exam_scorebug(), exam_game(), "")
        names = next(item for item in rows if item["name"] == "names-on-court")
        self.assertFalse(names["ok"])
        self.assertIn("没有写出中文名", names["detail"])

    def test_score_mismatch_and_swapped_sides(self):
        wrong = [dict(row, awayScore=100, homeScore=107) for row in exam_scorebug()]
        missed = assess_scoreboard(wrong, "", exam_game(), "", "0022500701")
        self.assertFalse(missed["ok"])
        self.assertIn("103-107", missed["detail"])
        swapped = [dict(row, awayScore=107, homeScore=103) for row in exam_scorebug()]
        flipped = assess_scoreboard(swapped, "", exam_game(), "", "0022500701")
        self.assertIn("反了", flipped["detail"])

    def test_clock_jump(self):
        rows = exam_scorebug() + [{
            "t": 3,
            "period": "第四节",
            "clock": "2:10",
            "awayScore": 100,
            "homeScore": 107,
        }]
        result = assess_scoreboard(rows, "", exam_game(), "", "0022500701")
        self.assertFalse(result["ok"])
        self.assertIn("回到", result["detail"])

    def test_bad_resolution_and_missing_audio(self):
        wide = {
            "streams": [
                {"codec_type": "video", "width": 640, "height": 360},
                {"codec_type": "audio"},
            ],
            "format": {"duration": "24"},
        }
        silent = {
            "streams": [{"codec_type": "video", "width": 1280, "height": 720}],
            "format": {"duration": "24"},
        }
        http = {"status": 200, "size": 2_000_000}
        self.assertIn("640x360", assess_probe("probe-broadcast", wide, http)["detail"])
        self.assertIn("没有音轨", assess_probe("probe-broadcast", silent, http)["detail"])

    def test_event_count_mismatch(self):
        row = assess_dynamodb({"status": "DONE", "eventCount": 1}, "", warm_stream())
        self.assertFalse(row["ok"])
        self.assertIn("对不上", row["short"])

    def test_clips_js_lead_prefix(self):
        text = 'window.DEEPCOURT_LEAD="exam";\nwindow.DEEPCOURT_CLIPS={"exam":{"scorebug":[{"t":0}]}};\n'
        data = parse_clips_js(text)
        self.assertEqual(data["exam"]["scorebug"][0]["t"], 0)

    def test_bad_job_id_exits_without_a_report(self):
        self.assertEqual(main(["../etc"]), 2)


if __name__ == "__main__":
    unittest.main()
