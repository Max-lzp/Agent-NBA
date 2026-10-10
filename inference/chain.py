"""云上认人、事件清单和官方数据。

认人用 Qwen3 VL 对每条轨迹多帧投票。Rekognition 算第二票，Textract 只在 Qwen 调用失败时兜底。
事件清单按球和记分牌跳动写成 structured JSON，交给已有的校验 Lambda。
官方数据从媒体桶的 official/ 对上回合和预期命中率。得分人、助攻人先钉，投票号码再补其余人。
"""

from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import boto3
import cv2
from botocore.config import Config

REGION = os.environ.get("AWS_REGION", "us-east-1")
ROOT = Path("/tmp/official")
MODEL_ID = "qwen.qwen3-vl-235b-a22b"
PROMPT = (
    'This is a cropped frame of one basketball player from an NBA broadcast. '
    'What jersey number is on this player\'s uniform, and what color is the jersey? '
    'Reply ONLY as JSON: {"number":"<digits or unknown>","color":"<color>"}'
)
MIN_VOTES = 2
FRAMES_PER_TRACK = 12
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
    _download_official(s3, bucket)
    roster = _roster()
    started = time.time()
    reads, votes, stats = _read_jerseys(video, frames)
    game_id, plays, quality = _official_game(board.get("reads") or [])
    pinned = _pin_official(plays, board, roster)
    _stamp_numbers(plays, game_id)
    named = _apply_votes(votes, roster, pinned, board, plays)
    matches = _matches_from_tracks(named)
    people = _people(named)
    width, height = _frame_size(video)
    tags = _playback_tags(named, width, height)
    elapsed = round(time.time() - started, 1)
    first_second = sorted({
        item["nameZh"] for item in named
        if item.get("nameZh") and any(t <= 1.0 for t, _box in item["boxes"])
    })
    identity = {
        "jobId": job_id,
        "service": "bedrock",
        "model": MODEL_ID,
        "bedrockCalls": stats["bedrockCalls"],
        "rekognitionCalls": stats["rekognitionCalls"],
        "textractCalls": stats["textractCalls"],
        "identitySeconds": elapsed,
        "firstSecond": first_second,
        "pinned": pinned,
        "people": people,
        "tags": tags,
        "votes": [
            {key: value for key, value in item.items() if key != "boxes"}
            for item in named
        ],
        "reads": reads,
        "matches": matches,
    }
    s3.put_object(
        Bucket=bucket,
        Key=f"identity/{job_id}.json",
        Body=json.dumps(identity, ensure_ascii=False).encode(),
        ContentType="application/json",
    )
    stream = _stream(job_id, game_id, frames, board, matches, plays, quality, people)
    s3.put_object(
        Bucket=bucket,
        Key=f"structured/{job_id}.json",
        Body=json.dumps(stream, ensure_ascii=False).encode(),
        ContentType="application/json",
    )
    return {
        "events": _page_events(stream),
        "gameId": game_id,
        "identityCount": len(first_second),
        "bedrockCalls": stats["bedrockCalls"],
        "textractCalls": stats["textractCalls"],
        "identitySeconds": elapsed,
        "firstSecond": first_second,
        "firstSecondDetecting": _detecting_at(named, 1.0),
        "tags": tags,
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


def _read_jerseys(video: Path, frames: list) -> tuple[list[dict], list[dict], dict]:
    tracks = _associate(frames)
    chosen = []
    cap = cv2.VideoCapture(str(video))
    try:
        for track in tracks:
            for stamp, box in _pick_frames(track, frames)[:FRAMES_PER_TRACK]:
                cap.set(cv2.CAP_PROP_POS_MSEC, float(stamp) * 1000)
                ok, image = cap.read()
                if not ok:
                    continue
                crop = _torso(image, box)
                if crop is None:
                    continue
                encoded, buf = cv2.imencode(".jpg", crop)
                if not encoded:
                    continue
                chosen.append((track["id"], round(float(stamp), 3), buf.tobytes()))
    finally:
        cap.release()
    config = Config(read_timeout=40, retries={"max_attempts": 1})
    bedrock = boto3.client("bedrock-runtime", region_name=REGION, config=config)
    rekognition = boto3.client("rekognition", region_name=REGION)
    textract = boto3.client("textract", region_name=REGION)
    stats = {"bedrockCalls": 0, "rekognitionCalls": 0, "textractCalls": 0}
    reads = []
    workers = min(12, max(1, len(chosen)))
    with ThreadPoolExecutor(workers) as pool:
        reads = list(pool.map(lambda item: _ask(bedrock, rekognition, textract, *item), chosen))
    stats["bedrockCalls"] = len(reads)
    stats["rekognitionCalls"] = sum(1 for read in reads if read.get("rekognitionCalled"))
    stats["textractCalls"] = sum(1 for read in reads if read.get("textractCalled"))
    by_track = {track["id"]: track for track in tracks}
    votes = []
    for track_id, group in _grouped(reads).items():
        track = by_track.get(track_id) or {"id": track_id, "boxes": []}
        number, ballots, color, color_votes = _majority(group)
        votes.append({
            "id": track_id,
            "boxes": track["boxes"],
            "number": number,
            "ballots": ballots,
            "color": color,
            "colorVotes": color_votes,
            "samples": len(group),
        })
    return reads, votes, stats


def _ask(bedrock, rekognition, textract, track_id: int, stamp: float, payload: bytes) -> dict:
    number, color, error = "", "", ""
    textract_called = False
    try:
        response = bedrock.converse(
            modelId=MODEL_ID,
            messages=[{"role": "user", "content": [
                {"image": {"format": "jpeg", "source": {"bytes": payload}}},
                {"text": PROMPT},
            ]}],
            inferenceConfig={"maxTokens": 200, "temperature": 0},
        )
        text = " ".join(
            block.get("text", "")
            for block in response.get("output", {}).get("message", {}).get("content") or []
        )
        number, color = _parse_qwen(text)
    except Exception as exc:  # noqa: BLE001 — Qwen 失败才落到 Textract
        error = str(exc)[:500]
        textract_called = True
        try:
            detected = textract.detect_document_text(Document={"Bytes": payload})
            lines = [
                block.get("Text", "")
                for block in detected.get("Blocks") or []
                if block.get("BlockType") == "LINE"
            ]
            number = _jersey_number(lines)
        except Exception as fallback:  # noqa: BLE001
            error = f"{error} | textract {fallback}"[:500]
    rek_number = ""
    rek_called = False
    try:
        detected = rekognition.detect_text(Image={"Bytes": payload})
        rek_called = True
        words = [
            item.get("DetectedText", "")
            for item in detected.get("TextDetections") or []
            if item.get("Type") == "WORD"
        ]
        rek_number = _jersey_number(words)
    except Exception as exc:  # noqa: BLE001
        rek_called = True
        if not error:
            error = f"rekognition {exc}"[:500]
    return {
        "t": stamp,
        "track": track_id,
        "number": number,
        "rekognition": rek_number,
        "color": color,
        "error": error,
        "textractCalled": textract_called,
        "rekognitionCalled": rek_called,
    }


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


def _associate(frames: list) -> list[dict]:
    tracks: list[dict] = []
    active: list[tuple[int, list, float]] = []
    for frame in frames:
        stamp = float(frame.get("t") or 0)
        detections = [box for box in frame.get("players") or [] if box and box[2] * box[3] >= 40 * 80]
        pairs = []
        for index, (track_id, box, seen) in enumerate(active):
            if stamp - seen > 0.75:
                continue
            for detect_index, detect in enumerate(detections):
                overlap = _iou(box, detect)
                if overlap >= 0.25:
                    pairs.append((overlap, index, detect_index))
        pairs.sort(reverse=True)
        taken_tracks, taken_detections = set(), set()
        refreshed = {}
        for _overlap, index, detect_index in pairs:
            if index in taken_tracks or detect_index in taken_detections:
                continue
            taken_tracks.add(index)
            taken_detections.add(detect_index)
            track_id = active[index][0]
            tracks[track_id]["boxes"].append((stamp, detections[detect_index]))
            refreshed[index] = (track_id, detections[detect_index], stamp)
        nxt = [refreshed[index] if index in refreshed else item for index, item in enumerate(active) if index in refreshed or stamp - item[2] <= 0.75]
        for detect_index, detect in enumerate(detections):
            if detect_index in taken_detections:
                continue
            tracks.append({"id": len(tracks), "boxes": [(stamp, detect)]})
            nxt.append((len(tracks) - 1, detect, stamp))
        active = nxt
    return [track for track in tracks if len(track["boxes"]) >= 6]


def _pick_frames(track: dict, frames: list) -> list[tuple[float, list]]:
    by_time = {round(float(frame.get("t") or 0), 3): frame.get("players") or [] for frame in frames}
    ranked = []
    for stamp, box in track["boxes"]:
        area = box[2] * box[3]
        others = [item for item in by_time.get(round(stamp, 3), []) if item is not box and item[:4] != box[:4]]
        overlap = max((_iou(box, item) for item in others), default=0)
        if area < 70 * 140 or overlap > 0.35:
            continue
        ranked.append((area * (1 - overlap), stamp, box))
    ranked.sort(reverse=True)
    picked = []
    for _score, stamp, box in ranked:
        if any(abs(stamp - previous) < 0.4 for previous, _box in picked):
            continue
        picked.append((stamp, box))
        if len(picked) >= FRAMES_PER_TRACK:
            break
    if len(picked) < 10:
        for _score, stamp, box in ranked:
            if any(abs(stamp - previous) < 0.2 for previous, _box in picked):
                continue
            picked.append((stamp, box))
            if len(picked) >= 10:
                break
    return picked


def _majority(reads: list[dict]) -> tuple[str, int, str, int]:
    ballots = []
    for read in reads:
        if read.get("number"):
            ballots.append(read["number"])
        if read.get("rekognition"):
            ballots.append(read["rekognition"])
    counts = Counter(ballots)
    number, votes = ("", 0)
    if counts:
        number, votes = counts.most_common(1)[0]
        second = counts.most_common(2)[1][1] if len(counts) > 1 else 0
        if votes < MIN_VOTES or votes <= second:
            number = ""
    colors = Counter(_team_from_color(read.get("color") or "") for read in reads)
    colors.pop("", None)
    color, color_votes = ("", 0)
    if colors:
        color, color_votes = colors.most_common(1)[0]
        second = colors.most_common(2)[1][1] if len(colors) > 1 else 0
        if color_votes < MIN_VOTES or color_votes <= second:
            color, color_votes = "", 0
    return number, votes, color, color_votes


def _team_from_color(color: str) -> str:
    text = color.lower()
    if any(word in text for word in ("white", "白", "light")):
        return "HOU"
    if any(word in text for word in ("dark", "navy", "black", "blue", "深", "蓝", "黑")):
        return "DAL"
    return ""


def _apply_votes(votes: list[dict], roster: dict, pinned: list[dict], board: dict, plays: list) -> list[dict]:
    pins = {(item["team"], item["number"]): item for item in pinned}
    table = _court_table(plays, roster)
    named = []
    for vote in votes:
        number = vote.get("number") or ""
        period, clock = _moment(vote, board)
        court = _court_at(table, period, clock)
        person = _match_on_court(number, vote.get("color") or "", court)
        item = dict(vote)
        item["nameZh"] = ""
        if person:
            team, number = person["team"], person["number"]
            pin = pins.get((team, number))
            item.update(
                team=team,
                number=number,
                rosterName=person["name"],
                nameZh=NAMES.get((team, number), person["name"]),
                source="official" if pin else "qwen",
            )
        elif number:
            item.update(team="", rosterName="", nameZh="", source="offcourt")
        else:
            item.update(team="", rosterName="", nameZh="", source="")
        named.append(item)
    return _hang_labels(_one_track_each(named))


def _lookup(number: str, color_team: str, roster: dict):
    if not number:
        return None
    hits = [(team, name) for (team, num), name in roster.items() if num == number]
    if color_team:
        sided = [hit for hit in hits if hit[0] == color_team]
        if len(sided) == 1:
            return sided[0]
    return hits[0] if len(hits) == 1 else None


def _court_table(plays: list, roster: dict) -> list[tuple]:
    court = _opening_lineup(plays)
    rows = []
    for play in plays:
        team = play.get("team") or ""
        number = play.get("number") or ""
        if play.get("kind") == "substitution" and team in court and number:
            side = court[team]
            if play.get("sub") == "out":
                court[team] = [person for person in side if person["number"] != number]
            elif play.get("sub") == "in" and len(side) < 5 and all(person["number"] != number for person in side):
                side.append({"team": team, "number": number, "name": roster.get((team, number), play.get("family") or "")})
        period, clock = play.get("period"), play.get("clock")
        if period is not None and clock is not None:
            rows.append((period, clock, {name: list(people) for name, people in court.items()}))
    return rows


def _opening_lineup(plays: list) -> dict:
    court = {"HOU": [], "DAL": []}
    for play in plays:
        if play.get("kind") == "substitution":
            break
        team, number = play.get("team") or "", play.get("number") or ""
        if team in court and number and len(court[team]) < 5 and all(person["number"] != number for person in court[team]):
            court[team].append({"team": team, "number": number, "name": play.get("family") or ""})
    return court


def _stamp_numbers(plays: list, game_id: str) -> None:
    numbers = {}
    if game_id:
        for path in ROOT.rglob(f"{game_id}_boxscore.json"):
            doc = json.loads(path.read_text())
            for side in ("homeTeam", "awayTeam"):
                team = (doc.get("game") or {}).get(side) or {}
                tri = str(team.get("teamTricode") or "").upper()
                for player in team.get("players") or []:
                    numbers[(tri, str(player.get("familyName") or "").lower())] = str(player.get("jerseyNum") or "")
            break
    for play in plays:
        play["number"] = numbers.get((play.get("team") or "", (play.get("family") or "").lower()), "")


def _moment(vote: dict, board: dict) -> tuple:
    stamps = [stamp for stamp, _box in vote.get("boxes") or []]
    stamp = sorted(stamps)[len(stamps) // 2] if stamps else 0
    reads = [read for read in board.get("reads") or [] if read.get("clock")]
    if not reads:
        return None, None
    read = min(reads, key=lambda item: abs(float(item.get("t") or 0) - stamp))
    return _period_num(read.get("period")), _clock_seconds(read.get("clock"))


def _court_at(table: list, period, clock) -> dict:
    if period is None or clock is None:
        return {}
    chosen = None
    for row_period, row_clock, court in table:
        if row_period == period and row_clock >= clock:
            chosen = court
        elif row_period == period and row_clock < clock and chosen is not None:
            break
    return chosen or {}


def _match_on_court(number: str, color: str, court: dict):
    if not number or not court:
        return None
    teams = [color] if color in court else list(court)
    hits = [person for team in teams for person in court.get(team) or [] if person["number"] == number]
    return hits[0] if len(hits) == 1 else None


def _one_track_each(named: list[dict]) -> list[dict]:
    best = {}
    for item in named:
        name = item.get("nameZh") or ""
        if not name:
            continue
        current = best.get(name)
        rank = (item.get("ballots") or 0, item.get("samples") or 0)
        if current is None or rank > current[0]:
            best[name] = (rank, item)
    for item in named:
        name = item.get("nameZh") or ""
        if name and best.get(name, (None, None))[1] is not item:
            item["nameZh"] = ""
            item["rosterName"] = ""
            item["source"] = "duplicate"
    return named


def _hang_labels(named: list[dict]) -> list[dict]:
    """名字从轨迹第一帧挂到最后一帧。有队无名才写 Agent detecting。"""
    for item in named:
        if item.get("nameZh"):
            item["label"] = item["nameZh"]
            continue
        team = item.get("team") or item.get("color") or ""
        item["nameZh"] = ""
        if team:
            item["team"] = team
            item["label"] = "Agent detecting"
        else:
            item["team"] = ""
            item["label"] = ""
    return named


def _matches_from_tracks(named: list[dict]) -> list[dict]:
    matches = []
    seen = set()
    for item in named:
        if not item.get("nameZh"):
            continue
        for stamp, _box in item["boxes"]:
            key = (round(float(stamp), 1), item["nameZh"])
            if key in seen:
                continue
            seen.add(key)
            matches.append({
                "t": round(float(stamp), 3),
                "team": item.get("team") or "",
                "number": item.get("number") or "",
                "name": item["nameZh"],
            })
    return matches


def _pin_official(plays: list, board: dict, roster: dict) -> list[dict]:
    reads = [read for read in board.get("reads") or [] if read.get("clock")]
    clocks = [value for value in (_clock_seconds(read.get("clock")) for read in reads) if value is not None]
    if not clocks:
        return []
    period = _period_num(reads[0].get("period"))
    lo, hi = min(clocks) - 3, max(clocks) + 3
    by_last = {}
    for (team, number), name in roster.items():
        by_last.setdefault(name.lower(), []).append((team, number))
    pinned = []
    seen = set()

    def add(text: str, team_hint: str, role: str) -> None:
        last = re.sub(r"[^A-Za-z]", "", (text or "").split()[-1] if text else "").lower()
        hits = [hit for hit in by_last.get(last, []) if not team_hint or hit[0] == team_hint]
        if len(hits) != 1:
            return
        team, number = hits[0]
        if (team, number) in seen:
            return
        seen.add((team, number))
        pinned.append({
            "team": team,
            "number": number,
            "nameZh": NAMES.get((team, number), last),
            "role": role,
            "source": "official",
        })

    for play in plays:
        if play.get("period") != period or play.get("clock") is None or not (lo <= play["clock"] <= hi):
            continue
        if play.get("kind") == "shot":
            add(play.get("player") or "", play.get("team") or "", "scorer")
        if play.get("assist"):
            add(play["assist"], play.get("team") or "", "assist")
    return pinned


def _grouped(reads: list[dict]) -> dict[int, list[dict]]:
    groups: dict[int, list[dict]] = {}
    for read in reads:
        groups.setdefault(read["track"], []).append(read)
    return groups


def _parse_qwen(text: str) -> tuple[str, str]:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return "", ""
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return "", ""
    raw = str(data.get("number") or "").strip()
    number = "" if raw.lower() == "unknown" else _jersey_number([raw])
    return number, str(data.get("color") or "")


def _iou(a, b) -> float:
    ax, ay, aw, ah = [float(value) for value in a[:4]]
    bx, by, bw, bh = [float(value) for value in b[:4]]
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = aw * ah + bw * bh - inter
    return inter / union if union else 0.0


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
        "family": raw.get("playerName") or "",
        "sub": str(raw.get("subType") or "").lower(),
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


def _people(named: list[dict]) -> list[dict]:
    rows = []
    for item in named:
        label = item.get("label") or ""
        if not label:
            continue
        stamps = [float(stamp) for stamp, _box in item.get("boxes") or []]
        rows.append({
            "id": item.get("id"),
            "team": item.get("team") or "",
            "nameZh": item.get("nameZh") or "",
            "label": label,
            "t0": round(min(stamps), 3) if stamps else None,
            "t1": round(max(stamps), 3) if stamps else None,
        })
    return rows


def _detecting_at(named: list[dict], limit: float) -> int:
    return sum(
        1 for item in named
        if item.get("label") == "Agent detecting" and any(float(stamp) <= limit for stamp, _box in item.get("boxes") or [])
    )


def _frame_size(video: Path) -> tuple[float, float]:
    cap = cv2.VideoCapture(str(video))
    width = cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1280
    height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 720
    cap.release()
    return width, height


def _playback_tags(named: list[dict], src_w: float, src_h: float) -> list[dict]:
    """标签坐标按成片 1280×720。名字和 Agent detecting 都从轨迹第一帧画到最后一帧。"""
    scale_x = 1280 / src_w if src_w else 1
    scale_y = 720 / src_h if src_h else 1
    by_time: dict[float, list] = {}
    for item in named:
        text = item.get("label") or ""
        if not text:
            continue
        jersey = "" if text == "Agent detecting" else str(item.get("number") or "")
        for stamp, box in item.get("boxes") or []:
            x, y, w, h = [float(value) for value in box[:4]]
            if h * scale_y < 48:
                continue
            key = round(float(stamp), 3)
            by_time.setdefault(key, []).append({
                "name": text,
                "jersey": jersey,
                "team": item.get("team") or "",
                "label": text,
                "x": round(x * scale_x, 1),
                "y": round(y * scale_y, 1),
                "w": round(w * scale_x, 1),
                "h": round(h * scale_y, 1),
            })
    return [{"t": stamp, "players": players} for stamp, players in sorted(by_time.items())]


def _stream(job_id, game_id, frames, board, matches, plays, quality, people=None) -> dict:
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
        "people": people or [],
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
                "source": "qwen" if who else "cv",
                "confidence": 0.7 if who else 0.4,
            }
        else:
            event = {
                "t": round(float(frame["t"]), 3),
                "type": "pass",
                "actor": previous,
                "target": actor,
                "source": "qwen" if who else "cv",
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
