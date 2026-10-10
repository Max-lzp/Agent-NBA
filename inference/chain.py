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
    'If the person is a referee (grey shirt or black-and-white stripes, not a team jersey), set referee to true. '
    'Reply ONLY as JSON: {"number":"<digits or unknown>","color":"<color>","referee":<true or false>}'
)
MIN_VOTES = 2
FRAMES_PER_TRACK = 12
SCORE_LAG_S = 8.0
NAMES = {
    ("HOU", "0"): "霍勒迪", ("HOU", "1"): "阿门", ("HOU", "2"): "芬尼-史密斯",
    ("HOU", "7"): "杜兰特", ("HOU", "8"): "泰特", ("HOU", "10"): "小史密斯",
    ("HOU", "15"): "谢泼德", ("HOU", "17"): "伊森", ("HOU", "20"): "奥科吉",
    ("HOU", "28"): "申京", ("HOU", "30"): "卡佩拉",
    ("DAL", "00"): "克里斯蒂", ("DAL", "0"): "克里斯蒂", ("DAL", "1"): "哈迪",
    ("DAL", "3"): "戴维斯", ("DAL", "5"): "拉塞尔", ("DAL", "7"): "鲍威尔",
    ("DAL", "9"): "内姆哈德", ("DAL", "10"): "威廉姆斯", ("DAL", "13"): "马绍尔",
    ("DAL", "16"): "马丁", ("DAL", "21"): "加福德", ("DAL", "25"): "华盛顿",
    ("DAL", "30"): "西塞", ("DAL", "31"): "克莱", ("DAL", "32"): "弗拉格",
}
TEAM_ZH = {"DAL": "独行侠", "HOU": "火箭"}
TEAM_EN = {"DAL": "Dallas", "HOU": "Houston"}
EN_NAMES: dict[tuple[str, str], str] = {}
PERSON_IDS: dict[tuple[str, str], int] = {}
RANK_MIN_GP, RANK_MIN_MPG = 20, 15.0  # 和 pipeline/enrich/defense.py 的队内轮换球员口径一致


def link(job_id: str, video: Path, frames: list, board: dict, bucket: str) -> dict:
    s3 = boto3.client("s3", region_name=REGION)
    _download_official(s3, bucket)
    roster = _roster()
    started = time.time()
    reads, tracks, stats = _read_jerseys(video, frames)
    game_id, plays, quality = _official_game(board.get("reads") or [])
    pinned = _pin_official(plays, board, roster)
    _stamp_numbers(plays, game_id)
    court = _clip_court(plays, board, roster)
    LEARNED_COLORS.clear()
    LEARNED_COLORS.update(_learn_team_colors(reads, court))
    print("球衣颜色归属：", LEARNED_COLORS or os.environ.get("TEAM_COLORS") or "旧规则")
    votes = _build_votes(reads, tracks, court)
    named = _stitch(_apply_votes(votes, roster, pinned, board, plays))
    matches = _matches_from_tracks(named)
    people = _people(named)
    width, height = _frame_size(video)
    tags = _playback_tags(named, width, height, frames)
    extras = _official_extras(game_id, court)
    stream = _stream(job_id, game_id, frames, board, matches, plays, quality, people, extras)
    possession = (stream.get("possessions") or [{}])[0]
    defense = _lead_defense(named, frames, possession.get("offense_team") or "", _attack_windows(possession.get("events") or []))
    _mark_defense(tags, defense)
    stream["defense"] = defense
    try:
        cards = _cards(possession.get("events") or [], frames, width, _team_ranks())
    except Exception as exc:  # noqa: BLE001 — 卡片出不来不影响成片
        print(f"定格卡没算出来：{exc}")
        cards = []
    stream["cards"] = cards
    script = _script(stream, board, extras, matches, frames)
    stream["script"] = script
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
        "teamColors": dict(LEARNED_COLORS),
        "court": {team: sorted(numbers) for team, numbers in court.items()},
        "pinned": pinned,
        "people": people,
        "defense": defense,
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
    s3.put_object(
        Bucket=bucket,
        Key=f"structured/{job_id}.json",
        Body=json.dumps(stream, ensure_ascii=False).encode(),
        ContentType="application/json",
    )
    return {
        "script": script,
        "cards": cards,
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
    wanted = int(os.environ.get("IDENTITY_WORKERS") or 16)
    workers = min(max(1, min(wanted, 32)), max(1, len(chosen)))
    print(f"认人并发 {workers} 路，共 {len(chosen)} 张裁切图，Rekognition {'关' if os.environ.get('SKIP_REKOGNITION') == '1' else '开'}")
    with ThreadPoolExecutor(workers) as pool:
        reads = list(pool.map(lambda item: _ask(bedrock, rekognition, textract, *item), chosen))
    stats["bedrockCalls"] = len(reads)
    stats["rekognitionCalls"] = sum(1 for read in reads if read.get("rekognitionCalled"))
    stats["textractCalls"] = sum(1 for read in reads if read.get("textractCalled"))
    return reads, tracks, stats


def _build_votes(reads: list[dict], tracks: list[dict], court: dict) -> list[dict]:
    _spread_referee(reads, court)
    by_track = {track["id"]: track for track in tracks}
    votes = []
    for track_id, group in _grouped(reads).items():
        track = by_track.get(track_id) or {"id": track_id, "boxes": []}
        for part, (boxes, sub, referee) in enumerate(_split_referee(track["boxes"], group)):
            if referee:
                votes.append({
                    "id": f"{track_id}.{part}", "boxes": boxes, "number": "", "ballots": 0,
                    "color": "", "colorVotes": 0, "samples": len(sub), "referee": True,
                })
                continue
            number, ballots, color, color_votes = _majority(sub)
            votes.append({
                "id": f"{track_id}.{part}",
                "boxes": boxes,
                "number": number,
                "ballots": ballots,
                "color": color,
                "colorVotes": color_votes,
                "samples": len(sub),
                "referee": False,
            })
    return votes


def _spread_referee(reads: list[dict], court: dict) -> None:
    """Qwen 对同一个裁判时答时不答。裁判读到过的号码，在别的帧里如果按球衣颜色对不上那一队的场上号码，也算裁判。"""
    numbers = {
        number for read in reads if read.get("referee")
        for number in (read.get("number"), read.get("rekognition")) if number
    }
    for read in reads:
        if read.get("referee"):
            continue
        hit = {read.get("number"), read.get("rekognition")} & numbers
        if not hit:
            continue
        team = _team_from_color(read.get("color") or "")
        if not team or not hit & court.get(team, set()):
            read["referee"] = True


def _clip_court(plays: list, board: dict, roster: dict) -> dict:
    """片段里出现过的场上号码，按队分开。"""
    table = _court_table(plays, roster)
    out: dict[str, set] = {}
    for read in board.get("reads") or []:
        court = _court_at(table, _period_num(read.get("period")), _clock_seconds(read.get("clock")))
        for team, people in court.items():
            out.setdefault(team, set()).update(person["number"] for person in people)
    return out


COLOR_WORDS = (
    ("red", ("red", "maroon", "crimson", "burgundy", "红")),
    ("black", ("black", "黑")),
    ("navy", ("navy",)),
    ("blue", ("blue", "蓝")),
    ("white", ("white", "cream", "白")),
    ("green", ("green", "绿")),
    ("yellow", ("yellow", "gold", "黄")),
    ("purple", ("purple", "紫")),
    ("orange", ("orange", "橙")),
)
LEARNED_COLORS: dict[str, str] = {}


def _canon_color(color: str) -> str:
    text = (color or "").lower()
    if not text or _referee_color(text):
        return ""
    for canon, words in COLOR_WORDS:
        if any(word in text for word in words):
            return canon
    return ""


def _learn_team_colors(reads: list[dict], court: dict) -> dict[str, str]:
    """每种球衣颜色下读到的号码去对场上 10 人，哪一队独有的号码重合得多，这种颜色就归哪一队。"""
    counts: dict[str, Counter] = {}
    for read in reads:
        if read.get("referee"):
            continue
        canon = _canon_color(read.get("color") or "")
        if not canon:
            continue
        for number in {read.get("number"), read.get("rekognition")} - {None, ""}:
            counts.setdefault(canon, Counter())[number] += 1
    learned = {}
    for canon, numbers in counts.items():
        score = {}
        for team, own in court.items():
            others = set().union(*(nums for name, nums in court.items() if name != team)) if len(court) > 1 else set()
            score[team] = sum(count for number, count in numbers.items() if number in own and number not in others)
        ranked = sorted(score.items(), key=lambda item: item[1], reverse=True)
        if not ranked or ranked[0][1] < 3:
            continue
        second = ranked[1][1] if len(ranked) > 1 else 0
        if ranked[0][1] >= 2 * second:
            learned[canon] = ranked[0][0]
    return learned


def _split_referee(boxes: list, reads: list) -> list[tuple[list, list, bool]]:
    """简易连线会把裁判和球员接成一条。裁判和球员交接处切开，裁判那段不投票、不挂标签。"""
    ordered = sorted(reads, key=lambda read: read["t"])
    runs: list[list] = []
    for read in ordered:
        if runs and bool(runs[-1][0].get("referee")) == bool(read.get("referee")):
            runs[-1].append(read)
        else:
            runs.append([read])
    if len(runs) <= 1:
        return [(boxes, ordered, bool(ordered and ordered[0].get("referee")))]
    cuts = [(runs[k][-1]["t"] + runs[k + 1][0]["t"]) / 2 for k in range(len(runs) - 1)]
    edges = [float("-inf")] + cuts + [float("inf")]
    parts = []
    for k, run in enumerate(runs):
        lo, hi = edges[k], edges[k + 1]
        part = [(stamp, box) for stamp, box in boxes if lo <= float(stamp) < hi]
        if part:
            parts.append((part, run, bool(run[0].get("referee"))))
    return parts


def _ask(bedrock, rekognition, textract, track_id: int, stamp: float, payload: bytes) -> dict:
    number, color, error = "", "", ""
    referee = False
    textract_called = False
    try:
        response = _with_backoff(lambda: bedrock.converse(
            modelId=MODEL_ID,
            messages=[{"role": "user", "content": [
                {"image": {"format": "jpeg", "source": {"bytes": payload}}},
                {"text": PROMPT},
            ]}],
            inferenceConfig={"maxTokens": 200, "temperature": 0},
        ))
        text = " ".join(
            block.get("text", "")
            for block in response.get("output", {}).get("message", {}).get("content") or []
        )
        number, color, referee = _parse_qwen(text)
    except Exception as exc:  # noqa: BLE001 — Qwen 失败才落到 Textract；限流重试用完就当这一帧读不出
        error = str(exc)[:500]
        textract_called = not _throttled(exc)
    if textract_called:
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
    if os.environ.get("SKIP_REKOGNITION") != "1":
        try:
            detected = _with_backoff(lambda: rekognition.detect_text(Image={"Bytes": payload}))
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
        "referee": referee or _referee_color(color),
        "error": error,
        "textractCalled": textract_called,
        "rekognitionCalled": rek_called,
    }


