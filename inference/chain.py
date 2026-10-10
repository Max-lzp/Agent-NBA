"""云上认人、事件清单和官方数据。

认人用 Textract 读球衣号码，再对上桶里的名单。
事件清单按球和记分牌跳动写成 structured JSON，交给已有的校验 Lambda。
官方数据从媒体桶的 official/ 对上回合和预期命中率。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import boto3
import cv2

REGION = os.environ.get("AWS_REGION", "us-east-1")
ROOT = Path("/tmp/official")
MAX_CROPS = 12
SCORE_LAG_S = 8.0
NAMES = {
    ("HOU", "0"): "霍利戴", ("HOU", "1"): "阿门", ("HOU", "2"): "芬尼史密斯",
    ("HOU", "7"): "杜兰特", ("HOU", "8"): "泰特", ("HOU", "10"): "史密斯",
    ("HOU", "15"): "谢泼德", ("HOU", "17"): "伊森", ("HOU", "20"): "奥科吉",
    ("HOU", "28"): "申京", ("HOU", "30"): "卡佩拉",
    ("DAL", "00"): "克里斯蒂", ("DAL", "0"): "克里斯蒂", ("DAL", "1"): "哈迪",
    ("DAL", "3"): "戴维斯", ("DAL", "5"): "拉塞尔", ("DAL", "7"): "鲍威尔",
    ("DAL", "9"): "南姆哈德", ("DAL", "10"): "威廉姆斯", ("DAL", "13"): "马歇尔",
    ("DAL", "16"): "马丁", ("DAL", "21"): "加福德", ("DAL", "25"): "华盛顿",
    ("DAL", "30"): "西塞", ("DAL", "31"): "克莱", ("DAL", "32"): "弗拉格",
}
TEAM_ZH = {"DAL": "独行侠", "HOU": "火箭"}


def link(job_id: str, video: Path, frames: list, board: dict, bucket: str) -> dict:
    s3 = boto3.client("s3", region_name=REGION)
    textract = boto3.client("textract", region_name=REGION)
    _download_official(s3, bucket)
    roster = _roster()
    reads = _read_jerseys(textract, video, frames)
    matches = [item for item in (_match(read, roster) for read in reads) if item]
    identity = {
        "jobId": job_id,
        "service": "textract",
        "reads": reads,
        "matches": matches,
    }
    s3.put_object(
        Bucket=bucket,
        Key=f"identity/{job_id}.json",
        Body=json.dumps(identity, ensure_ascii=False).encode(),
        ContentType="application/json",
    )
    game_id, plays, quality = _official_game(board.get("reads") or [])
    stream = _stream(job_id, game_id, frames, board, matches, plays, quality)
    s3.put_object(
        Bucket=bucket,
        Key=f"structured/{job_id}.json",
        Body=json.dumps(stream, ensure_ascii=False).encode(),
        ContentType="application/json",
    )
    return {
        "events": _page_events(stream),
        "gameId": game_id,
        "identityCount": len(identity["matches"]),
        "textractCalls": len(reads),
    }


def _download_official(s3, bucket: str) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": "official/"}
        if token:
            kwargs["ContinuationToken"] = token
        page = s3.list_objects_v2(**kwargs)
        for item in page.get("Contents") or []:
            key = item["Key"]
            if not key.endswith(".json"):
                continue
            dest = ROOT / key.removeprefix("official/")
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                s3.download_file(bucket, key, str(dest))
        if not page.get("IsTruncated"):
            return
        token = page.get("NextContinuationToken")


def _roster() -> dict:
    path = ROOT / "roster" / "2025-26.json"
    if not path.exists():
        return {}
    doc = json.loads(path.read_text())
    out = {}
    for team, body in (doc.get("teams") or {}).items():
        players = body.get("players") if isinstance(body, dict) else body
        for player in players or []:
            if isinstance(player, dict):
                out[(team.upper(), str(player.get("jersey") or ""))] = str(player.get("name") or "")
    return out


def _read_jerseys(textract, video: Path, frames: list) -> list[dict]:
    cap = cv2.VideoCapture(str(video))
    reads = []
    last_t = -1.0
    try:
        for frame in frames:
            if len(reads) >= MAX_CROPS:
                break
            players = frame.get("players") or []
            if not players or frame.get("t", 0) - last_t < 0.4:
                continue
            box = max(players, key=lambda item: item[2] * item[3])
            cap.set(cv2.CAP_PROP_POS_MSEC, float(frame["t"]) * 1000)
            ok, image = cap.read()
            if not ok:
                continue
            crop = _torso(image, box)
            if crop is None:
                continue
            encoded, buf = cv2.imencode(".jpg", crop)
            if not encoded:
                continue
            try:
                response = textract.detect_document_text(Document={"Bytes": buf.tobytes()})
            except Exception as exc:  # noqa: BLE001 — 一次读号失败不中断整段
                reads.append({"t": frame["t"], "error": type(exc).__name__})
                last_t = frame["t"]
                continue
            lines = [
                block.get("Text", "")
                for block in response.get("Blocks") or []
                if block.get("BlockType") == "LINE"
            ]
            number = _jersey_number(lines)
            reads.append({"t": round(float(frame["t"]), 3), "lines": lines[:6], "number": number})
            last_t = frame["t"]
    finally:
        cap.release()
    return reads


def _torso(image, box):
    height, width = image.shape[:2]
    x, y, w, h = [int(float(value)) for value in box[:4]]
    x1, y1 = max(0, x), max(0, y)
    x2, y2 = min(width, x + w), min(height, y + int(h * 0.45))
    if x2 - x1 < 12 or y2 - y1 < 12:
        return None
    return image[y1:y2, x1:x2]


def _jersey_number(lines: list[str]) -> str:
    for line in lines:
        token = re.sub(r"[^0-9]", "", line)
        if token in {"0", "00"} or (token.isdigit() and 1 <= int(token) <= 99 and len(token) <= 2):
            return token
    return ""


def _match(read: dict, roster: dict) -> dict | None:
    number = read.get("number") or ""
    if not number:
        return None
    hits = [
        {"team": team, "number": number, "name": name, "t": read.get("t")}
        for (team, num), name in roster.items()
        if num == number
    ]
    return hits[0] if len(hits) == 1 else None


def _official_game(reads: list) -> tuple[str, list, dict]:
    plays_by_game = {}
    for path in ROOT.rglob("*_playbyplay*.json"):
        game_id = path.name.split("_", 1)[0]
        try:
            doc = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        plays_by_game[game_id] = [_play(item) for item in (doc.get("game") or {}).get("actions") or []]
    best_id, best_n = "", 0
    for game_id, plays in plays_by_game.items():
        hits = sum(1 for read in reads if _score_seen(plays, read))
        if hits > best_n:
            best_id, best_n = game_id, hits
    quality = {}
    if best_id and best_n >= 2:
        for path in ROOT.rglob(f"{best_id}_shotquality*.json"):
            doc = json.loads(path.read_text())
            for shot in doc.get("shots") or []:
                key = (
                    shot.get("period"),
                    _clock_seconds(shot.get("gameClock")),
                    str(shot.get("teamAbbreviation") or "").upper(),
                )
                quality[key] = shot.get("shotQuality")
            break
        return best_id, plays_by_game.get(best_id) or [], quality
    return "", [], {}


def _play(raw: dict) -> dict:
    kind = str(raw.get("actionType") or "").lower()
    if kind in {"2pt", "3pt"}:
        kind = "shot"
    value = 3 if "3pt" in str(raw.get("actionType") or "").lower() or "3PT" in str(raw.get("description") or "") else 2
    return {
        "kind": kind,
        "period": raw.get("period"),
        "clock": _clock_seconds(raw.get("clock")),
        "team": str(raw.get("teamTricode") or "").upper(),
        "made": str(raw.get("shotResult") or "").lower() == "made",
        "value": value if kind == "shot" else 0,
        "description": raw.get("description") or "",
        "assist": _assist(raw.get("description") or ""),
        "scores": _pair(raw.get("scoreAway"), raw.get("scoreHome")),
        "player": raw.get("playerNameI") or raw.get("playerName") or "",
    }


def _assist(description: str) -> str:
    match = re.search(r"\(([A-Za-z .'-]+) \d+ AST\)", description)
    return match.group(1).strip() if match else ""


def _score_seen(plays: list, read: dict) -> bool:
    period = _period_num(read.get("period"))
    clock = _clock_seconds(read.get("clock"))
    scores = _pair(*(tuple(read.get("scores") or (None, None))[:2]))
    if period is None or clock is None or scores is None:
        return False
    return any(
        play["period"] == period and play["clock"] is not None
        and abs(play["clock"] - clock) <= SCORE_LAG_S and play["scores"] == scores
        for play in plays
    )


def _stream(job_id, game_id, frames, board, matches, plays, quality) -> dict:
    away = board.get("away") or os.environ.get("AWAY", "")
    home = board.get("home") or os.environ.get("HOME_TEAM", "")
    events = _holder_events(frames, matches, away, home)
    events.extend(_shot_events(board.get("ticks") or [], board.get("reads") or [], plays, quality))
    events.sort(key=lambda item: item["t"])
    return {
        "version": "0.1",
        "jobId": job_id,
        "gameId": game_id,
        "possessions": [{
            "possession_id": "p0",
            "offense_team": away if away in {"HOU", "DAL"} else "HOU",
            "events": events,
        }],
    }


def _holder_events(frames, matches, away, home) -> list[dict]:
    named = {round(float(item["t"]), 1): item for item in matches}
    events = []
    previous = None
    for frame in frames:
        ball = frame.get("ball")
        players = frame.get("players") or []
        if not ball or not players:
            continue
        holder = min(players, key=lambda box: _foot_distance(box, ball))
        who = named.get(round(float(frame["t"]), 1))
        actor = _actor(who, away)
        if previous and previous.get("name") == actor.get("name"):
            continue
        if previous is None:
            event = {
                "t": round(float(frame["t"]), 3),
                "type": "receive",
                "actor": actor,
                "source": "textract" if who else "cv",
                "confidence": 0.7 if who else 0.4,
            }
        else:
            event = {
                "t": round(float(frame["t"]), 3),
                "type": "pass",
                "actor": previous,
                "target": actor,
                "source": "textract" if who else "cv",
                "confidence": 0.7 if who else 0.4,
            }
        events.append(event)
        previous = actor
        if len(events) >= 24:
            break
    return events


def _shot_events(ticks, reads, plays, quality) -> list[dict]:
    events = []
    for tick in ticks:
        read = _nearest_read(reads, tick.get("t_sec") or 0)
        play = _shot_play(tick, read, plays)
        if not play:
            continue
        label_team = tick.get("team") or ""
        official = {
            "made": play["made"],
            "value": play["value"],
            "description": play["description"],
        }
        if play.get("assist"):
            official["assist"] = {"name": play["assist"]}
        xfg = _xfg(play, quality)
        if xfg is not None:
            official["xfg"] = xfg
        events.append({
            "t": tick.get("t_sec") or 0,
            "type": "shot",
            "actor": _shot_actor(play, label_team),
            "source": "official",
            "confidence": 0.8,
            "official": official,
        })
    return events


def _nearest_read(reads, t) -> dict:
    ranked = [read for read in reads if read.get("clock")]
    if not ranked:
        return {}
    return min(ranked, key=lambda read: abs(float(read.get("t") or 0) - float(t)))


def _shot_play(tick, read, plays):
    period = read.get("period")
    clock = _clock_seconds(read.get("clock"))
    team = str(tick.get("team") or "").upper()
    if clock is None:
        return None
    candidates = [
        play for play in plays
        if play["kind"] == "shot" and play["team"] == team and play["clock"] is not None
        and abs(play["clock"] - clock) <= SCORE_LAG_S
        and (period is None or play["period"] == period or _period_num(period) == play["period"])
    ]
    return min(candidates, key=lambda play: abs(play["clock"] - clock)) if candidates else None


def _xfg(play, quality):
    key = (play["period"], play["clock"], play["team"])
    if key in quality:
        return quality[key]
    near = [
        value for (period, clock, team), value in quality.items()
        if period == play["period"] and team == play["team"] and clock is not None
        and abs(clock - play["clock"]) <= 2
    ]
    return near[0] if near else None


def _shot_actor(play, team: str) -> dict:
    if not play:
        return {"name": TEAM_ZH.get(team, team or "球员"), "team": team}
    number = ""
    match = re.search(r"#(\d+)", play.get("description") or "")
    if match:
        number = match.group(1)
    zh = NAMES.get((play["team"], number))
    return {"name": zh or play.get("player") or TEAM_ZH.get(team, "球员"), "team": play["team"], "number": number}


def _actor(who, away) -> dict:
    if not who:
        return {"name": "持球人", "team": away}
    return {
        "name": NAMES.get((who["team"], who["number"]), who.get("name") or "持球人"),
        "team": who["team"],
        "number": who["number"],
    }


def _foot_distance(box, ball) -> float:
    x, y, w, h = [float(value) for value in box[:4]]
    bx, by = float(ball[0]), float(ball[1])
    return (x + w / 2 - bx) ** 2 + (y + h - by) ** 2


def _page_events(stream: dict) -> list[dict]:
    rows = []
    for event in (stream.get("possessions") or [{}])[0].get("events") or []:
        official = event.get("official") or {}
        label = _label(event, official)
        rows.append({
            "t": event.get("t") or 0,
            "timecode": "",
            "label": label,
            "eventType": {"receive": "hold", "pass": "pass", "shot": "shot"}.get(event.get("type"), event.get("type") or ""),
            "detail": official.get("description") or "",
            "source": event.get("source") or "",
            "shot": {"xfg": official["xfg"]} if "xfg" in official else None,
        })
    return rows


def _label(event: dict, official: dict) -> str:
    actor = (event.get("actor") or {}).get("name") or ""
    kind = event.get("type")
    if kind == "shot":
        made = "命中" if official.get("made", event.get("result") == "make") else "不中"
        value = official.get("value") or ""
        xfg = official.get("xfg")
        extra = f" 预期命中率 {round(float(xfg) * 100)}%" if isinstance(xfg, (int, float)) else ""
        return f"{actor} {value}分{made}{extra}".replace(" 分", "分")
    if kind == "pass":
        return f"{actor} 传球"
    return f"{actor} 接球"


def _pair(away, home):
    try:
        return (int(away), int(home))
    except (TypeError, ValueError):
        return None


def _period_num(value) -> int | None:
    if isinstance(value, int):
        return value
    text = str(value or "")
    for index, token in enumerate(("一", "二", "三", "四"), start=1):
        if token in text:
            return index
    upper = text.upper()
    if upper[:1].isdigit():
        return int(upper[:1])
    return None


def _clock_seconds(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    iso = re.fullmatch(r"PT(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?", text)
    if iso:
        return int(iso.group(1) or 0) * 60 + float(iso.group(2) or 0)
    clock = re.fullmatch(r"(\d+):(\d+(?:\.\d+)?)", text)
    if clock:
        return int(clock.group(1)) * 60 + float(clock.group(2))
    return None
