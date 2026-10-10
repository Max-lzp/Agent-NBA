#!/usr/bin/env python3
"""成片自动自检。检查失败只写入报告，不中途退出。

    python3 scripts/qa/check_output.py JOB_ID
    python3 scripts/qa/check_output.py JOB_ID --clip exam
    python3 scripts/qa/check_output.py JOB_ID --publish

默认把中文 Markdown 和 JSON 写到 scripts/qa/out/。
--publish 才把 HTML 传到网页桶 qa/{jobId}.html，并只失效这一条路径。

CloudFront、媒体桶、Jobs 表和官方逐回合都按 docs/pipeline-map.md 的考试路径读。
本机需要 aws（profile nba、us-east-1）和 ffprobe。没有凭证时由队员在 macOS 上跑。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.enrich.official import load_game, on_court  # noqa: E402

CLOUDFRONT = "https://d2vzd2batw1eqe.cloudfront.net"
MEDIA_BUCKET = "team04deepcourt-mediabucketbcbb02ba-haisfxujonfg"
STACK = "Team04DeepCourt"
REGION = "us-east-1"
PROFILE = "nba"
PAGE_MIN = 400
PAGE_MAX = 5_000_000
MP4_MIN = 50_000
MP4_MAX = 80_000_000
DURATION_MIN = 3.0
DURATION_MAX = 120.0
RESOLUTIONS = {(1280, 720), (1920, 1080)}
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,80}")
CLOCK_ISO = re.compile(r"PT(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?")
CLOCK_TEXT = re.compile(r"(\d+):(\d+(?:\.\d+)?)")

CHECK_ORDER = (
    "cloudfront-root",
    "cloudfront-readme",
    "film-broadcast",
    "film-en",
    "film-zh",
    "probe-broadcast",
    "probe-en",
    "probe-zh",
    "structured",
    "dynamodb",
    "names-duplicate",
    "names-on-court",
    "scoreboard",
)
GROUPS = (
    ("CloudFront", ("cloudfront-root", "cloudfront-readme")),
    ("成片和音轨", ("film-broadcast", "film-en", "film-zh", "probe-broadcast", "probe-en", "probe-zh")),
    ("清单和任务", ("structured", "dynamodb")),
    ("认人", ("names-duplicate", "names-on-court")),
    ("记分牌", ("scoreboard",)),
)


@dataclass
class Fetched:
    """一次自检读到的材料。测试直接构造，不连 AWS。"""

    http: dict = field(default_factory=dict)
    probes: dict = field(default_factory=dict)
    job: dict | None = None
    job_error: str = ""
    stream: dict | None = None
    stream_error: str = ""
    identity: dict | None = None
    identity_error: str = ""
    scorebug: list = field(default_factory=list)
    clips_error: str = ""
    game: dict | None = None
    game_id: str = ""
    game_error: str = ""
    stem: str = ""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="检查云上成片能不能交")
    parser.add_argument("job_id", help="Batch / DynamoDB 的 jobId")
    parser.add_argument("--clip", default="", help="成片文件名里的片名。不填就用 jobId")
    parser.add_argument("--publish", action="store_true", help="把 HTML 报告传到网页桶 qa/{jobId}.html。默认不传")
    parser.add_argument("--out", default="", help="报告目录，默认 scripts/qa/out")
    parser.add_argument("--profile", default=PROFILE)
    parser.add_argument("--region", default=REGION)
    parser.add_argument("--media-bucket", default=MEDIA_BUCKET)
    parser.add_argument("--web-bucket", default="")
    parser.add_argument("--cloudfront", default=CLOUDFRONT)
    return parser


def asset_urls(cloudfront: str, stem: str) -> dict[str, str]:
    base = cloudfront.rstrip("/")
    return {
        "root": f"{base}/",
        "readme": f"{base}/readme",
        "broadcast": f"{base}/assets/{stem}-broadcast.mp4",
        "en": f"{base}/assets/{stem}-en.mp4",
        "zh": f"{base}/assets/{stem}-zh.mp4",
    }


def period_number(value) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value == int(value):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    upper = text.upper()
    if upper.startswith("OT"):
        rest = upper[2:]
        return 4 + int(rest or "1")
    for index, token in enumerate(("一", "二", "三", "四"), start=1):
        if token in text:
            return index
    if upper[:1].isdigit():
        return int(upper[:1])
    return None


def clock_seconds(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    iso = CLOCK_ISO.fullmatch(text)
    if iso:
        return int(iso.group(1) or 0) * 60 + float(iso.group(2) or 0)
    clock = CLOCK_TEXT.fullmatch(text)
    if clock:
        return int(clock.group(1)) * 60 + float(clock.group(2))
    return None


def norm_jersey(value) -> str:
    text = str(value or "").strip()
    if text.isdigit():
        return str(int(text))
    return text


def as_int(value) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def check(name: str, ok: bool, detail: str, suggestion: str = "", short: str = "", headline: bool = True) -> dict:
    return {
        "name": name,
        "ok": bool(ok),
        "detail": detail,
        "suggestion": suggestion,
        "short": short or detail,
        "headline": headline,
    }


def conclusion(checks: list[dict]) -> str:
    bad = [item for item in checks if not item["ok"] and item.get("headline", True)]
    if not bad:
        return "可以提交。"
    reasons = "；".join(item["short"] for item in bad)
    tips = []
    for item in bad:
        tip = item.get("suggestion") or ""
        if tip and tip not in tips:
            tips.append(tip)
    text = f"有问题：{reasons}。"
    if tips:
        text += "建议：" + "；".join(tips) + "。"
    return text


def parse_clips_js(text: str) -> dict:
    marker = "window.DEEPCOURT_CLIPS="
    start = text.find(marker)
    if start < 0:
        raise ValueError("回放数据不是预期格式")
    raw = text[start + len(marker):].strip()
    if raw.endswith(";"):
        raw = raw[:-1]
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("回放数据不是对象")
    return data


def pick_pack(data: dict, job_id: str, clip: str) -> dict | None:
    if clip and clip in data:
        pack = data[clip]
        return pack if isinstance(pack, dict) else None
    pack = data.get(job_id)
    return pack if isinstance(pack, dict) else None


def stream_events(stream: dict | None) -> list[dict]:
    if not stream:
        return []
    events = []
    for poss in stream.get("possessions") or []:
        events.extend(poss.get("events") or [])
    return events


def unmarshal(value):
    if not isinstance(value, dict):
        return value
    if "S" in value:
        return value["S"]
    if "N" in value:
        raw = value["N"]
        if re.fullmatch(r"-?\d+", raw):
            return int(raw)
        return float(raw)
    if "BOOL" in value:
        return bool(value["BOOL"])
    if "NULL" in value:
        return None
    if "M" in value:
        return {key: unmarshal(item) for key, item in value["M"].items()}
    if "L" in value:
        return [unmarshal(item) for item in value["L"]]
    return value


def http_fault(row: dict, kind: str) -> str:
    status = int(row.get("status") or 0)
    error = row.get("error") or ""
    if status == 0:
        return f"请求失败：{error or '没有响应'}"
    if status != 200:
        return f"HTTP {status}"
    size = row.get("size")
    if kind == "page":
        if size is None:
            return "没有读到页面大小"
        if size < PAGE_MIN:
            return f"页面只有 {size} 字节"
        if size > PAGE_MAX:
            return f"页面有 {size} 字节，大得不正常"
        return ""
    if size is None:
        return ""
    if size < MP4_MIN:
        return f"文件只有 {size} 字节"
    if size > MP4_MAX:
        return f"文件有 {size} 字节，大得不正常"
    return ""


def assess_http(name: str, row: dict, kind: str, suggestion: str, short: str) -> dict:
    fault = http_fault(row, kind)
    url = row.get("url") or ""
    size = row.get("size")
    if fault:
        detail = f"{url} {fault}".strip()
        return check(name, False, detail, suggestion, short)
    if size is None:
        detail = f"{url} HTTP 200，响应没有 Content-Length"
    else:
        detail = f"{url} HTTP 200，{size} 字节"
    return check(name, True, detail)


def assess_probe(name: str, probe: dict, http_row: dict) -> dict:
    if int(http_row.get("status") or 0) != 200:
        return check(name, False, "地址不是 HTTP 200，没有跑 ffprobe", "", "ffprobe 跳过", headline=False)
    if probe.get("error"):
        return check(
            name,
            False,
            f"ffprobe 失败：{probe['error']}",
            "本机需要 ffprobe（随 ffmpeg 安装）后再确认时长、分辨率和音轨。",
            "ffprobe 失败",
        )
    streams = probe.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audios = [item for item in streams if item.get("codec_type") == "audio"]
    if not video:
        return check(name, False, "ffprobe 没有看到视频轨", "文件可能不是成片。", "没有视频轨")
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    if (width, height) not in RESOLUTIONS:
        return check(
            name,
            False,
            f"分辨率 {width}x{height}",
            "成片应是 1280x720。确认传的是 broadcast，不是源片。",
            f"分辨率 {width}x{height}",
        )
    try:
        duration = float((probe.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if not (DURATION_MIN <= duration <= DURATION_MAX):
        return check(
            name,
            False,
            f"时长 {duration:.1f} 秒",
            "考试片大约 24 秒。过短或过长先不要交。",
            f"时长 {duration:.1f} 秒",
        )
    if http_row.get("size") is None:
        raw_size = (probe.get("format") or {}).get("size")
        size = as_int(raw_size)
        if size is not None and (size < MP4_MIN or size > MP4_MAX):
            return check(name, False, f"文件大小 {size} 字节不正常", "重新出片再传。", "文件大小不正常")
    if len(audios) < 1:
        return check(
            name,
            False,
            "没有音轨",
            "成片里要有一条音轨。英文原声没有映射进去就不要交无声片。",
            "没有音轨",
        )
    return check(name, True, f"{width}x{height}，时长 {duration:.1f} 秒，音轨 {len(audios)} 条")


def assess_structured(stream: dict | None, error: str, job_id: str) -> dict:
    if stream is None:
        return check(
            "structured",
            False,
            error or f"媒体桶没有 structured/{job_id}.json",
            "事件清单没写上。可以先交只烧记分牌的成片。",
            "没有事件清单",
        )
    if stream.get("jobId") and str(stream.get("jobId")) != job_id:
        return check(
            "structured",
            False,
            f"清单 jobId 是 {stream.get('jobId')}",
            "清单和作业对不上，不要交。",
            "清单 jobId 不对",
        )
    if not isinstance(stream.get("possessions"), list):
        return check(
            "structured",
            False,
            "清单缺少 possessions",
            "这不是 chain.py 写的 structured JSON。",
            "清单结构不对",
        )
    events = stream_events(stream)
    shots = [item for item in events if item.get("type") == "shot"]
    game_id = stream.get("gameId") or ""
    detail = f"structured/{job_id}.json 在，{len(events)} 条事件，出手 {len(shots)} 条"
    if game_id:
        detail += f"，比赛 {game_id}"
    if not events:
        return check(
            "structured",
            False,
            detail + "。事件是空的",
            "空清单不要当完成。看认人日志，或先交只烧记分牌的成片。",
            "清单没有事件",
        )
    return check("structured", True, detail + "。")


def assess_dynamodb(job: dict | None, error: str, stream: dict | None) -> dict:
    if job is None:
        return check(
            "dynamodb",
            False,
            error or "没有读到 Jobs 表",
            "确认栈 Team04DeepCourt 的 JobsTable，以及 profile nba。",
            "Jobs 表没读到",
        )
    status = str(job.get("status") or "")
    count = as_int(job.get("eventCount"))
    stream_count = len(stream_events(stream)) if stream else None
    if status != "DONE":
        shown = status or "空"
        return check(
            "dynamodb",
            False,
            f"Jobs 表状态是 {shown}",
            "状态不是 DONE 就不要交。看 Batch 和 analyze_job 日志。",
            f"任务状态 {shown}",
        )
    if count is None:
        return check(
            "dynamodb",
            False,
            "状态是 DONE，但没有事件数",
            "analyze_job 应写入 eventCount。等校验写完再查。",
            "没有事件数",
        )
    if stream_count is not None and count != stream_count:
        return check(
            "dynamodb",
            False,
            f"Jobs 表事件数 {count}，清单里 {stream_count} 条",
            "两边不一致。等校验 Lambda 写完再查，或重传 structured。",
            "事件数对不上",
        )
    return check("dynamodb", True, f"Jobs 表状态 DONE，事件数 {count}。")


def court_jerseys(game: dict, period: int, clock_s: float) -> set[tuple[str, str]]:
    found = set()
    players = game.get("players") or {}
    for team, ids in on_court(game, period, clock_s).items():
        for pid in ids:
            player = players.get(pid) or {}
            jersey = norm_jersey(player.get("jersey"))
            side = str(player.get("team") or team or "").upper()
            if jersey:
                found.add((side, jersey))
    return found


def format_court(pairs: set[tuple[str, str]]) -> str:
    grouped: dict[str, list[str]] = {}
    for team, jersey in sorted(pairs):
        grouped.setdefault(team, []).append(jersey)
    if not grouped:
        return "（没有推出场上人员）"
    return "，".join(f"{team} {'、'.join(nums)}" for team, nums in sorted(grouped.items()))


def named_votes(identity: dict) -> list[dict]:
    rows = []
    for vote in identity.get("votes") or []:
        if not str(vote.get("nameZh") or "").strip():
            continue
        rows.append(vote)
    return rows


def duplicate_names(votes: list[dict]) -> list[str]:
    grouped: dict[str, list] = {}
    for index, vote in enumerate(votes):
        name = str(vote.get("nameZh") or "").strip()
        if not name:
            continue
        track = vote.get("id")
        grouped.setdefault(name, [])
        token = track if track is not None else f"missing-{index}"
        if token not in grouped[name]:
            grouped[name].append(token)
    issues = []
    for name, ids in grouped.items():
        if len(ids) > 1:
            issues.append(f"{name}同时落在轨迹 {_join_ids(ids)}")
    return issues


def _join_ids(ids: list) -> str:
    text = [str(item) for item in ids]
    if len(text) == 2:
        return f"{text[0]} 和 {text[1]}"
    return "、".join(text)


def scorebug_at(scorebug: list, t: float) -> dict | None:
    found = None
    found_t = None
    for row in scorebug:
        try:
            stamp = float(row.get("t") or 0)
        except (TypeError, ValueError):
            continue
        if stamp <= t + 0.05 and (found_t is None or stamp >= found_t):
            found = row
            found_t = stamp
    return found


def times_for(vote: dict, matches: list) -> list[float]:
    name = str(vote.get("nameZh") or "")
    number = norm_jersey(vote.get("number"))
    team = str(vote.get("team") or "").upper()
    times = []
    for match in matches:
        same_name = str(match.get("name") or "") == name
        same_jersey = number and norm_jersey(match.get("number")) == number and str(match.get("team") or "").upper() == team
        if same_name or same_jersey:
            try:
                times.append(float(match.get("t") or 0))
            except (TypeError, ValueError):
                continue
    return times


def assess_names(identity: dict | None, error: str, scorebug: list, game: dict | None, game_error: str) -> list[dict]:
    if identity is None:
        return [
            check(
                "names-on-court",
                False,
                error or "没有 identity/{jobId}.json，没法核对画面上的名字",
                "认人结果没写上。可以先交只烧记分牌的成片，再补人名。",
                "没有认人结果",
            ),
            check("names-duplicate", True, "没有认人结果，不检查同名轨迹。"),
        ]
    votes = named_votes(identity)
    duplicates = duplicate_names(votes)
    if duplicates:
        duplicate_check = check(
            "names-duplicate",
            False,
            "；".join(duplicates),
            "同一名字不要留在两条轨迹上。票数少的那条不要写名字，重出成片后再查。",
            "同一名字落在两条轨迹",
        )
    else:
        duplicate_check = check("names-duplicate", True, "没有同一个名字落在两条轨迹上。")
    summary = _identity_summary(identity)
    if not votes:
        return [
            check(
                "names-on-court",
                False,
                (summary + "。" if summary else "") + "认人结果里没有写出中文名",
                "可以先交只烧记分牌的成片，再补人名。",
                "画面上没有人名",
            ),
            duplicate_check,
        ]
    if game is None:
        return [
            check(
                "names-on-court",
                False,
                game_error or "没有官方逐回合，没法核对场上人员",
                "可以先交只烧记分牌的成片。补上 S3 official/ 后再核对人名。",
                "没法核对场上人员",
            ),
            duplicate_check,
        ]
    issues = []
    voted = {str(vote.get("nameZh")) for vote in votes}
    for name in identity.get("firstSecond") or []:
        if name not in voted:
            issues.append(f"第 1 秒有 {name}，投票结果里没有")
    matches = identity.get("matches") or []
    for vote in votes:
        name = str(vote.get("nameZh"))
        team = str(vote.get("team") or "").upper()
        number = norm_jersey(vote.get("number"))
        times = times_for(vote, matches) or [0.0]
        if not number:
            issues.append(f"{name} 没有号码，没法对到场上人员")
            continue
        reported = False
        for stamp in times:
            if reported:
                break
            row = scorebug_at(scorebug, stamp)
            if row is None:
                issues.append(f"{name} 在视频 {stamp:.1f} 秒没有对上记分牌")
                reported = True
                continue
            period = period_number(row.get("period"))
            clock = clock_seconds(row.get("clock"))
            if period is None or clock is None:
                issues.append(f"{name} 对上的记分牌节次或比赛钟认不出")
                reported = True
                continue
            court = court_jerseys(game, period, clock)
            if (team, number) in court:
                continue
            if not team and sum(1 for side, jersey in court if jersey == number) == 1:
                continue
            issues.append(
                f"{name}（{team or '?'} {number}）在视频 {stamp:.1f} 秒、比赛钟 {row.get('clock')} 不在场上。"
                f"当时场上 {format_court(court)}"
            )
            reported = True
    if issues:
        shown = "；".join(issues[:8])
        if len(issues) > 8:
            shown += f"；等 {len(issues)} 处"
        prefix = f"{summary}。" if summary else ""
        return [
            check(
                "names-on-court",
                False,
                prefix + shown,
                "写出的名字必须是当时场上的人。先交只烧记分牌的成片，认人改完再补。",
                "有名字不在当时场上",
            ),
            duplicate_check,
        ]
    detail = "写出的名字都在当时场上。"
    if summary:
        detail = summary + "。" + detail
    return [check("names-on-court", True, detail), duplicate_check]


def _identity_summary(identity: dict) -> str:
    bits = []
    if identity.get("bedrockCalls") is not None:
        bits.append(f"Bedrock {identity['bedrockCalls']} 次")
    if identity.get("identitySeconds") is not None:
        bits.append(f"认人 {identity['identitySeconds']} 秒")
    first = identity.get("firstSecond") or []
    if first:
        bits.append(f"第 1 秒 {len(first)} 个名字")
    model = identity.get("model")
    if model:
        bits.append(str(model))
    return "，".join(bits)


def iter_official_plays(game: dict) -> list[dict]:
    rows = []
    for play in game.get("plays") or []:
        period = period_number(play.get("period"))
        clock = play.get("clock_s")
        clock_s = float(clock) if isinstance(clock, (int, float)) else clock_seconds(play.get("clock"))
        away = play.get("score_away")
        home = play.get("score_home")
        if (away is None or home is None) and play.get("scores"):
            pair = play.get("scores") or ()
            if len(pair) >= 2:
                away, home = pair[0], pair[1]
        scores = None
        if away not in (None, "") and home not in (None, ""):
            left = as_int(away)
            right = as_int(home)
            if left is not None and right is not None:
                scores = (left, right)
        rows.append({"period": period, "clock": clock_s, "scores": scores})
    return rows


def official_score_at(plays: list[dict], period: int, clock_s: float) -> tuple[tuple[int, int], float] | None:
    candidates = [
        play for play in plays
        if play["period"] == period and play["clock"] is not None and play["clock"] + 1e-6 >= clock_s and play["scores"]
    ]
    if not candidates:
        return None
    play = min(candidates, key=lambda item: item["clock"])
    return play["scores"], play["clock"]


def row_score(row: dict) -> tuple[int, int] | None:
    if "awayScore" in row and "homeScore" in row:
        left, right = as_int(row.get("awayScore")), as_int(row.get("homeScore"))
        if left is None or right is None:
            return None
        return left, right
    scores = row.get("scores") or []
    if len(scores) >= 2:
        left, right = as_int(scores[0]), as_int(scores[1])
        if left is None or right is None:
            return None
        return left, right
    return None


def assess_scoreboard(scorebug: list, clips_error: str, game: dict | None, game_error: str, game_id: str) -> dict:
    if not scorebug:
        return check(
            "scoreboard",
            False,
            clips_error or "回放数据里没有记分牌",
            "记分牌没进成片或没写进 replay/clips.js。可以重烧记分牌后再传。",
            "没有记分牌",
        )
    if game is None:
        return check(
            "scoreboard",
            False,
            game_error or "没有官方逐回合，没法对记分牌",
            "官方逐回合没对上。记分牌成片仍可先交，补上 S3 official/ 后再核对。",
            "没有官方逐回合",
        )
    plays = iter_official_plays(game)
    issues = []
    ordered = sorted(scorebug, key=lambda row: float(row.get("t") or 0))
    previous = None
    for row in ordered:
        period = period_number(row.get("period"))
        clock = clock_seconds(row.get("clock"))
        stamp = float(row.get("t") or 0)
        label = f"{row.get('period') or '?'} {row.get('clock') or '?'}"
        if period is None:
            issues.append(f"{label} 的节次认不出")
            continue
        if clock is None:
            issues.append(f"{label} 的比赛钟认不出")
            continue
        if previous and previous[0] == period:
            elapsed = stamp - previous[2]
            if clock > previous[1] + 1.5:
                issues.append(f"比赛钟从 {previous[3]} 回到 {row.get('clock')}，和视频时间对不上")
            elif previous[1] - clock > max(3.0, elapsed + 3):
                issues.append(f"比赛钟从 {previous[3]} 掉到 {row.get('clock')}，快过视频时间")
        previous = (period, clock, stamp, row.get("clock"))
        score = row_score(row)
        if score is None:
            issues.append(f"{label} 没有比分")
            continue
        found = official_score_at(plays, period, clock)
        if found is None:
            issues.append(f"{label} 对不上官方记录（这个钟点之前没有带比分的逐回合）")
            continue
        official, _play_clock = found
        if score == official:
            continue
        if score == (official[1], official[0]):
            issues.append(f"{label} 客主队比分反了：画面 {score[0]}-{score[1]}，官方 {official[0]}-{official[1]}")
            continue
        issues.append(f"{label} 记分牌 {score[0]}-{score[1]}，官方 {official[0]}-{official[1]}")
    if issues:
        shown = "；".join(issues[:8])
        if len(issues) > 8:
            shown += f"；等 {len(issues)} 处"
        where = f"（比赛 {game_id}）" if game_id else ""
        return check(
            "scoreboard",
            False,
            shown + where,
            "记分牌和官方逐回合不一致。不要交错比分。核对比赛钟和客主队方向，必要时重跑。",
            "记分牌和官方对不上",
        )
    where = f"比赛 {game_id}，" if game_id else ""
    return check("scoreboard", True, f"{where}{len(scorebug)} 条记分牌读数的节次、比赛钟和比分都对得上官方逐回合。")


def build_checks(job_id: str, fetched: Fetched) -> list[dict]:
    stem = fetched.stem or job_id
    http = fetched.http
    root = http.get("root") or {"status": 0, "error": "没有请求根路径"}
    readme = http.get("readme") or {"status": 0, "error": "没有请求 /readme"}
    broadcast = http.get("broadcast") or {"status": 0, "error": "没有请求成片"}
    english = http.get("en") or {"status": 0, "error": "没有请求英文音轨"}
    chinese = http.get("zh") or {"status": 0, "error": "没有请求中文音轨"}
    checks = [
        assess_http(
            "cloudfront-root",
            root,
            "page",
            "根路径打不开。先确认 CloudFront 还在，不要改 game.html。",
            "根路径打不开",
        ),
        assess_http(
            "cloudfront-readme",
            readme,
            "page",
            "/readme 打不开。检查网页桶 readme.html 和 CloudFront 函数 ReadmePath。",
            "/readme 打不开",
        ),
        assess_http(
            "film-broadcast",
            broadcast,
            "mp4",
            f"成片不在网页桶 assets/{stem}-broadcast.mp4。先看 Batch 是否 SUCCEEDED；没有就走本机应急 scripts/fallback/local_exam.sh。",
            "成片取不到",
        ),
        assess_http(
            "film-en",
            english,
            "mp4",
            f"英文音轨没有。页面会回退到成片原声。可以先交成片，再补 assets/{stem}-en.mp4。",
            "英文音轨取不到",
        ),
        assess_http(
            "film-zh",
            chinese,
            "mp4",
            f"中文音轨没有。可以先交成片原声，再跑 python3 -m pipeline.present.tracks 补 assets/{stem}-zh.mp4。",
            "中文音轨取不到",
        ),
        assess_probe("probe-broadcast", fetched.probes.get("broadcast") or {"error": "没有 ffprobe 结果"}, broadcast),
        assess_probe("probe-en", fetched.probes.get("en") or {"error": "没有 ffprobe 结果"}, english),
        assess_probe("probe-zh", fetched.probes.get("zh") or {"error": "没有 ffprobe 结果"}, chinese),
        assess_structured(fetched.stream, fetched.stream_error, job_id),
        assess_dynamodb(fetched.job, fetched.job_error, fetched.stream),
    ]
    checks.extend(assess_names(fetched.identity, fetched.identity_error, fetched.scorebug, fetched.game, fetched.game_error))
    checks.append(assess_scoreboard(fetched.scorebug, fetched.clips_error, fetched.game, fetched.game_error, fetched.game_id))
    order = {name: index for index, name in enumerate(CHECK_ORDER)}
    checks.sort(key=lambda item: order.get(item["name"], 100))
    return checks


def render_markdown(job_id: str, checks: list[dict]) -> str:
    lines = [conclusion(checks), ""]
    by_name = {item["name"]: item for item in checks}
    for title, names in GROUPS:
        lines.append(f"## {title}")
        lines.append("")
        for name in names:
            item = by_name.get(name)
            if not item:
                continue
            mark = "通过" if item["ok"] else "未通过"
            lines.append(f"- {mark}：{item['detail']}")
        lines.append("")
    lines.append(f"作业 `{job_id}`。")
    lines.append("")
    return "\n".join(lines)


def render_json(job_id: str, stem: str, checks: list[dict]) -> dict:
    return {
        "jobId": job_id,
        "stem": stem,
        "ok": all(item["ok"] for item in checks),
        "conclusion": conclusion(checks),
        "checks": checks,
        "published": False,
    }


def render_html(job_id: str, markdown: str) -> str:
    escaped = (
        markdown.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-CN">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        f"<title>成片自检 {job_id}</title>\n"
        "</head>\n"
        "<body>\n"
        '<pre style="white-space:pre-wrap;font:16px/1.5 sans-serif;max-width:52rem;margin:2rem auto">\n'
        f"{escaped}\n"
        "</pre>\n"
        "</body>\n"
        "</html>\n"
    )


def write_reports(out_dir: Path, job_id: str, markdown: str, payload: dict) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{job_id}.md"
    json_path = out_dir / f"{job_id}.json"
    md_path.write_text(markdown, encoding="utf-8")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return md_path, json_path


def _aws(args: list[str], profile: str, region: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["aws", *args, "--profile", profile, "--region", region],
        capture_output=True,
        timeout=timeout,
    )


def _aws_text(args: list[str], profile: str, region: str) -> str:
    proc = _aws([*args, "--output", "text"], profile, region)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout).decode("utf-8", "replace").strip()
        raise RuntimeError(err[:400] or "aws 失败")
    return proc.stdout.decode("utf-8", "replace").strip()


def _aws_json(args: list[str], profile: str, region: str) -> dict:
    proc = _aws([*args, "--output", "json"], profile, region)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout).decode("utf-8", "replace").strip()
        raise RuntimeError(err[:400] or "aws 失败")
    text = proc.stdout.decode("utf-8", "replace").strip()
    if not text:
        return {}
    data = json.loads(text)
    return data if isinstance(data, dict) else {"value": data}


def stack_output(key: str, profile: str, region: str) -> str:
    value = _aws_text([
        "cloudformation", "describe-stacks",
        "--stack-name", STACK,
        "--query", f"Stacks[0].Outputs[?OutputKey=='{key}'].OutputValue",
    ], profile, region)
    if not value or value == "None":
        raise RuntimeError(f"栈 {STACK} 没有输出 {key}")
    return value


def s3_bytes(bucket: str, key: str, profile: str, region: str) -> bytes:
    proc = _aws(["s3", "cp", f"s3://{bucket}/{key}", "-"], profile, region, timeout=120)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise FileNotFoundError(err[:400] or f"没有 s3://{bucket}/{key}")
    return proc.stdout


def list_keys(bucket: str, prefix: str, profile: str, region: str) -> list[str]:
    keys = []
    token = ""
    while True:
        args = ["s3api", "list-objects-v2", "--bucket", bucket, "--prefix", prefix, "--max-keys", "1000"]
        if token:
            args.extend(["--continuation-token", token])
        page = _aws_json(args, profile, region)
        for item in page.get("Contents") or []:
            key = item.get("Key")
            if key:
                keys.append(key)
        if not page.get("IsTruncated"):
            return keys
        token = page.get("NextContinuationToken") or ""
        if not token:
            return keys


def fetch_http(url: str, kind: str) -> dict:
    try:
        if kind == "page":
            return _request(url, "GET", read_body=True)
        row = _request(url, "HEAD", read_body=False)
        if row["status"] in {403, 405}:
            row = _request(url, "GET", read_body=False)
        return row
    except Exception as exc:  # noqa: BLE001 — 单项失败记入报告
        return {"status": 0, "size": 0, "url": url, "error": str(exc)[:300]}


def _request(url: str, method: str, read_body: bool) -> dict:
    req = urllib.request.Request(url, method=method, headers={"User-Agent": "deepcourt-qa/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            if read_body:
                body = resp.read()
                size = len(body)
            else:
                raw = resp.headers.get("Content-Length")
                size = int(raw) if raw and str(raw).isdigit() else None
            return {"status": resp.status, "size": size, "url": url, "error": ""}
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, "size": 0, "url": url, "error": ""}


def run_ffprobe(url: str) -> dict:
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", url],
            capture_output=True,
            text=True,
            timeout=90,
        )
    except FileNotFoundError:
        return {"error": "本机没有 ffprobe"}
    except subprocess.TimeoutExpired:
        return {"error": "ffprobe 超时"}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)[:300]}
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "ffprobe 失败").strip()
        return {"error": err[:300]}
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return {"error": "ffprobe 没有返回 JSON"}
    return data if isinstance(data, dict) else {"error": "ffprobe 结果不是对象"}


def _score_hits(game: dict, scorebug: list) -> int:
    plays = iter_official_plays(game)
    hits = 0
    for row in scorebug:
        period = period_number(row.get("period"))
        clock = clock_seconds(row.get("clock"))
        score = row_score(row)
        if period is None or clock is None or score is None:
            continue
        found = official_score_at(plays, period, clock)
        if found and found[0] == score:
            hits += 1
    return hits


def _load_official(dest: Path, game_id: str, scorebug: list) -> tuple[dict | None, str, str]:
    ids = []
    if game_id:
        ids.append(game_id)
    else:
        for path in sorted(dest.glob("*playbyplay*.json")):
            token = path.name.split("_", 1)[0]
            if token and token not in ids:
                ids.append(token)
    best_id, best_game, best_n = "", None, -1
    for token in ids:
        game = load_game(token, dest)
        if not game.get("plays"):
            continue
        hits = _score_hits(game, scorebug) if not game_id else len(game["plays"])
        if game_id and token == game_id:
            return game, token, ""
        if hits > best_n:
            best_id, best_game, best_n = token, game, hits
    if game_id:
        return None, game_id, f"没有读到 {game_id} 的逐回合"
    if best_game is not None and best_n >= 2:
        return best_game, best_id, ""
    return None, "", "记分牌对不上 S3 official/ 里的任何一场"


def fetch_all(args: argparse.Namespace) -> Fetched:
    stem = args.clip or args.job_id
    urls = asset_urls(args.cloudfront, stem)
    http = {
        "root": fetch_http(urls["root"], "page"),
        "readme": fetch_http(urls["readme"], "page"),
        "broadcast": fetch_http(urls["broadcast"], "mp4"),
        "en": fetch_http(urls["en"], "mp4"),
        "zh": fetch_http(urls["zh"], "mp4"),
    }
    probes = {}
    for key in ("broadcast", "en", "zh"):
        if int(http[key].get("status") or 0) == 200:
            probes[key] = run_ffprobe(urls[key])
        else:
            probes[key] = {"error": "地址不是 HTTP 200"}
    fetched = Fetched(http=http, probes=probes, stem=stem)
    try:
        raw = s3_bytes(args.media_bucket, f"structured/{args.job_id}.json", args.profile, args.region)
        fetched.stream = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        fetched.stream_error = str(exc)[:400]
    try:
        raw = s3_bytes(args.media_bucket, f"identity/{args.job_id}.json", args.profile, args.region)
        fetched.identity = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        fetched.identity_error = str(exc)[:400]
    try:
        table = _aws_text([
            "cloudformation", "describe-stack-resource",
            "--stack-name", STACK,
            "--logical-resource-id", "JobsTable",
            "--query", "StackResourceDetail.PhysicalResourceId",
        ], args.profile, args.region)
        item = _aws_json([
            "dynamodb", "get-item",
            "--table-name", table,
            "--key", json.dumps({"jobId": {"S": args.job_id}}),
        ], args.profile, args.region)
        fetched.job = unmarshal(item.get("Item")) if item.get("Item") else None
        if fetched.job is None:
            fetched.job_error = f"Jobs 表没有 {args.job_id}"
    except Exception as exc:  # noqa: BLE001
        fetched.job_error = str(exc)[:400]
    try:
        web = args.web_bucket or stack_output("WebBucketName", args.profile, args.region)
        text = s3_bytes(web, "replay/clips.js", args.profile, args.region).decode("utf-8")
        pack = pick_pack(parse_clips_js(text), args.job_id, args.clip)
        if pack is None:
            fetched.clips_error = "回放数据里没有这个作业"
        else:
            fetched.scorebug = pack.get("scorebug") or []
            if not fetched.scorebug:
                fetched.clips_error = "回放数据里没有记分牌"
    except Exception as exc:  # noqa: BLE001
        fetched.clips_error = str(exc)[:400]
    game_id = str((fetched.stream or {}).get("gameId") or "")
    try:
        with tempfile.TemporaryDirectory(prefix="deepcourt-qa-") as tmp:
            dest = Path(tmp)
            keys = list_keys(args.media_bucket, "official/", args.profile, args.region)
            for key in keys:
                name = Path(key).name
                if not name.endswith(".json"):
                    continue
                if game_id and game_id not in name:
                    continue
                if not game_id and "playbyplay" not in name and "boxscore" not in name:
                    continue
                (dest / name).write_bytes(s3_bytes(args.media_bucket, key, args.profile, args.region))
            game, found_id, error = _load_official(dest, game_id, fetched.scorebug)
        fetched.game = game
        fetched.game_id = found_id or game_id
        fetched.game_error = error
    except Exception as exc:  # noqa: BLE001
        fetched.game_error = str(exc)[:400]
        fetched.game_id = game_id
    return fetched


def publish_report(html: str, job_id: str, web_bucket: str, distribution_id: str, profile: str, region: str, cloudfront: str) -> dict:
    try:
        with tempfile.TemporaryDirectory(prefix="deepcourt-qa-html-") as tmp:
            path = Path(tmp) / f"{job_id}.html"
            path.write_text(html, encoding="utf-8")
            proc = _aws([
                "s3", "cp", str(path), f"s3://{web_bucket}/qa/{job_id}.html",
                "--content-type", "text/html; charset=utf-8",
                "--cache-control", "max-age=60",
            ], profile, region, timeout=120)
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", "replace").strip()
            return {"ok": False, "detail": err[:400] or "上传报告失败"}
        if distribution_id:
            inv = _aws([
                "cloudfront", "create-invalidation",
                "--distribution-id", distribution_id,
                "--paths", f"/qa/{job_id}.html",
            ], profile, region)
            if inv.returncode != 0:
                err = inv.stderr.decode("utf-8", "replace").strip()[:200]
                return {"ok": True, "detail": f"已上传，失效没做成（{err}）。地址 {cloudfront.rstrip('/')}/qa/{job_id}.html"}
        return {"ok": True, "detail": f"已发布 {cloudfront.rstrip('/')}/qa/{job_id}.html"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": str(exc)[:400]}


def _default_publisher(html: str, args: argparse.Namespace) -> dict:
    web = args.web_bucket or stack_output("WebBucketName", args.profile, args.region)
    dist = ""
    try:
        dist = stack_output("DistributionId", args.profile, args.region)
    except Exception as exc:  # noqa: BLE001
        uploaded = publish_report(html, args.job_id, web, "", args.profile, args.region, args.cloudfront)
        if uploaded["ok"]:
            uploaded["detail"] += f"。没有分布编号，未失效（{exc}）"
        return uploaded
    return publish_report(html, args.job_id, web, dist, args.profile, args.region, args.cloudfront)


def main(argv: list[str] | None = None, *, fetcher=None, publisher=None) -> int:
    args = build_parser().parse_args(argv)
    if not TOKEN.fullmatch(args.job_id) or (args.clip and not TOKEN.fullmatch(args.clip)):
        print("jobId 和片名只能用字母、数字、下划线和中划线", file=sys.stderr)
        return 2
    fetched = (fetcher or fetch_all)(args)
    if not fetched.stem:
        fetched.stem = args.clip or args.job_id
    checks = build_checks(args.job_id, fetched)
    markdown = render_markdown(args.job_id, checks)
    payload = render_json(args.job_id, fetched.stem, checks)
    html = render_html(args.job_id, markdown)
    if args.publish:
        result = (publisher or _default_publisher)(html, args)
        payload["published"] = bool(result.get("ok"))
        payload["publishDetail"] = result.get("detail") or ""
    out_dir = Path(args.out) if args.out else ROOT / "scripts" / "qa" / "out"
    md_path, json_path = write_reports(out_dir, args.job_id, markdown, payload)
    print(payload["conclusion"])
    print(f"Markdown：{md_path}")
    print(f"JSON：{json_path}")
    if args.publish:
        print(payload.get("publishDetail") or "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