def _throttled(exc: Exception) -> bool:
    code = getattr(exc, "response", {}).get("Error", {}).get("Code", "") if hasattr(exc, "response") else ""
    return code in {"ThrottlingException", "TooManyRequestsException", "ProvisionedThroughputExceededException"} \
        or "Throttl" in str(exc)


def _with_backoff(call, attempts: int = 3):
    """被限流就退避重试，最多 3 次；还是限流就把异常交回去，由调用方当作读不出。"""
    for attempt in range(attempts + 1):
        try:
            return call()
        except Exception as exc:  # noqa: BLE001
            if not _throttled(exc) or attempt == attempts:
                raise
            time.sleep(0.5 * (2 ** attempt))
    return None


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


PLAYER_CLASS = 4  # player_detector.pt：0 Ball、1 Clock、2 Hoop、3 Overlay、4 Player、5 Ref、6 Scoreboard


def _is_player(box) -> bool:
    return bool(box) and (len(box) < 6 or int(box[5]) == PLAYER_CLASS)


def _associate(frames: list) -> list[dict]:
    tracks: list[dict] = []
    active: list[tuple[int, list, float]] = []
    for frame in frames:
        stamp = float(frame.get("t") or 0)
        detections = [box for box in frame.get("players") or [] if _is_player(box) and box[2] * box[3] >= 40 * 80]
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
        if color_votes < MIN_VOTES or color_votes < 0.7 * sum(colors.values()):
            color, color_votes = "", 0
    return number, votes, color, color_votes


def _referee_color(color: str) -> bool:
    text = color.lower()
    if "black and white" in text or "white and black" in text or "黑白" in text:
        return True
    return any(word in text for word in ("gray", "grey", "stripe", "灰", "条纹"))


def _team_colors() -> list[tuple[str, str]]:
    """TEAM_COLORS="HOU=red,DAL=black"：这场两队的球衣颜色，按作业传入。"""
    pairs = []
    for part in os.environ.get("TEAM_COLORS", "").split(","):
        team, _, words = part.partition("=")
        team, words = team.strip().upper(), words.strip().lower()
        if team and words:
            for word in words.split("|"):
                if word.strip():
                    pairs.append((word.strip(), team))
    return pairs


def _team_from_color(color: str) -> str:
    """先用程序从读数里学出来的颜色归属；学不出来才看人工兜底的 TEAM_COLORS，再退回旧规则。"""
    text = color.lower()
    if _referee_color(text):
        return ""
    if LEARNED_COLORS:
        return LEARNED_COLORS.get(_canon_color(text), "")
    configured = _team_colors()
    if configured:
        for word, team in configured:
            if word in text:
                return team
        return ""
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
                    EN_NAMES[(tri, str(player.get("jerseyNum") or ""))] = str(player.get("familyName") or "")
                    PERSON_IDS[(tri, str(player.get("jerseyNum") or ""))] = player.get("personId")
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
    """号码对上了，球衣颜色也得是那个人的队。颜色分不出就不写名字。"""
    if not number or not court or color not in court:
        return None
    hits = [person for person in court.get(color) or [] if person["number"] == number]
    return hits[0] if len(hits) == 1 else None


def _one_track_each(named: list[dict]) -> list[dict]:
    """同一个名字：票数最多的那条先留；别的碎轨迹和已留的在时间上不重叠，就是同一个人接上，否则清掉名字。"""
    by_name: dict[str, list] = {}
    for item in named:
        if item.get("nameZh"):
            by_name.setdefault(item["nameZh"], []).append(item)
    for items in by_name.values():
        items.sort(key=lambda item: (item.get("ballots") or 0, item.get("samples") or 0), reverse=True)
        kept: list[dict] = []
        for item in items:
            if all(not _overlaps(item, other) or _same_body(item, other) for other in kept):
                kept.append(item)
                continue
            item["nameZh"] = ""
            item["rosterName"] = ""
            item["source"] = "duplicate"
    return named


def _same_body(a: dict, b: dict) -> bool:
    """同一时刻两条轨迹的框几乎重合，是检测器给同一个人出了两个框。"""
    boxes = {round(float(stamp), 3): box for stamp, box in b.get("boxes") or []}
    shared = [_iou(box, boxes[round(float(stamp), 3)]) for stamp, box in a.get("boxes") or [] if round(float(stamp), 3) in boxes]
    return bool(shared) and sum(shared) / len(shared) >= 0.5


def _span(item: dict) -> tuple[float, float]:
    stamps = [float(stamp) for stamp, _box in item.get("boxes") or []]
    return (min(stamps), max(stamps)) if stamps else (0.0, -1.0)


def _overlaps(a: dict, b: dict) -> bool:
    a0, a1 = _span(a)
    b0, b1 = _span(b)
    return a0 <= b1 and b0 <= a1


def _stitch(named: list[dict]) -> list[dict]:
    """没名字的碎轨迹，接到前后不超过 1 秒、位置挨着、同一队的轨迹上，继承它的名字。"""
    for _round in range(3):
        changed = False
        for item in named:
            if item.get("nameZh") or item.get("referee") or not item.get("boxes"):
                continue
            team = item.get("team") or ""
            start, end = _span(item)
            first, last = min(item["boxes"], key=lambda row: row[0]), max(item["boxes"], key=lambda row: row[0])
            best, best_d = None, None
            for other in named:
                if other is item or not other.get("nameZh") or not other.get("boxes"):
                    continue
                if team and other.get("team") != team:
                    continue
                o_start, o_end = _span(other)
                if o_end < start and start - o_end <= 1.0:
                    gap, here, there = start - o_end, first[1], max(other["boxes"], key=lambda row: row[0])[1]
                elif end < o_start and o_start - end <= 1.0:
                    gap, here, there = o_start - end, last[1], min(other["boxes"], key=lambda row: row[0])[1]
                else:
                    continue
                d = _center_distance(here, there)
                if d > float(there[3]) * (0.6 + gap):
                    continue
                if any(_overlaps(item, rival) for rival in named if rival.get("nameZh") == other["nameZh"]):
                    continue
                if best_d is None or d < best_d:
                    best, best_d = other, d
            if best is not None:
                item.update(
                    nameZh=best["nameZh"], team=best.get("team") or team, number=best.get("number") or "",
                    rosterName=best.get("rosterName") or "", source="stitched", label=best.get("label") or best["nameZh"],
                )
                changed = True
        if not changed:
            break
    return named


def _center_distance(a, b) -> float:
    ax, ay = float(a[0]) + float(a[2]) / 2, float(a[1]) + float(a[3]) / 2
    bx, by = float(b[0]) + float(b[2]) / 2, float(b[1]) + float(b[3]) / 2
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def _hang_labels(named: list[dict]) -> list[dict]:
    """名字从轨迹第一帧挂到最后一帧。有队无名才写 Agent detecting。"""
    for item in named:
        if item.get("referee"):
            item.update(nameZh="", team="", label="")
            continue
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


def _parse_qwen(text: str) -> tuple[str, str, bool]:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return "", "", False
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return "", "", False
    raw = str(data.get("number") or "").strip()
    number = "" if raw.lower() == "unknown" else _jersey_number([raw])
    referee = data.get("referee")
    referee = referee is True or str(referee).strip().lower() == "true"
    return number, str(data.get("color") or ""), referee


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
        "action": raw.get("actionNumber") or 0,
        "distance": raw.get("shotDistance"),
        "area": raw.get("area") or "",
        "personId": raw.get("personId"),
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


def _wide_frames(frames: list, src_h: float) -> set:
    """转播全景才挂标签。特写、慢镜头的框又高又少；一旦切到特写，要连着 1 秒回到全景才恢复。"""
    wide, streak, closed = set(), 0, False
    for frame in frames:
        heights = sorted(float(box[3]) for box in frame.get("players") or [] if box and float(box[3]) >= 48)
        median = heights[len(heights) // 2] if heights else 0
        ok = len(heights) >= 4 and heights[-1] <= 0.5 * src_h and median <= 0.3 * src_h
        streak = streak + 1 if ok else 0
        if not ok:
            closed = True
        if ok and (not closed or streak >= 8):
            closed = False
            wide.add(round(float(frame.get("t") or 0), 3))
    return wide


def _court_floor(named: list[dict]) -> dict:
    """每一帧场上最靠上的已认出球员的脚。比他再高出一截的框在看台上。"""
    floor: dict[float, float] = {}
    for item in named:
        if not item.get("nameZh"):
            continue
        for stamp, box in item.get("boxes") or []:
            key = round(float(stamp), 3)
            foot = float(box[1]) + float(box[3])
            floor[key] = min(floor.get(key, foot), foot)
    return floor


def _playback_tags(named: list[dict], src_w: float, src_h: float, frames: list | None = None) -> list[dict]:
    """标签坐标按成片 1280×720。名字和 Agent detecting 都从轨迹第一帧画到最后一帧。"""
    scale_x = 1280 / src_w if src_w else 1
    scale_y = 720 / src_h if src_h else 1
    wide = _wide_frames(frames, src_h) if frames else None
    floor = _court_floor(named)
    by_time: dict[float, list] = {}
    for item in named:
        text = item.get("label") or ""
        if not text:
            continue
        jersey = "" if text == "Agent detecting" else str(item.get("number") or "")
        for stamp, box in item.get("boxes") or []:
            x, y, w, h = [float(value) for value in box[:4]]
            if h * scale_y < 48 or w * scale_x > h * scale_y:
                continue
            key = round(float(stamp), 3)
            if wide is not None and key not in wide:
                continue
            if text == "Agent detecting" and key in floor and y + h < floor[key] - 0.04 * src_h:
                continue
            by_time.setdefault(key, []).append({
                "id": item.get("id"),
                "name": text,
                "jersey": jersey,
                "team": item.get("team") or "",
                "label": text,
                "x": round(x * scale_x, 1),
                "y": round(y * scale_y, 1),
                "w": round(w * scale_x, 1),
                "h": round(h * scale_y, 1),
            })
    out = []
    for stamp, players in sorted(by_time.items()):
        best: dict = {}
        rest = []
        for player in players:
            if player["name"] == "Agent detecting":
                rest.append(player)
                continue
            other = best.get(player["name"])
            if other is None or player["w"] * player["h"] > other["w"] * other["h"]:
                best[player["name"]] = player
        named_boxes = list(best.values())
        rest = [p for p in rest if all(_iou([p["x"], p["y"], p["w"], p["h"]], [q["x"], q["y"], q["w"], q["h"]]) < 0.5 for q in named_boxes)]
        out.append({"t": stamp, "players": named_boxes + rest})
    return out


def _stream(job_id, game_id, frames, board, matches, plays, quality, people=None, extras=None) -> dict:
    away = board.get("away") or os.environ.get("AWAY", "")
    home = board.get("home") or os.environ.get("HOME_TEAM", "")
    official = _clip_events(frames, board, plays, quality, extras or {})
    shooters = Counter(event["actor"].get("team") for event in official if event["type"] == "shot")
    offense = shooters.most_common(1)[0][0] if shooters else (away if away in {"HOU", "DAL"} else "HOU")
    events = _quiet_passes(_holder_events(frames, matches, offense, home), official, offense) + official
    events.sort(key=lambda item: item["t"])
    return {
        "version": "0.1",
        "jobId": job_id,
        "gameId": game_id,
        "possessions": [{
            "possession_id": "p0",
            "offense_team": offense,
            "events": events,
        }],
        "people": people or [],
    }


CLOCK_REACTION_S = 0.3  # 记分员听到哨声再停表的反应时间


def _clock_marks(reads: list) -> list[tuple[float, float]]:
    """比赛钟每跳一次的时刻和跳到的值。值 v 刚出现时，剩余时间正好是 v+1。"""
    marks, previous = [], None
    for read in reads:
        value = _clock_seconds(read.get("clock"))
        if value is None:
            continue
        if previous is None or value != previous:
            marks.append((float(read["t"]), value, previous is not None and previous - value == 1))
        previous = value
    real = [(t, value) for t, value, ticked in marks if ticked]
    return real or [(t, value) for t, value, _ticked in marks]


def _video_time(clock: float, marks: list) -> float | None:
    if not marks:
        return None
    t, value = min(marks, key=lambda mark: abs(mark[1] - clock))
    return t + (value + 1 - clock)


def _clock_stop(reads: list, t_tick: float) -> tuple[float, bool, bool]:
    """比分跳动前比赛钟停在哪一刻。返回（停表时刻，钟是否停过，进攻钟是否给出了更准的时刻）。"""
    before = [read for read in reads if float(read["t"]) <= t_tick and _clock_seconds(read.get("clock")) is not None]
    if not before:
        return t_tick, False, False
    final = _clock_seconds(before[-1]["clock"])
    t_first = float(before[-1]["t"])
    for read in reversed(before):
        if _clock_seconds(read["clock"]) != final:
            break
        t_first = float(read["t"])
    frozen = t_tick - t_first > 1.6
    shots = [
        (float(read["t"]), float(read["shot_clock"])) for read in before
        if read.get("shot_clock") is not None and t_first - 1.0 <= float(read["t"]) <= t_first + 1.5
    ]
    change, last = None, None
    for t, value in shots:
        if last is None or value != last:
            change, last = t, value
    if frozen and change is not None and last is not None and last < 24 and t_first - 0.5 <= change <= t_first + 1.2:
        return change, True, True
    return t_first + 0.5, frozen, False


def _ball_center(ball) -> tuple[float, float]:
    x, y, w, h = [float(value) for value in ball[:4]]
    return x + w / 2, y + h / 2


def _ball_ok(ball) -> bool:
    """球框要像个球：不大、接近正方形、不贴画面顶边。"""
    if not ball or len(ball) < 4:
        return False
    x, y, w, h = [float(value) for value in ball[:4]]
    return 8 <= w <= 70 and 8 <= h <= 70 and 0.6 <= w / h <= 1.6 and y > 2


def _ball_release(frames: list, lo: float, hi: float) -> float | None:
    """球在某人框里、下一帧到了他头顶以上：前一帧就是出手。"""
    window = [frame for frame in frames if lo <= float(frame.get("t") or 0) <= hi and _ball_ok(frame.get("ball"))]
    found = None
    for prev, cur in zip(window, window[1:]):
        px, py = _ball_center(prev["ball"])
        holder = _holder_box(prev.get("players") or [], px, py)
        if holder is None:
            continue
        cx, cy = _ball_center(cur["ball"])
        if cy < float(holder[1]) + 0.15 * float(holder[3]) and cy < py:
            found = float(prev["t"])
    return found


def _holder_box(players: list, bx: float, by: float):
    for box in players:
        x, y, w, h = [float(value) for value in box[:4]]
        if h < 48 or w > h:
            continue
        if x <= bx <= x + w and y <= by <= y + 0.7 * h:
            return box
    return None


def _ball_caught(frames: list, lo: float, hi: float) -> float | None:
    """球连着两帧落在某人上半身：抢到篮板。"""
    window = [frame for frame in frames if lo <= float(frame.get("t") or 0) <= hi]
    streak = 0
    for frame in window:
        ball = frame.get("ball")
        held = _ball_ok(ball) and _holder_box(frame.get("players") or [], *_ball_center(ball)) is not None
        streak = streak + 1 if held else 0
        if streak >= 3:
            return float(frame["t"]) - 0.25
    return None


def _clip_events(frames: list, board: dict, plays: list, quality: dict, extras: dict) -> list[dict]:
    """片段里的官方动作：谁、什么动作看官方逐回合；什么时候看画面——比赛钟、进攻钟和球离手。"""
    reads = [read for read in board.get("reads") or [] if read.get("clock")]
    if not reads or not plays:
        return _shot_events(board.get("ticks") or [], board.get("reads") or [], plays, quality)
    period = _period_num(reads[0].get("period"))
    clocks = [_clock_seconds(read["clock"]) for read in reads]
    clocks = [value for value in clocks if value is not None]
    marks = _clock_marks(reads)
    seen = {tuple(read["scores"]) for read in reads if read.get("scores")}
    lo, hi = min(clocks), max(clocks)
    window = [
        play for play in plays
        if play.get("period") == period and play.get("clock") is not None and lo - 0.5 <= play["clock"] <= hi + 1.0
        and play["kind"] in {"shot", "block", "rebound", "foul", "turnover", "steal"}
    ]
    window = [play for play in window if not (play["kind"] == "shot" and play["made"] and play["scores"] not in seen)]
    end_t = max(float(frame.get("t") or 0) for frame in frames) if frames else float(reads[-1]["t"])

    anchors = {}
    for tick in board.get("ticks") or []:
        read = _nearest_read(reads, tick.get("t_sec") or 0)
        play = _shot_play(tick, read, plays)
        if not play or play not in window:
            continue
        t_stop, frozen, precise = _clock_stop(reads, float(tick["t_sec"]))
        if frozen:
            guess = t_stop - CLOCK_REACTION_S if precise else t_stop
            release = _ball_release(frames, t_stop - 0.6, t_stop) or guess
        else:
            reset = tick.get("t_reset")
            base = float(reset) if reset is not None else float(tick["t_sec"]) - 1.5
            release = _ball_release(frames, base - 2.0, base) or base - 1.0
        anchors[id(play)] = (round(release, 3), round(t_stop, 3) if frozen else None)
    lags = []
    for play in window:
        if id(play) in anchors:
            mapped = _video_time(play["clock"], marks)
            if mapped is not None:
                lags.append(mapped - anchors[id(play)][0])
    lag = sorted(lags)[len(lags) // 2] if lags else 0.0

    events, last_miss = [], None
    for play in sorted(window, key=lambda item: (-item["clock"], item.get("action") or 0)):
        mapped = _video_time(play["clock"], marks)
        guess = max(0.0, (mapped if mapped is not None else 0.0) - lag)
        actor = _shot_actor(play, play.get("team") or "")
        if play["kind"] == "shot":
            if id(play) in anchors:
                t, stop = anchors[id(play)]
            else:
                t = _ball_release(frames, guess - 0.8, guess + 0.2) or guess
                stop = None
            official = {
                "made": play["made"],
                "value": play["value"],
                "description": play["description"],
                "distance": play.get("distance"),
                "zone": play.get("area") or "",
                "action": play.get("action"),
            }
            xfg = _xfg(play, quality)
            if xfg is not None:
                official["xfg"] = xfg
            if play.get("assist"):
                official["assist"] = {"name": play["assist"]}
            if play["made"] and play.get("scores"):
                official["scoreAfter"] = list(play["scores"])
            shot_clock = _shot_clock_at(reads, t)
            if shot_clock is not None:
                official["shotClock"] = shot_clock
            event = {"t": round(t, 3), "type": "shot", "actor": actor, "result": "make" if play["made"] else "miss",
                     "source": "official", "confidence": 0.8, "official": official}
            if stop is not None:
                event["clockStop"] = stop
            events.append(event)
            if not play["made"]:
                last_miss = event
        elif play["kind"] == "block":
            target = last_miss if last_miss and abs(last_miss["official"].get("action", 0) - (play.get("action") or 0)) <= 2 else None
            if target is not None:
                target["official"]["block"] = actor
        elif play["kind"] == "rebound":
            if not play.get("number"):
                continue
            start = (last_miss["t"] + 0.8) if last_miss else guess - 1.0
            limit = min(guess, start + 2.1) if guess > start else start + 2.1
            t = _ball_caught(frames, start, limit) or (last_miss["t"] + 1.0 if last_miss else guess)
            events.append({"t": round(min(t, end_t), 3), "type": "rebound", "actor": actor, "source": "official",
                           "confidence": 0.7, "official": {"offensive": "offensive" in (play.get("sub") or "")
                                                            or play.get("team") == (last_miss or {}).get("actor", {}).get("team")}})
        elif play["kind"] == "foul":
            shot = next((event for event in events if event["type"] == "shot" and event.get("clockStop") is not None
                         and abs(guess - event["t"]) <= 1.5), None)
            t = shot["clockStop"] if shot else guess
            if shot is not None:
                shot["official"]["foul"] = actor
            events.append({"t": round(t, 3), "type": "foul", "actor": actor, "source": "official", "confidence": 0.7,
                           "official": {"description": play["description"], "shooting": "shooting" in (play.get("sub") or "")}})
        else:
            events.append({"t": round(guess, 3), "type": play["kind"], "actor": actor, "source": "official",
                           "confidence": 0.6, "official": {"description": play["description"]}})
    defenders = extras.get("defenders") or {}
    for event in events:
        if event["type"] == "shot":
            key = (event["actor"].get("team"), event["actor"].get("number"))
            if key in defenders:
                event["official"]["defender"] = defenders[key]
    return events


SWITCH_HOLD_S = 1.5  # 新防守人要连续这么久都明显更近才算换防；挡拆挡开一下不算
LOST_HOLD_S = 1.0


def _attack_windows(events: list[dict]) -> list[tuple[float, float, tuple]]:
    """每次出手前那几秒，主攻就是这次的投篮人：从上一个官方动作之后 1 秒算起，最多往前 8 秒。"""
    official = sorted((event for event in events if event.get("source") == "official"), key=lambda event: event["t"])
    windows, previous = [], None
    for event in official:
        if event["type"] == "shot":
            actor = event.get("actor") or {}
            start = max(event["t"] - 8.0, (previous + 1.0) if previous is not None else event["t"] - 3.0, 0.0)
            windows.append((start, event["t"] + 0.3, (actor.get("team"), actor.get("number"))))
        previous = event["t"]
    return windows


def _who(item: dict):
    return ("name", item["nameZh"]) if item.get("nameZh") else ("id", item.get("id"))


def _lead_defense(named: list[dict], frames: list, offense: str, windows: list) -> list[dict]:
    """领防按轨迹距离算：防守方里离主攻脚下最近的人。新的人要连续 1.5 秒都明显更近才换，挡拆挡开一下不换。"""
    by_time: dict[float, list] = {}
    for item in named:
        if not item.get("team") or item.get("referee"):
            continue
        for stamp, box in item.get("boxes") or []:
            by_time.setdefault(round(float(stamp), 3), []).append((item, box))
    step = 1.0 / 8
    rows, current_window = [], None
    defender, rival, rival_since, missing_since = None, None, None, None
    for frame in frames:
        t = round(float(frame.get("t") or 0), 3)
        window = next((row for row in windows if row[0] <= t <= row[1]), None)
        if window is not current_window:
            current_window, defender, rival, rival_since, missing_since = window, None, None, None, None
        if window is None:
            continue
        here = by_time.get(t) or []
        mine = [(item, box) for item, box in here if (item.get("team"), item.get("number")) == window[2] and item.get("nameZh")]
        if not mine:
            continue
        handler, hbox = max(mine, key=lambda row: float(row[1][3]))
        hx, hy = float(hbox[0]) + float(hbox[2]) / 2, float(hbox[1]) + float(hbox[3])
        scale = max(float(hbox[3]), 1)
        guards: dict = {}
        for item, box in here:
            if item.get("team") in ("", offense):
                continue
            gx, gy = float(box[0]) + float(box[2]) / 2, float(box[1]) + float(box[3])
            d = ((gx - hx) ** 2 + (gy - hy) ** 2) ** 0.5 / scale
            key = _who(item)
            if key not in guards or d < guards[key][0]:
                guards[key] = (d, item)
        if not guards:
            continue
        best_key, (best_d, best) = min(guards.items(), key=lambda row: row[1][0])
        if defender is None:
            defender = best_key
        elif defender not in guards:
            missing_since = missing_since if missing_since is not None else t
            if t - missing_since >= LOST_HOLD_S:
                defender, rival, rival_since, missing_since = best_key, None, None, None
        else:
            missing_since = None
            if best_key != defender and best_d < 0.75 * guards[defender][0]:
                if rival != best_key:
                    rival, rival_since = best_key, t
                elif t - rival_since >= SWITCH_HOLD_S:
                    defender, rival, rival_since = best_key, None, None
            else:
                rival, rival_since = None, None
        if defender in guards:
            rows.append({"t": t, "handler": handler, "defender": guards[defender][1], "dkey": defender})
    return _defense_segments(rows, step)


def _defense_segments(rows: list[dict], step: float) -> list[dict]:
    segments: list[dict] = []
    for row in rows:
        last = segments[-1] if segments else None
        if last and last["_h"].get("nameZh") == row["handler"].get("nameZh") and last["dkey"] == row["dkey"] \
                and row["t"] - last["t1"] <= 0.5:
            last["t1"] = row["t"]
            last["ids"].add(row["defender"].get("id"))
            continue
        segments.append({"t0": row["t"], "t1": row["t"], "_h": row["handler"], "_d": row["defender"],
                         "dkey": row["dkey"], "ids": {row["defender"].get("id")}})
    out = []
    for seg in segments:
        if seg["t1"] - seg["t0"] < 2 * step:
            continue
        out.append({
            "t0": seg["t0"], "t1": round(seg["t1"] + step, 3),
            "handler": _person(seg["_h"]), "defender": _person(seg["_d"]),
            "defenderIds": sorted(str(value) for value in seg["ids"]),
        })
    return out


def _person(item: dict) -> dict:
    named = bool(item.get("nameZh"))
    return {"name": item.get("nameZh") or "", "team": item.get("team") or "", "number": (item.get("number") or "") if named else ""}


def _mark_defense(tags: list[dict], defense: list[dict]) -> None:
    """名签里给领防加上身份，成片和网页按这个画。"""
    for frame in tags:
        seg = next((row for row in defense if row["t0"] <= frame["t"] <= row["t1"]), None)
        if not seg or not seg["defender"]["name"] or seg["t1"] - seg["t0"] < 1.0:
            continue
        for player in frame["players"]:
            if str(player.get("id")) in seg["defenderIds"]:
                player["role"] = "defender"
                player["label"] = f"领防 {player['name']}" if player["name"] != "Agent detecting" else "领防"
            elif seg["handler"]["name"] and player.get("name") == seg["handler"]["name"]:
                player["role"] = "handler"


def _stars(rank: int) -> float:
    """队内第 1 名五星，每往后一名少半星，最少半星。"""
    return max(5.0 - 0.5 * (rank - 1), 0.5)


def _rows(path: Path) -> list[dict]:
    sets = (json.loads(path.read_text()).get("resultSets") or [{}])
    rs = sets[0] if isinstance(sets, list) else sets
    return [dict(zip(rs.get("headers") or [], row)) for row in rs.get("rowSet") or []]


def _team_ranks() -> dict:
    """队内轮换球员的赛季排名：进攻看场均得分，防守看防守效率（越低越好）。"""
    out: dict = {}
    for path in ROOT.rglob("leaguedashplayerstats_*.json"):
        team_id = path.stem.rsplit("_", 1)[-1]
        adv_path = path.with_name(f"leaguedashadvanced_{team_id}.json")
        adv = {row["PLAYER_ID"]: row for row in _rows(adv_path)} if adv_path.exists() else {}
        pool = [row for row in _rows(path) if (row.get("GP") or 0) >= RANK_MIN_GP and row["MIN"] / row["GP"] >= RANK_MIN_MPG]
        metrics = {
            "pts": ([(row["PTS"] / row["GP"], row["PLAYER_ID"]) for row in pool], False),
            "def_rating": ([(adv.get(row["PLAYER_ID"], {}).get("DEF_RATING"), row["PLAYER_ID"]) for row in pool], True),
        }
        for name, (values, low) in metrics.items():
            ranked = sorted(((v, pid) for v, pid in values if v is not None), key=lambda item: item[0], reverse=not low)
            for index, (value, pid) in enumerate(ranked):
                out.setdefault(pid, {})[name] = {"value": round(value, 1), "rank": index + 1, "of": len(ranked), "stars": _stars(index + 1)}
    return out


def _hoop_side(frames: list, t: float, width: float) -> str:
    """篮筐在画面哪一边：出手前后检测器标成 Hoop 的框在左半还是右半。"""
    xs = [
        float(box[0]) + float(box[2]) / 2 for frame in frames if abs(float(frame.get("t") or 0) - t) <= 1.5
        for box in frame.get("players") or [] if len(box) > 5 and int(box[5]) == 2
    ]
    if not xs:
        return ""
    xs.sort()
    return "left" if xs[len(xs) // 2] < width / 2 else "right"


def _cards(events: list[dict], frames: list, width: float, ranks: dict) -> list[dict]:
    """每次出手一组定格卡：进攻方放远离篮筐的一侧，防守方放靠近篮筐的一侧，各带队内排名和星级。"""
    out = []
    for event in events:
        if event.get("type") != "shot":
            continue
        official, actor = event.get("official") or {}, event.get("actor") or {}
        guard = official.get("block") or official.get("foul") or official.get("defender")
        offense = {"name": actor.get("name") or "", "team": actor.get("team") or "", "lines": []}
        xfg, distance = official.get("xfg"), official.get("distance")
        if isinstance(xfg, (int, float)):
            offense["lines"].append(f"预期命中率 {xfg * 100:.0f}%")
        if distance:
            offense["lines"].append(f"{float(distance):g} 英尺 · {'命中' if official.get('made') else '不中'}")
        rank = ranks.get(PERSON_IDS.get((actor.get("team"), actor.get("number"))), {}).get("pts")
        if rank:
            offense["rank"] = f"场均 {rank['value']} 分 · 队内第 {rank['rank']}/{rank['of']}"
            offense["stars"] = rank["stars"]
        card = {"t0": round(max(0.0, float(event["t"]) - 0.2), 3), "t1": round(float(event["t"]) + 3.2, 3),
                "hoop": _hoop_side(frames, float(event["t"]), width), "offense": offense}
        if guard and guard.get("name"):
            defense = {"name": guard["name"], "team": guard.get("team") or "", "lines": []}
            if official.get("block"):
                defense["lines"].append("盖帽")
            elif official.get("foul"):
                defense["lines"].append("投篮犯规")
            matchup = official.get("defender") or {}
            if matchup.get("name") == guard["name"] and matchup.get("fga"):
                defense["lines"].append(f"本场防他 {matchup['fga']} 投 {matchup['fgm']} 中")
            rank = ranks.get(PERSON_IDS.get((guard.get("team"), guard.get("number"))), {}).get("def_rating")
            if rank:
                defense["rank"] = f"防守效率 {rank['value']} · 队内第 {rank['rank']}/{rank['of']}"
                defense["stars"] = rank["stars"]
            card["defense"] = defense
        out.append(card)
    return out


def _official_extras(game_id: str, court: dict) -> dict:
    """matchups 里每个进攻球员被场上哪位防守人防得最多；shotquality 里两队全场平均预期命中率。"""
    out = {"defenders": {}, "teamQuality": {}}
    if not game_id:
        return out
    for path in ROOT.rglob(f"{game_id}_boxscorematchups*.json"):
        doc = (json.loads(path.read_text()).get("boxScoreMatchups") or {})
        sides = [doc.get("homeTeam") or {}, doc.get("awayTeam") or {}]
        for side, other in ((sides[0], sides[1]), (sides[1], sides[0])):
            tri, opp = str(side.get("teamTricode") or "").upper(), str(other.get("teamTricode") or "").upper()
            for player in side.get("players") or []:
                rows = [
                    row for row in player.get("matchups") or []
                    if str(row.get("jerseyNum") or "") in court.get(opp, set())
                ]
                if not rows:
                    continue
                row = max(rows, key=lambda item: (
                    (item.get("statistics") or {}).get("matchupFieldGoalsAttempted") or 0,
                    (item.get("statistics") or {}).get("partialPossessions") or 0,
                ))
                stats = row.get("statistics") or {}
                number = str(row.get("jerseyNum") or "")
                out["defenders"][(tri, str(player.get("jerseyNum") or ""))] = {
                    "name": NAMES.get((opp, number), row.get("familyName") or ""),
                    "team": opp,
                    "number": number,
                    "fga": stats.get("matchupFieldGoalsAttempted") or 0,
                    "fgm": stats.get("matchupFieldGoalsMade") or 0,
                    "blocks": stats.get("matchupBlocks") or 0,
                    "fouls": stats.get("shootingFouls") or 0,
                }
        break
    for path in ROOT.rglob(f"{game_id}_shotquality*.json"):
        for team in json.loads(path.read_text()).get("teams") or []:
            if team.get("shotQuality") is not None:
                out["teamQuality"][str(team.get("teamAbbreviation") or "").upper()] = float(team["shotQuality"])
        break
    return out


SHOT_WORDS = (
    ("step back", "后撤步", "step-back"), ("pullup", "急停", "pull-up"), ("fadeaway", "后仰", "fadeaway"),
    ("turnaround", "转身", "turnaround"), ("driving", "突破", "driving"), ("floating", "抛投", "floater"),
    ("layup", "上篮", "layup"), ("dunk", "扣篮", "dunk"), ("hook", "勾手", "hook"), ("bank", "打板", "off the glass"),
)


def _shot_words(description: str) -> tuple[str, str]:
    text = (description or "").lower()
    zh = [word for key, word, _en in SHOT_WORDS if key in text]
    en = [word for key, _zh, word in SHOT_WORDS if key in text]
    return "".join(zh), " ".join(en)


def _script(stream: dict, board: dict, extras: dict, matches: list, frames: list) -> list[dict]:
    """按时间轴写解说：开场交代比分，每个官方动作一句，再插数据；空出 5 秒以上就补一句持球人或进攻时间。"""
    events = (stream.get("possessions") or [{}])[0].get("events") or []
    reads = [read for read in board.get("reads") or [] if read.get("clock") and read.get("scores")]
    away = board.get("away") or os.environ.get("AWAY", "")
    home = board.get("home") or os.environ.get("HOME_TEAM", "")
    offense = (stream.get("possessions") or [{}])[0].get("offense_team") or ""
    end_t = max((float(frame.get("t") or 0) for frame in frames), default=float(reads[-1]["t"]) if reads else 20.0)
    lines: list[dict] = []

    def say(t, zh, en):
        lines.append({"t": round(max(0.2, min(t, end_t - 1.0)), 2), "zh": zh, "en": en})

    if reads:
        first = reads[0]
        period = _period_num(first.get("period"))
        clock = _clock_seconds(first.get("clock"))
        a, h = first["scores"][0], first["scores"][1]
        lead = away if a >= h else home
        hi, lo = max(a, h), min(a, h)
        if period and clock is not None:
            mm, ss = int(clock // 60), int(clock % 60)
            zh_period = "一二三四"[period - 1] if 1 <= period <= 4 else str(period)
            en_period = {1: "first", 2: "second", 3: "third", 4: "fourth"}.get(period, f"period {period}")
            zh_score = f"双方{a}平" if a == h else f"{TEAM_ZH.get(lead, lead)}{hi}比{lo}领先"
            en_score = f"tied at {a}" if a == h else f"{TEAM_EN.get(lead, lead)} up {hi}-{lo}"
            say(0.3, f"第{zh_period}节还剩{mm}分{ss:02d}秒，{zh_score}。", f"{mm}:{ss:02d} left in the {en_period}, {en_score}.")
    quality = extras.get("teamQuality") or {}
    for event in events:
        kind, actor, official = event["type"], event.get("actor") or {}, event.get("official") or {}
        name, name_en = actor.get("name") or "", EN_NAMES.get((actor.get("team"), actor.get("number")), actor.get("name") or "")
        t = float(event["t"])
        if kind == "shot":
            zh_words, en_words = _shot_words(official.get("description"))
            distance = official.get("distance")
            xfg = official.get("xfg")
            block = official.get("block")
            if not event.get("result") == "make" and block:
                b_en = EN_NAMES.get((block.get("team"), block.get("number")), block.get("name"))
                say(t - 0.3, f"{name}{zh_words or '出手'}，被{block['name']}盖掉！", f"{name_en} {en_words or 'goes up'}, and {b_en} blocks it!")
                if isinstance(xfg, (int, float)) and distance:
                    say(t + 2.6, f"这次{distance:.0f}英尺出手，预期命中率只有{xfg * 100:.0f}%。",
                        f"From {distance:.0f} feet, expected make just {xfg * 100:.0f} percent.")
            elif event.get("result") == "make":
                clock = official.get("shotClock")
                rush = "进攻时间快到了，" if clock is not None and clock <= 3 else ""
                rush_en = "Clock winding down, " if rush else ""
                foul = official.get("foul")
                points = "三分" if official.get("value") == 3 else "跳投"
                say(t - 0.6, f"{rush}{name}{zh_words}{points}！", f"{rush_en}{name_en}, {en_words or 'the shot'}...")
                f_en = EN_NAMES.get((foul.get("team"), foul.get("number")), foul.get("name")) if foul else ""
                say(t + 1.8, f"命中！还造成{foul['name']}犯规！" if foul else "命中！",
                    f"Good! And the foul on {f_en}!" if foul else "Good!")
                team = actor.get("team")
                if isinstance(xfg, (int, float)):
                    avg = quality.get(team)
                    compare = f"，低于{TEAM_ZH.get(team, team)}全场平均{avg * 100:.0f}%" if avg and avg > xfg else ""
                    compare_en = f", below the {TEAM_EN.get(team, team)} average of {avg * 100:.0f}" if compare else ""
                    feet = f"{distance:.0f}英尺，" if distance else ""
                    say(t + 4.3, f"{feet}预期命中率只有{xfg * 100:.0f}%{compare}。",
                        f"Only a {xfg * 100:.0f} percent look{compare_en}.")
                after = official.get("scoreAfter")
                if after:
                    a, h = after
                    lead = away if a > h else home
                    zh_score = f"双方{a}平" if a == h else f"{TEAM_ZH.get(lead, lead)}{max(a, h)}比{min(a, h)}领先"
                    extra = "，还有一次加罚。" if foul else "。"
                    extra_en = ", and one free throw to come." if foul else "."
                    say(t + 6.8, f"{zh_score}{extra}", f"{TEAM_EN.get(away, away)} {a}, {TEAM_EN.get(home, home)} {h}{extra_en}")
        elif kind == "rebound":
            word = "进攻篮板" if official.get("offensive") else "篮板"
            say(t + 0.2, f"{name}抢到{word}！", f"{name_en} grabs the {'offensive ' if official.get('offensive') else ''}rebound!")
        elif kind == "turnover":
            say(t, f"{name}失误。", f"Turnover, {name_en}.")
        elif kind == "steal":
            say(t, f"{name}抢断！", f"Steal by {name_en}!")
    lines.sort(key=lambda row: row["t"])
    lines = _thin(lines, 2.4, end_t - 1.2)
    return _fill(lines, board, _attack_windows(events), end_t, extras.get("defenders") or {}, offense)


def _thin(lines: list[dict], gap: float, last: float) -> list[dict]:
    kept: list[dict] = []
    for line in lines:
        if kept and line["t"] - kept[-1]["t"] < gap:
            line = dict(line, t=round(kept[-1]["t"] + gap, 2))
        if line["t"] <= last:
            kept.append(line)
    return kept


def _fill(lines: list[dict], board: dict, windows: list, end_t: float, defenders: dict, offense: str = "") -> list[dict]:
    """两句之间空出 5 秒以上，就在中间补一句：出手前的进攻窗口里说投篮人和防他的人，其余时间报进攻时间。"""
    reads = [read for read in board.get("reads") or [] if read.get("shot_clock") is not None]
    out = list(lines)
    stamps = [0.0] + [line["t"] for line in lines] + [end_t]
    for left, right in zip(stamps, stamps[1:]):
        t = left + 2.4
        while right - t >= 2.4 and t - left >= 2.4 and right - left > 5.0:
            window = next((row for row in windows if row[0] <= t <= row[1]), None)
            holder = None
            if window:
                team, number = window[2]
                holder = {"team": team, "number": number, "name": NAMES.get((team, number), EN_NAMES.get((team, number), ""))}
            clock = min(reads, key=lambda read: abs(float(read["t"]) - t), default=None)
            if holder and holder["name"]:
                name = holder.get("name") or ""
                name_en = EN_NAMES.get((holder.get("team"), holder.get("number")), name)
                guard = defenders.get((holder.get("team"), holder.get("number")))
                if guard:
                    g_en = EN_NAMES.get((guard.get("team"), guard.get("number")), guard.get("name"))
                    out.append({"t": round(t, 2), "zh": f"{name}持球，{guard['name']}贴防。", "en": f"{name_en} with it, {g_en} guarding."})
                else:
                    out.append({"t": round(t, 2), "zh": f"{name}持球，寻找机会。", "en": f"{name_en} looking for an opening."})
            elif clock is not None and abs(float(clock["t"]) - t) <= 0.6 and float(clock["shot_clock"]) < 24:
                left_s = int(float(clock["shot_clock"]))
                out.append({"t": round(t, 2), "zh": f"进攻时间还剩{left_s}秒。", "en": f"{left_s} on the shot clock."})
            elif offense:
                out.append({"t": round(t, 2), "zh": f"{TEAM_ZH.get(offense, offense)}在半场组织进攻。",
                            "en": f"{TEAM_EN.get(offense, offense)} working it in the half court."})
            left = t
            t = left + 4.0
    out.sort(key=lambda row: row["t"])
    return out


def _shot_clock_at(reads: list, t: float) -> float | None:
    near = [read for read in reads if read.get("shot_clock") is not None and abs(float(read["t"]) - t) <= 0.6]
    if not near:
        return None
    return float(min(near, key=lambda read: abs(float(read["t"]) - t))["shot_clock"])


def _quiet_passes(holder: list[dict], official: list[dict], offense: str) -> list[dict]:
    """持球人换手只留进攻方两个都认出来的、相隔 1.2 秒以上、离官方动作 1 秒以外的；停表以后不算。"""
    stops = [event.get("clockStop") for event in official if event.get("clockStop") is not None]
    dead = min(stops) if stops else None
    kept, last_t = [], -9.0
    for event in holder:
        if event["type"] != "pass":
            if (event.get("actor") or {}).get("team") == offense:
                kept.append(event)
            continue
        actor, target = event.get("actor") or {}, event.get("target") or {}
        if not actor.get("number") or not target.get("number") or actor.get("team") != target.get("team"):
            continue
        if actor.get("team") != offense:
            continue
        if dead is not None and event["t"] > dead:
            continue
        if any(abs(event["t"] - other["t"]) < 1.0 for other in official) or event["t"] - last_t < 1.2:
            continue
        kept.append(event)
        last_t = event["t"]
    return kept


def _holder_events(frames, matches, away, home) -> list[dict]:
    named = {round(float(item["t"]), 1): item for item in matches}
    events = []
    previous = None
    for frame in frames:
        ball = frame.get("ball")
        players = [box for box in frame.get("players") or [] if _is_player(box)]
        if not _ball_ok(ball) or not players:
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
    number = play.get("number") or ""
    match = re.search(r"#(\d+)", play.get("description") or "")
    if match and not number:
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
            "shot": _page_shot(event, official) if event.get("type") == "shot" else None,
        })
    return rows


def _page_shot(event: dict, official: dict) -> dict:
    actor = event.get("actor") or {}
    shot = {
        "t": event.get("t") or 0,
        "name": actor.get("name") or "",
        "team": actor.get("team") or "",
        "number": actor.get("number") or "",
        "made": official.get("made"),
        "value": official.get("value"),
        "description": official.get("description") or "",
    }
    for key in ("xfg", "distance", "zone", "shotClock", "scoreAfter"):
        if official.get(key) is not None:
            shot[key] = official[key]
    if official.get("block"):
        shot["block"] = official["block"].get("name")
    if official.get("foul"):
        shot["foul"] = official["foul"].get("name")
    if official.get("defender"):
        shot["defender"] = official["defender"]
    if official.get("assist"):
        shot["assist"] = official["assist"].get("name")
    return shot


def _label(event: dict, official: dict) -> str:
    actor = (event.get("actor") or {}).get("name") or ""
    kind = event.get("type")
    if kind == "shot":
        made = "命中" if official.get("made", event.get("result") == "make") else "不中"
        if official.get("block"):
            made = f"被{official['block'].get('name')}盖帽"
        value = official.get("value") or ""
        xfg = official.get("xfg")
        extra = f" 预期命中率 {round(float(xfg) * 100)}%" if isinstance(xfg, (int, float)) else ""
        foul = f" {official['foul'].get('name')}犯规" if official.get("foul") else ""
        return f"{actor} {value}分{made}{foul}{extra}".replace(" 分", "分")
    if kind == "pass":
        return f"{actor} 传球"
    if kind == "rebound":
        return f"{actor} {'进攻' if official.get('offensive') else ''}篮板"
    if kind == "foul":
        return f"{actor} 犯规"
    if kind in {"turnover", "steal"}:
        return f"{actor} {'失误' if kind == 'turnover' else '抢断'}"
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
