"""进球后的精彩回放。

从官方逐回合里找出盖帽、打进加犯规（三分记为 3+1），也可扩到扣篮和抢断。
官方时钟先对上记分牌，再靠停表、进攻时间重置这类画面信号把出手放到哨响之前。
没有画面信号时，回放窗口盖住录入时刻前后几秒。人物、时间和卡片数字都从
当次输入推，不写死任何一场的结果。

不改 ``inference/run_job.py``。成片出来之后可以这样接::

    from highlights import render_highlights

    doc = render_highlights(
        video=film,
        board=board,
        playbyplay=playbyplay_path,
        shotquality=shotquality_path,
        matchups=matchups_path,
        boxscore=boxscore_path,
        roster_stats=roster_dash_paths,  # leaguedashplayerstats / leaguedashadvanced
        identity=identity_doc,           # 含轨迹、队、号码、名字；没有框就只用官方防守人
        out_video=Path("/tmp/broadcast-highlights.mp4"),
        out_json=Path(f"/tmp/{job_id}-highlights.json"),
    )
    # 原片在成片开头，时间轴对齐。事件 t 用原片秒，replay 是回放在成片里的起止。
    for event in doc["events"]:
        clips_events.append(event)  # eventType == "highlight"
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import re
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEASON = ROOT / "data" / "season" / "2025-26"
NAMES_PATH = ROOT / "data" / "names" / "zh-names.json"
FONT_CANDIDATES = (
    ROOT / "web" / "fonts" / "MiSans-Regular.ttf",
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
)

MIN_REPLAY_S = 5.0
FREEZE_S = 2.0
PRE_ROLL_S = 1.5
# 官方录入往往落在出手之后。没有停表信号时，关键帧比录入时刻稍早，窗口再向前多留几秒。
ENTRY_LEAD_S = 0.6
WINDOW_BEFORE_S = 3.4
WINDOW_AFTER_S = 1.6
HOLD_S = 2.5
RESET_JUMP = 3.0
OUT_W, OUT_H = 1280, 720
PANEL = (20, 17, 14, 230)
RED = (255, 43, 43, 255)
GOLD = (245, 197, 66, 255)
CREAM = (243, 237, 226, 255)
MUTED = (184, 172, 156, 255)

ZONE_ZH = {
    "Restricted Area": "合理冲撞区",
    "In The Paint (Non-RA)": "油漆区（非合理冲撞区）",
    "Mid-Range": "中距离",
    "Left Corner 3": "左底角三分",
    "Right Corner 3": "右底角三分",
    "Above the Break 3": "弧顶以上三分",
    "Backcourt": "后场",
    "Corner 3": "底角三分",
}
BAND_ZH = {
    "near_six_ft": "近框（6 英尺内）",
    "near_ten_ft": "10 英尺内",
    "three_point": "三分",
}
BAND_EN = {
    "near_six_ft": "within 6 feet",
    "near_ten_ft": "within 10 feet",
    "three_point": "from three",
}
TITLES = {
    "block": "精彩回放·盖帽",
    "and1_3": "精彩回放·3+1",
    "and1_2": "精彩回放·打进加犯规",
    "dunk": "精彩回放·扣篮",
    "steal": "精彩回放·抢断",
}
DEF_LABEL = {
    "block": "盖帽",
    "and1_3": "犯规",
    "and1_2": "犯规",
    "dunk": "防守",
    "steal": "抢断",
}
OFF_KEYS = {"xfg", "distance", "fg3_pct", "zone_fg_pct", "blocks_against", "off_rank"}
DEF_KEYS = {"season_blocks", "block_pct", "defense_fg", "matchup", "season_steals", "def_rank"}
OFF_PICK = (("ts_pct", "真实命中率", True), ("pts", "得分", False), ("fg_pct", "命中率", True))
DEF_PICK = (("def_rating", "防守效率", False), ("blk", "盖帽", False))


def load_doc(source):
    """路径、gzip 或已经读好的对象。"""
    if source is None or isinstance(source, (dict, list)):
        return source
    path = Path(source)
    if path.suffix == ".gz" or path.name.endswith(".json.gz"):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return json.load(handle)
    return json.loads(path.read_text(encoding="utf-8"))


def _num(value):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _intish(value):
    number = _num(value)
    if number is None:
        return None
    if abs(number - round(number)) < 1e-6:
        return int(round(number))
    return number


def clock_seconds(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value or "").strip()
    iso = re.fullmatch(r"PT(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?", text, re.IGNORECASE)
    if iso and (iso.group(1) or iso.group(2)):
        return int(iso.group(1) or 0) * 60 + float(iso.group(2) or 0)
    clock = re.fullmatch(r"(\d+):(\d+(?:\.\d+)?)", text)
    if clock:
        return int(clock.group(1)) * 60 + float(clock.group(2))
    return None


def format_clock(seconds):
    if seconds is None:
        return ""
    minutes = int(seconds // 60)
    rest = seconds - minutes * 60
    if abs(rest - round(rest)) < 0.05:
        return f"{minutes}:{int(round(rest)):02d}"
    return f"{minutes}:{rest:04.1f}"


def period_num(value):
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    for index, token in enumerate(("一", "二", "三", "四"), start=1):
        if token in text:
            return index
    upper = text.upper()
    if upper[:1].isdigit():
        return int(upper[:1])
    return None


def parse_time(value):
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.timestamp()


def pct_text(value, digits=1):
    number = _num(value)
    if number is None:
        return None
    return f"{number * 100:.{digits}f}%"


def feet_text(value):
    number = _num(value)
    if number is None:
        return None
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _ratio(value, attempts):
    """0 次出手的命中率不当成 0% 展示。百分比原值是 0–1。"""
    if _num(attempts) is None or _num(attempts) <= 0:
        return None
    return _num(value)


def font_path():
    for path in FONT_CANDIDATES:
        if path.is_file():
            return path
    return None


def _actions(doc):
    if isinstance(doc, list):
        return doc
    game = (doc or {}).get("game") or doc or {}
    return game.get("actions") or []


def _shots(doc):
    if not doc:
        return []
    if isinstance(doc, list):
        return doc
    return doc.get("shots") or []


class ClockModel:
    """记分牌读数 → 视频秒。比赛钟在走的时候按实时拟合，停表单独记。"""

    def __init__(self, board: dict, duration: float):
        self.duration = float(duration or 0)
        self.samples = []
        for read in (board or {}).get("reads") or []:
            clock = clock_seconds(read.get("clock"))
            if clock is None:
                continue
            t = read.get("t")
            if t is None:
                t = read.get("t_sec")
            if t is None:
                continue
            shot = _num(read.get("shot_clock"))
            self.samples.append((float(t), period_num(read.get("period")), clock, shot))
        self.samples.sort()
        periods = [period for _, period, _, _ in self.samples if period is not None]
        self.period = Counter(periods).most_common(1)[0][0] if periods else None
        self._held = self._held_indexes()
        self.offset, self.slope = self._fit()
        self.whistles = self._whistles()

    def _held_indexes(self):
        held = set()
        samples = self.samples
        index = 0
        while index < len(samples):
            end = index
            while (
                end + 1 < len(samples)
                and samples[end + 1][1] == samples[index][1]
                and abs(samples[end + 1][2] - samples[index][2]) < 0.05
            ):
                end += 1
            if samples[end][0] - samples[index][0] >= HOLD_S:
                held.update(range(index + 1, end + 1))
            index = end + 1 if end > index else index + 1
        return held

    def _fit(self):
        running = [sample for index, sample in enumerate(self.samples) if index not in self._held]
        if self.period is not None:
            same = [sample for sample in running if sample[1] in (None, self.period)]
            if len(same) >= 2:
                running = same
        if len(running) < 2:
            if not running:
                return None, None
            t, _, clock, _ = running[0]
            return clock + t, -1.0
        times = [sample[0] for sample in running]
        clocks = [sample[2] for sample in running]
        mean_t = sum(times) / len(times)
        mean_c = sum(clocks) / len(clocks)
        var_t = sum((t - mean_t) ** 2 for t in times)
        slope = -1.0
        if var_t > 1e-6:
            slope = sum((t - mean_t) * (c - mean_c) for t, c in zip(times, clocks)) / var_t
        if abs(slope + 1) <= 0.35:
            offset = sum(c + t for t, c in zip(times, clocks)) / len(times)
            return offset, -1.0
        return mean_c - slope * mean_t, slope

    def map_clock(self, clock):
        if clock is None or self.offset is None or not self.slope:
            return None
        return (float(clock) - self.offset) / self.slope

    def _whistles(self):
        found = []
        previous = None
        for index, (t, period, clock, shot) in enumerate(self.samples):
            if shot is not None and previous is not None and shot - previous >= RESET_JUMP:
                found.append(self._release_before(index))
            if shot is not None:
                previous = shot
        if found:
            return found
        samples = self.samples
        index = 0
        while index < len(samples):
            end = index
            while (
                end + 1 < len(samples)
                and samples[end + 1][1] == samples[index][1]
                and abs(samples[end + 1][2] - samples[index][2]) < 0.05
            ):
                end += 1
            if samples[end][0] - samples[index][0] >= HOLD_S:
                found.append({
                    "t": max(0.0, samples[index][0] - 0.4),
                    "clock": samples[index][2],
                    "period": samples[index][1],
                })
            index = end + 1 if end > index else index + 1
        return found

    def _release_before(self, reset_index):
        """进攻时间跳回 24 的前一刻是哨响；同一比赛钟上进攻时间最小的读数最接近出手。"""
        _, period, clock, _ = self.samples[reset_index]
        prior = [
            sample for sample in self.samples[:reset_index]
            if sample[1] == period and abs(sample[2] - clock) < 0.2
        ]
        with_shot = [sample for sample in prior if sample[3] is not None]
        chosen = min(with_shot, key=lambda sample: sample[3]) if with_shot else (prior[0] if prior else self.samples[reset_index])
        return {"t": chosen[0], "clock": clock, "period": period}

    def as_dict(self):
        return {
            "period": self.period,
            "offset": None if self.offset is None else round(self.offset, 3),
            "slope": None if self.slope is None else round(self.slope, 4),
            "whistles": [
                {"t": round(item["t"], 3), "clock": format_clock(item["clock"]), "period": item["period"]}
                for item in self.whistles
            ],
        }


def _normalize(raw, order):
    kind = str(raw.get("actionType") or "").lower()
    description = raw.get("description") or ""
    descriptor = str(raw.get("descriptor") or "")
    sub = str(raw.get("subType") or "")
    text = " ".join((descriptor, sub, description)).lower()
    return {
        "order": order,
        "actionNumber": raw.get("actionNumber"),
        "kind": kind,
        "period": period_num(raw.get("period")),
        "clock": clock_seconds(raw.get("clock")),
        "clockText": raw.get("clock") or "",
        "time": parse_time(raw.get("timeActual")),
        "team": str(raw.get("teamTricode") or "").upper(),
        "personId": raw.get("personId"),
        "name": raw.get("playerNameI") or raw.get("playerName") or "",
        "family": raw.get("playerName") or "",
        "description": description,
        "descriptor": descriptor,
        "subType": sub,
        "made": str(raw.get("shotResult") or "").lower() == "made",
        "fg": kind in {"2pt", "3pt"} or raw.get("isFieldGoal") == 1,
        "three": kind == "3pt" or "3pt" in description.lower(),
        "distance": _num(raw.get("shotDistance")),
        "area": raw.get("area") or "",
        "side": str(raw.get("side") or "").lower(),
        "blockPersonId": raw.get("blockPersonId"),
        "blocked": "blocked" in description.lower() or raw.get("blockPersonId") is not None,
        "foulDrawn": raw.get("foulDrawnPersonId"),
        "officialId": raw.get("officialId"),
        "shootingFoul": kind == "foul" and "shooting" in text,
        "dunk": "dunk" in text,
        "steal": kind == "steal",
    }


def _same_moment(left, right, clock_slack=2.0, time_slack=12.0):
    if left["period"] != right["period"] and None not in (left["period"], right["period"]):
        return False
    if left["clock"] is None or right["clock"] is None:
        return False
    if abs(left["clock"] - right["clock"]) > clock_slack:
        return False
    if left["time"] is not None and right["time"] is not None and abs(left["time"] - right["time"]) > time_slack:
        return False
    return True


def _pair_block(shot, actions):
    if not shot["blocked"] and not any(item["kind"] == "block" and _same_moment(shot, item) for item in actions):
        return None
    blocks = [item for item in actions if item["kind"] == "block" and _same_moment(shot, item)]
    if shot["blockPersonId"] is not None:
        same = [item for item in blocks if item["personId"] == shot["blockPersonId"]]
        if same:
            return same[0]
        return {"personId": shot["blockPersonId"], "team": "", "name": "", "number": None, "description": ""}
    return blocks[0] if blocks else ({"personId": shot["blockPersonId"]} if shot["blockPersonId"] else None)


def _pair_foul(shot, actions, used):
    if not shot["made"]:
        return None
    fouls = []
    for item in actions:
        if not item["shootingFoul"] or id(item) in used or not _same_moment(shot, item):
            continue
        drawn = item["foulDrawn"]
        if drawn is not None and shot["personId"] is not None and drawn != shot["personId"]:
            continue
        fouls.append(item)
    if not fouls:
        return None
    fouls.sort(key=lambda item: abs((item["time"] or 0) - (shot["time"] or 0)))
    return fouls[0]


def detect_actions(playbyplay):
    raws = _actions(playbyplay)
    actions = [_normalize(raw, index) for index, raw in enumerate(raws)]
    highlights = []
    used_fouls = set()
    for shot in actions:
        if not shot["fg"]:
            continue
        block = _pair_block(shot, actions)
        foul = None if block else _pair_foul(shot, actions, used_fouls)
        kind = None
        if block:
            kind = "block"
        elif foul:
            used_fouls.add(id(foul))
            kind = "and1_3" if shot["three"] else "and1_2"
        elif shot["made"] and shot["dunk"]:
            kind = "dunk"
        if not kind:
            continue
        highlights.append({"type": kind, "shot": shot, "block": block, "foul": foul, "steal": None})
    for action in actions:
        if action["steal"]:
            highlights.append({"type": "steal", "shot": None, "block": None, "foul": None, "steal": action})
    return actions, highlights


def _anchors(actions, model: ClockModel):
    anchors = []
    for whistle in model.whistles:
        group = [
            action for action in actions
            if action["time"] is not None
            and action["clock"] is not None
            and action["period"] == whistle["period"]
            and abs(action["clock"] - whistle["clock"]) <= 1.0
        ]
        group.sort(key=lambda action: action["time"])
        if not group:
            anchors.append({**whistle, "time": None})
            continue
        earliest = group[0]["time"]
        cluster = [action for action in group if action["time"] - earliest <= 8]
        shot = next((action for action in cluster if action["fg"]), cluster[0])
        anchors.append({**whistle, "time": shot["time"]})
    return anchors


def _place(action, model: ClockModel, anchors):
    if action is None or action.get("clock") is None:
        return None, False
    if model.period is not None and action["period"] not in (None, model.period):
        return None, False
    for whistle in model.whistles:
        if action["period"] != whistle["period"] or abs(action["clock"] - whistle["clock"]) > 1.0:
            continue
        anchor = next((item for item in anchors if item is whistle or item.get("t") == whistle["t"]), whistle)
        origin = anchor.get("time")
        if action["time"] is not None and origin is not None and action["time"] - origin > 8:
            return whistle["t"] + (action["time"] - origin), True
        return whistle["t"], True
    mapped = model.map_clock(action["clock"])
    via = None
    usable = [item for item in anchors if item.get("time") is not None]
    if action["time"] is not None and usable:
        anchor = min(usable, key=lambda item: abs(item["time"] - action["time"]))
        if abs(anchor["time"] - action["time"]) <= 45:
            via = anchor["t"] + (action["time"] - anchor["time"])
    if via is not None and mapped is not None:
        if abs(via - mapped) > 6:
            return mapped - 1.5, False
        return min(via, mapped), False
    if via is not None:
        return via, False
    if mapped is not None:
        return mapped - 2.0, False
    return None, False


def _window(raw, on_whistle, mapped, duration):
    key = raw if on_whistle or raw is None else raw - ENTRY_LEAD_S
    if key is None:
        return None
    points = [value for value in (raw, key, mapped) if value is not None]
    start = min(points) - WINDOW_BEFORE_S
    end = max(points) + WINDOW_AFTER_S
    if end - start < 4:
        end = start + 4
    if duration:
        if start < 0:
            end = min(duration, end - start)
            start = 0.0
        if end > duration:
            start = max(0.0, start - (end - duration))
            end = min(duration, end)
        end = min(end, duration)
        start = max(0.0, start)
    if end - start < 0.8:
        return None
    key = min(max(key, start + 0.08), end - 0.08)
    return {"start": round(start, 3), "end": round(end, 3), "key": round(key, 3)}


def _in_clip(t, duration):
    if t is None:
        return False
    if not duration:
        return True
    return -0.4 <= t <= duration + 0.4


def load_names(source=None):
    doc = load_doc(source or NAMES_PATH) or {}
    by_id = {}
    for row in doc.get("players") or []:
        try:
            pid = int(row.get("player_id"))
        except (TypeError, ValueError):
            continue
        by_id[pid] = row
    teams = {row.get("team"): row for row in doc.get("teams") or []}
    return by_id, teams


def _dash_docs(roster_stats, season_dir: Path):
    if roster_stats is None:
        raw = season_dir / "raw"
        paths = sorted(raw.glob("*_base.json.gz")) + sorted(raw.glob("*_advanced.json.gz"))
        for path in paths:
            if path.is_file():
                yield path, load_doc(path)
        return
    items = roster_stats if isinstance(roster_stats, (list, tuple)) else [roster_stats]
    for item in items:
        if isinstance(item, dict):
            yield None, item
            continue
        path = Path(item)
        if path.is_dir():
            files = sorted(path.glob("leaguedashplayerstats*.json"))
            files += sorted(path.glob("leaguedashadvanced*.json"))
            files += sorted(path.glob("*_base.json.gz"))
            files += sorted(path.glob("*_advanced.json.gz"))
            for child in files:
                yield child, load_doc(child)
        elif path.is_file():
            yield path, load_doc(path)


def load_dash(roster_stats, season_dir: Path):
    table = {}
    for path, doc in _dash_docs(roster_stats, season_dir):
        if not isinstance(doc, dict):
            continue
        params = doc.get("parameters") or {}
        measure = str(params.get("MeasureType") or "")
        kind = "advanced" if "adv" in measure.lower() else "base"
        league = str(params.get("TeamID", "0")) in {"0", "None", ""} or (path is not None and "NBA" in path.name)
        result = (doc.get("resultSets") or [None])[0] or {}
        headers = result.get("headers") or []
        if "PLAYER_ID" not in headers:
            continue
        for row in result.get("rowSet") or []:
            record = dict(zip(headers, row))
            try:
                pid = int(record["PLAYER_ID"])
            except (TypeError, ValueError, KeyError):
                continue
            slot = table.setdefault(pid, {})
            if kind in slot and slot[kind].get("_league") and not league:
                continue
            record["_league"] = bool(league)
            slot[kind] = record
    return table


def _rank_rules():
    import sys

    root = str(ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from pipeline.enrich.defense import RANK_METRICS, RANK_MIN_GP, RANK_MIN_MPG, stars
    except ImportError:
        # 云上作业容器里没有仓库的 pipeline 包，口径和 pipeline/enrich/defense.py 保持一致。
        RANK_MIN_GP, RANK_MIN_MPG = 20, 15.0
        RANK_METRICS = {
            "pts": (lambda b, a: b["PTS"] / b["GP"], False),
            "fg_pct": (lambda b, a: b.get("FG_PCT"), False),
            "ts_pct": (lambda b, a: a.get("TS_PCT"), False),
            "stl": (lambda b, a: b["STL"] / b["GP"], False),
            "blk": (lambda b, a: b["BLK"] / b["GP"], False),
            "dreb": (lambda b, a: b["DREB"] / b["GP"], False),
            "def_rating": (lambda b, a: a.get("DEF_RATING"), True),
        }

        def stars(rank):
            return max(5.0 - 0.5 * (rank - 1), 0.5)

    return stars, RANK_MIN_GP, RANK_MIN_MPG, RANK_METRICS


def _metric_value(getter, base, advanced):
    try:
        value = getter(base, advanced or {})
    except (TypeError, ValueError, KeyError, ZeroDivisionError):
        return None
    number = _num(value)
    return number


def load_team_ranks(roster_stats, season_dir: Path):
    """同队轮换（≥20 场、场均 ≥15 分钟）的赛季排名。星级用 defense.stars。"""
    stars, min_gp, min_mpg, metrics = _rank_rules()
    groups: dict[str, dict] = {}
    league: dict[str, dict] = {}
    for path, doc in _dash_docs(roster_stats, season_dir):
        if not isinstance(doc, dict):
            continue
        params = doc.get("parameters") or {}
        measure = str(params.get("MeasureType") or "")
        kind = "advanced" if "adv" in measure.lower() else "base"
        is_league = str(params.get("TeamID", "0")) in {"0", "None", ""} or (path is not None and "NBA" in path.name)
        result = (doc.get("resultSets") or [None])[0] or {}
        headers = result.get("headers") or []
        if "PLAYER_ID" not in headers:
            continue
        target = league if is_league else groups
        file_team = ""
        if path is not None:
            file_team = path.name.split("_", 1)[0]
        for raw in result.get("rowSet") or []:
            record = dict(zip(headers, raw))
            try:
                pid = int(record["PLAYER_ID"])
            except (TypeError, ValueError, KeyError):
                continue
            team = str(record.get("TEAM_ABBREVIATION") or file_team or "").upper()
            if not team or team == "NBA":
                continue
            target.setdefault(team, {}).setdefault(pid, {})[kind] = record
    pools = dict(league)
    pools.update(groups)
    ranks: dict[int, dict] = {}
    for players in pools.values():
        pool = []
        for pid, slot in players.items():
            base = slot.get("base") or {}
            games = _num(base.get("GP"))
            minutes = _num(base.get("MIN"))
            if games is None or games < min_gp or minutes is None or minutes / games < min_mpg:
                continue
            pool.append((pid, base, slot.get("advanced") or {}))
        for name, (getter, low) in metrics.items():
            valued = []
            for pid, base, advanced in pool:
                value = _metric_value(getter, base, advanced)
                if value is not None:
                    valued.append((value, pid))
            valued.sort(key=lambda item: item[0], reverse=not low)
            for index, (value, pid) in enumerate(valued):
                ranks.setdefault(pid, {})[name] = {
                    "value": round(value, 3),
                    "rank": index + 1,
                    "of": len(valued),
                    "stars": stars(index + 1),
                }
    return ranks


def _csv_rows(path: Path):
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def load_season(season_dir: Path):
    zones = {}
    for row in _csv_rows(season_dir / "player_shot_zones.csv"):
        pid = _intish(row.get("player_id"))
        if not isinstance(pid, int):
            continue
        zones[(pid, row.get("scope") or "", row.get("zone") or "")] = row
    bands = {}
    for row in _csv_rows(season_dir / "player_defensive_shooting.csv"):
        pid = _intish(row.get("player_id"))
        if not isinstance(pid, int):
            continue
        bands[(pid, row.get("scope") or "", row.get("band") or "")] = row
    threes = {}
    for row in _csv_rows(season_dir / "player_shooting_three_bands.csv"):
        pid = _intish(row.get("player_id"))
        if not isinstance(pid, int):
            continue
        threes[(pid, row.get("scope") or "")] = row
    return {"zones": zones, "bands": bands, "threes": threes}


def _prefer(mapping, pid, team, extra=None):
    if extra is None:
        for scope in ("NBA", team, ""):
            if scope and (pid, scope) in mapping:
                return mapping[(pid, scope)]
        return None
    for scope in ("NBA", team, ""):
        if scope and (pid, scope, extra) in mapping:
            return mapping[(pid, scope, extra)]
    return None


def _people(boxscore, names):
    people = {}
    officials = []
    game = ((boxscore or {}).get("game") or boxscore or {}) if isinstance(boxscore, dict) else {}
    for official in game.get("officials") or []:
        officials.append({
            "personId": official.get("personId"),
            "number": str(official.get("jerseyNum") or ""),
            "name": official.get("name") or official.get("nameI") or "",
        })
    for side in ("awayTeam", "homeTeam"):
        team = game.get(side) or {}
        tri = str(team.get("teamTricode") or "").upper()
        for player in team.get("players") or []:
            pid = player.get("personId")
            if pid is None:
                continue
            stats = player.get("statistics") or {}
            zh = names.get(int(pid)) if str(pid).isdigit() or isinstance(pid, int) else None
            try:
                zh = names.get(int(pid))
            except (TypeError, ValueError):
                zh = None
            people[pid] = {
                "personId": pid,
                "team": tri,
                "number": str(player.get("jerseyNum") or ""),
                "nameEn": (zh or {}).get("name_en") or f"{player.get('firstName') or ''} {player.get('familyName') or ''}".strip(),
                "nameZh": (zh or {}).get("name_zh") or "",
                "aliasZh": (zh or {}).get("alias_zh") or "",
                "blocks": stats.get("blocks"),
                "blocksReceived": stats.get("blocksReceived"),
            }
    return people, officials


def _display_name(person):
    if not person:
        return ""
    return person.get("aliasZh") or person.get("nameZh") or person.get("nameEn") or person.get("name") or ""


def _person_from_action(action, people, names):
    if not action:
        return None
    pid = action.get("personId")
    known = people.get(pid)
    if known:
        return dict(known)
    zh = None
    try:
        zh = names.get(int(pid)) if pid is not None else None
    except (TypeError, ValueError):
        zh = None
    return {
        "personId": pid,
        "team": action.get("team") or "",
        "number": str(action.get("number") or ""),
        "nameEn": (zh or {}).get("name_en") or action.get("name") or "",
        "nameZh": (zh or {}).get("name_zh") or "",
        "aliasZh": (zh or {}).get("alias_zh") or "",
        "blocks": None,
        "blocksReceived": None,
    }


def _identity_samples(identity):
    if not identity:
        return []
    samples = []

    def add(t, box, **meta):
        if t is None or not box or len(box) < 4:
            return
        samples.append({
            "t": float(t),
            "box": tuple(float(v) for v in box[:4]),
            "team": str(meta.get("team") or "").upper(),
            "number": str(meta.get("number") or ""),
            "name": meta.get("name") or "",
            "personId": meta.get("personId"),
            "referee": bool(meta.get("referee")),
            "track": meta.get("track"),
        })

    for row in identity.get("tags") or []:
        for player in row.get("players") or []:
            box = None
            if all(key in player for key in ("x", "y", "w", "h")):
                box = (player["x"], player["y"], player["w"], player["h"])
            elif player.get("box"):
                box = player["box"]
            elif player.get("bbox"):
                box = player["bbox"]
            add(
                row.get("t"), box, team=player.get("team"), number=player.get("jersey") or player.get("number"),
                name=player.get("name") or player.get("label"), personId=player.get("personId"),
                referee=player.get("referee"), track=player.get("track") or player.get("id"),
            )
    for vote in identity.get("votes") or []:
        for stamp, box in vote.get("boxes") or []:
            add(
                stamp, box, team=vote.get("team") or vote.get("color"), number=vote.get("number"),
                name=vote.get("nameZh") or vote.get("rosterName") or vote.get("label"),
                personId=vote.get("personId"), referee=vote.get("referee"), track=vote.get("id"),
            )
    players = {row.get("track_id"): row for row in identity.get("players") or []}
    for row in identity.get("timeline") or []:
        for player in row.get("players") or []:
            meta = players.get(player.get("track_id"), {})
            role = player.get("role") or meta.get("role")
            add(
                row.get("t"), player.get("bbox") or player.get("box"),
                team=player.get("team") or meta.get("team"),
                number=player.get("jersey") or meta.get("jersey") or meta.get("number"),
                name=player.get("name") or meta.get("name"),
                personId=player.get("personId") or meta.get("personId"),
                referee=role == "referee" or player.get("referee") or meta.get("referee"),
                track=player.get("track_id"),
            )
    for track in identity.get("tracks") or []:
        add(
            track.get("t"), track.get("box") or track.get("bbox"),
            team=track.get("team"), number=track.get("number") or track.get("jersey"),
            name=track.get("name"), personId=track.get("personId"),
            referee=track.get("referee"), track=track.get("id") or track.get("track"),
        )
    return samples


def _foot(box):
    x, y, w, h = box
    return (x + w / 2.0, y + h)


def _same_player(sample, person):
    if not sample or not person:
        return False
    if sample.get("personId") is not None and person.get("personId") is not None:
        return sample["personId"] == person["personId"]
    number = str(person.get("number") or "")
    team = str(person.get("team") or "").upper()
    if number and sample.get("number") == number and team and sample.get("team") == team:
        return True
    return False


def _is_referee(sample, officials):
    if sample.get("referee"):
        return True
    if sample.get("personId") is not None and any(sample["personId"] == off.get("personId") for off in officials):
        return True
    number = sample.get("number") or ""
    if number and not sample.get("team") and any(number == off.get("number") and off.get("number") for off in officials):
        return True
    name = (sample.get("name") or "").lower()
    if name and any(name == (off.get("name") or "").lower() and name for off in officials):
        return True
    return False


def resolve_defender(identity, t, shooter, official, officials):
    """出手时离投篮人最近的对方球员。和官方盖帽人/犯规人不一致时，卡片只写防守人。"""
    official_view = {
        "display": _display_name(official) or "防守人",
        "named": bool(_display_name(official)),
        "reason": None if _display_name(official) else "no_official_name",
        "official": official,
        "identity": None,
        "agrees": None,
        "number": (official or {}).get("number") or "",
        "team": (official or {}).get("team") or "",
        "personId": (official or {}).get("personId"),
        "nameEn": (official or {}).get("nameEn") or "",
        "nameZh": (official or {}).get("nameZh") or "",
        "aliasZh": (official or {}).get("aliasZh") or "",
    }
    samples = _identity_samples(identity)
    if t is None or not samples:
        return official_view
    near = [sample for sample in samples if abs(sample["t"] - t) <= 0.8]
    collapsed = {}
    for sample in near:
        key = sample.get("track") or (sample.get("team"), sample.get("number"), sample.get("name"), sample.get("referee"))
        current = collapsed.get(key)
        if current is None or abs(sample["t"] - t) < abs(current["t"] - t):
            collapsed[key] = sample
    shooter_box = None
    for sample in collapsed.values():
        if _is_referee(sample, officials):
            continue
        if _same_player(sample, shooter):
            shooter_box = sample["box"]
            break
    if shooter_box is None:
        return official_view
    sx, sy = _foot(shooter_box)
    candidates = []
    for sample in collapsed.values():
        if _is_referee(sample, officials) or _same_player(sample, shooter):
            continue
        if shooter and shooter.get("team") and sample.get("team") == shooter.get("team"):
            continue
        if not sample.get("team"):
            continue
        fx, fy = _foot(sample["box"])
        candidates.append((((fx - sx) ** 2 + (fy - sy) ** 2), sample))
    if not candidates:
        return official_view
    _, nearest = min(candidates, key=lambda item: item[0])
    agrees = _same_player(nearest, official)
    view = dict(official_view)
    view["identity"] = {
        "team": nearest.get("team") or "",
        "number": nearest.get("number") or "",
        "name": nearest.get("name") or "",
        "personId": nearest.get("personId"),
    }
    view["agrees"] = agrees
    if agrees:
        view["named"] = bool(view["display"] and view["display"] != "防守人")
        view["reason"] = None
        return view
    view["display"] = "防守人"
    view["named"] = False
    view["reason"] = "identity_mismatch"
    view["number"] = ""
    return view


def _quality(shotquality, shot):
    best = None
    best_gap = 99
    for row in _shots(shotquality):
        if shot.get("personId") is not None and row.get("playerId") not in (shot["personId"], str(shot["personId"])):
            continue
        period = period_num(row.get("period"))
        if period is not None and shot.get("period") is not None and period != shot["period"]:
            continue
        clock = clock_seconds(row.get("gameClock"))
        if clock is None or shot.get("clock") is None:
            continue
        gap = abs(clock - shot["clock"])
        if gap <= 2 and gap < best_gap:
            best, best_gap = row, gap
    return best


def _matchup(matchups, shooter_id, defender_id):
    if not matchups or shooter_id is None or defender_id is None:
        return None
    root = matchups.get("boxScoreMatchups") or matchups
    for side in ("homeTeam", "awayTeam"):
        for player in (root.get(side) or {}).get("players") or []:
            if player.get("personId") != shooter_id:
                continue
            for row in player.get("matchups") or []:
                if row.get("personId") == defender_id:
                    stats = dict(row.get("statistics") or {})
                    stats["defenderJersey"] = str(row.get("jerseyNum") or "")
                    return stats
    return None


def _stat(key, value, display, source, line_zh, line_en, **extra):
    row = {
        "key": key,
        "value": value,
        "display": display,
        "source": source,
        "lineZh": line_zh,
        "lineEn": line_en,
    }
    row.update(extra)
    return row


def _block_rate(advanced):
    if not advanced:
        return None, None
    for key in ("BLK_PCT", "PCT_BLK", "BLKPCT"):
        if advanced.get(key) is not None:
            return _num(advanced.get(key)), f"leaguedashadvanced.{key}"
    return None, None


def _defense_line(row, band):
    if not row:
        return None
    attempts = _num(row.get("opponent_fga"))
    rate = _ratio(row.get("opponent_fg_pct"), attempts)
    if rate is None:
        return None
    makes = _intish(row.get("opponent_fgm"))
    attempts_i = _intish(attempts)
    normal = _num(row.get("opponents_normal_fg_pct_official"))
    diff = _num(row.get("diff_from_opponents_normal_official"))
    if diff is None and normal is not None:
        diff = rate - normal
    label = BAND_ZH.get(band, band)
    label_en = BAND_EN.get(band, band)
    count = f"{makes}/{attempts_i}" if makes is not None and attempts_i is not None else ""
    count_zh = f"（{count}）" if count else ""
    count_en = f" ({count})" if count else ""
    zh = f"{label}对手命中率 {pct_text(rate)}{count_zh}"
    en = f"opponents shoot {pct_text(rate)} {label_en}{count_en}"
    if normal is not None and diff is not None:
        points = diff * 100
        if abs(points) < 0.05:
            zh += f"，与正常 {pct_text(normal)} 持平"
            en += f", level with the normal {pct_text(normal)}"
        else:
            word = "低" if points < 0 else "高"
            word_en = "below" if points < 0 else "above"
            zh += f"，比正常 {pct_text(normal)} {word} {abs(points):.1f} 个百分点"
            en += f", {abs(points):.1f} percentage points {word_en} the normal {pct_text(normal)}"
    return _stat(
        "defense_fg",
        {"opponentFgPct": rate, "normalFgPct": normal, "diff": diff, "band": band, "fgm": makes, "fga": attempts_i},
        pct_text(rate),
        "player_defensive_shooting.csv opponent_fg_pct",
        zh,
        en,
        unit="ratio",
    )


def _close_band(distance, three):
    if three:
        return "three_point"
    if distance is None:
        return None
    if distance < 6:
        return "near_six_ft"
    if distance <= 10:
        return "near_ten_ft"
    return None


def _matchup_line(stats):
    if not stats:
        return None
    fga = _num(stats.get("matchupFieldGoalsAttempted"))
    fgm = _num(stats.get("matchupFieldGoalsMade"))
    tpa = _num(stats.get("matchupThreePointersAttempted"))
    tpm = _num(stats.get("matchupThreePointersMade"))
    points = _num(stats.get("playerPoints"))
    fouls = _num(stats.get("shootingFouls"))
    blocks = _num(stats.get("matchupBlocks"))
    minutes = stats.get("matchupMinutes") if _num(stats.get("matchupMinutesSort")) else None
    parts_zh = []
    parts_en = []
    shown = {}
    if minutes:
        parts_zh.append(str(minutes))
        parts_en.append(str(minutes))
        shown["minutes"] = minutes
    if fga and fga > 0 and fgm is not None:
        fg_pct = _ratio(stats.get("matchupFieldGoalsPercentage"), fga)
        piece = f"{_intish(fgm)}/{_intish(fga)}"
        piece_en = piece
        if fg_pct is not None:
            piece += f"（{pct_text(fg_pct)}）"
            piece_en += f" ({pct_text(fg_pct)})"
        parts_zh.append(piece)
        parts_en.append(piece_en)
        shown["fgm"] = _intish(fgm)
        shown["fga"] = _intish(fga)
        if fg_pct is not None:
            shown["fgPct"] = fg_pct
    if tpa and tpa > 0 and tpm is not None:
        fg3 = _ratio(stats.get("matchupThreePointersPercentage"), tpa)
        piece = f"三分 {_intish(tpm)}/{_intish(tpa)}"
        piece_en = f"three {_intish(tpm)}/{_intish(tpa)}"
        if fg3 is not None:
            piece += f"（{pct_text(fg3)}）"
            piece_en += f" ({pct_text(fg3)})"
        parts_zh.append(piece)
        parts_en.append(piece_en)
        shown["fg3m"] = _intish(tpm)
        shown["fg3a"] = _intish(tpa)
        if fg3 is not None:
            shown["fg3Pct"] = fg3
    if points is not None and ((fga or 0) > 0 or points > 0):
        parts_zh.append(f"得 {_intish(points)} 分")
        parts_en.append(f"{_intish(points)} points")
        shown["points"] = _intish(points)
    if fouls and fouls > 0:
        parts_zh.append(f"投篮犯规 {_intish(fouls)} 次")
        parts_en.append(f"{_intish(fouls)} shooting foul" + ("" if _intish(fouls) == 1 else "s"))
        shown["shootingFouls"] = _intish(fouls)
    if blocks and blocks > 0:
        parts_zh.append(f"盖帽 {_intish(blocks)} 次")
        parts_en.append(f"{_intish(blocks)} block" + ("" if _intish(blocks) == 1 else "s"))
        shown["blocks"] = _intish(blocks)
    if not parts_zh:
        return None
    return _stat(
        "matchup",
        shown,
        "，".join(parts_zh),
        "boxscorematchupsv3",
        "本场对位 " + "，".join(parts_zh),
        "matchup " + ", ".join(parts_en),
    )


def _season_shooting(stats_row, three_row, zone_row, area):
    rows = []
    fga = _num((three_row or {}).get("three_point_fga"))
    fgm = _num((three_row or {}).get("three_point_fgm"))
    rate = _ratio((three_row or {}).get("three_point_fg_pct"), fga)
    source = "player_shooting_three_bands.csv three_point_fg_pct"
    if rate is None and stats_row:
        fga = _num(stats_row.get("FG3A"))
        fgm = _num(stats_row.get("FG3M"))
        rate = _ratio(stats_row.get("FG3_PCT"), fga)
        source = "leaguedashplayerstats.FG3_PCT"
    if rate is not None:
        count = f"{_intish(fgm)}/{_intish(fga)}" if fgm is not None and fga is not None else ""
        count_zh = f"（{count}）" if count else ""
        count_en = f" ({count})" if count else ""
        rows.append(_stat(
            "fg3_pct", rate, pct_text(rate), source,
            f"本赛季三分 {pct_text(rate)}{count_zh}",
            f"season three-point percentage {pct_text(rate)}{count_en}",
            unit="ratio", fgm=_intish(fgm), fga=_intish(fga),
        ))
    zfga = _num((zone_row or {}).get("fga"))
    zrate = _ratio((zone_row or {}).get("fg_pct"), zfga)
    if zrate is not None:
        zfgm = _intish(zone_row.get("fgm"))
        zone_name = ZONE_ZH.get(area, area or "同区")
        zone_en = area or "that zone"
        rows.append(_stat(
            "zone_fg_pct", zrate, pct_text(zrate), "player_shot_zones.csv fg_pct",
            f"同区{zone_name} {pct_text(zrate)}（{zfgm}/{_intish(zfga)}）",
            f"same zone {zone_en} {pct_text(zrate)} ({zfgm}/{_intish(zfga)})",
            unit="ratio", zone=area,
        ))
    return rows


def _blocked_lines(shot_person, dash_row, pbp_received):
    game = None if not shot_person else shot_person.get("blocksReceived")
    if game is None:
        game = pbp_received
    season = _num((dash_row or {}).get("BLKA"))
    parts_zh = []
    parts_en = []
    value = {}
    if game is not None:
        parts_zh.append(f"本场被盖 {_intish(game)} 次")
        parts_en.append(f"blocked {_intish(game)} time" + ("" if _intish(game) == 1 else "s") + " tonight")
        value["game"] = _intish(game)
    if season is not None:
        parts_zh.append(f"本赛季被盖 {_intish(season)} 次")
        parts_en.append(f"{_intish(season)} times this season")
        value["season"] = _intish(season)
    if not parts_zh:
        return None
    source = "boxscore.statistics.blocksReceived" if shot_person and shot_person.get("blocksReceived") is not None else "playbyplay.blockPersonId"
    if season is not None:
        source += " + leaguedashplayerstats.BLKA"
    return _stat("blocks_against", value, "，".join(parts_zh), source, "，".join(parts_zh), ", ".join(parts_en))


def _blocker_lines(dash):
    base = (dash or {}).get("base") or {}
    blocks = _num(base.get("BLK"))
    games = _num(base.get("GP"))
    if blocks is None:
        return []
    per = blocks / games if games else None
    zh = f"本赛季盖帽 {_intish(blocks)} 次"
    en = f"{_intish(blocks)} blocks this season"
    if per is not None:
        zh += f"，场均 {per:.1f}"
        en += f", {per:.1f} per game"
    rows = [_stat(
        "season_blocks", _intish(blocks), zh.replace("本赛季盖帽 ", ""),
        "leaguedashplayerstats.BLK", zh, en, perGame=None if per is None else round(per, 3), gp=_intish(games),
    )]
    rate, source = _block_rate((dash or {}).get("advanced"))
    if rate is not None and 0 <= rate <= 1.5:
        shown = rate / 100 if rate > 1.5 else rate
        if rate <= 1:
            rows.append(_stat(
                "block_pct", rate, pct_text(rate), source,
                f"盖帽率 {pct_text(rate)}", f"block rate {pct_text(rate)}", unit="ratio",
            ))
        else:
            rows.append(_stat(
                "block_pct", shown, pct_text(shown), source,
                f"盖帽率 {pct_text(shown)}", f"block rate {pct_text(shown)}", unit="ratio",
            ))
    return rows


def _rank_line(pid, ranks, picks, label_zh, label_en, key):
    if pid is None:
        return None
    try:
        row = (ranks or {}).get(int(pid))
    except (TypeError, ValueError):
        return None
    if not row:
        return None
    for name, title, ratio in picks:
        item = row.get(name)
        if not item or not item.get("stars") or item["stars"] <= 0:
            continue
        shown = pct_text(item["value"]) if ratio else f"{item['value']:.1f}"
        if not shown:
            continue
        zh = f"队内{label_zh} {title} {shown} 第 {item['rank']}/{item['of']}"
        en = f"team {label_en}, {title} {shown}, {item['rank']} of {item['of']}"
        return _stat(
            key, dict(item), f"第 {item['rank']}/{item['of']}",
            f"leaguedash.{name}", zh, en, stars=item["stars"],
        )
    return None


def _panels(title, shooter, defender, kind, stats):
    offense = [{"style": "title", "text": title, "stars": None}]
    defense = []
    if shooter:
        number = f" #{shooter['number']}" if shooter.get("number") else ""
        offense.append({"style": "off", "text": f"投篮 {_display_name(shooter)}{number}", "stars": None})
    label = DEF_LABEL.get(kind, "防守")
    number = f" #{defender['number']}" if defender and defender.get("named") and defender.get("number") else ""
    who = (defender or {}).get("display") or "防守人"
    defense.append({"style": "def", "text": f"{label} {who}{number}", "stars": None})
    for stat in stats:
        row = {"style": "rank" if stat.get("stars") else "line", "text": stat["lineZh"], "stars": stat.get("stars")}
        if stat["key"] in DEF_KEYS:
            defense.append(row)
        else:
            offense.append(row)
    return {"offense": offense, "defense": defense}


def build_card(item, context):
    shot = item.get("shot") or {}
    kind = item["type"]
    offense = _person_from_action(shot or item.get("steal"), context["people"], context["names"])
    if kind == "block":
        defense_action = item.get("block") or {}
    elif kind in {"and1_3", "and1_2"}:
        defense_action = item.get("foul") or {}
    elif kind == "steal":
        defense_action = item.get("steal") or {}
        offense = None
    else:
        defense_action = {}
    defense = _person_from_action(defense_action, context["people"], context["names"])
    if defense and not defense.get("number"):
        jersey = (item.get("matchup") or {}).get("defenderJersey")
        if jersey:
            defense["number"] = jersey
    view = resolve_defender(context["identity"], item.get("key"), offense, defense, context["officials"])
    quality = _quality(context["shotquality"], shot) if shot else None
    xfg = _num((quality or {}).get("shotQuality"))
    distance = shot.get("distance") if shot else None
    area = shot.get("area") if shot else ""
    team = (offense or {}).get("team") or ""
    def_team = (defense or {}).get("team") or ""
    season = context["season"]
    dash = context["dash"]
    stats = []
    if kind != "steal":
        if xfg is not None:
            stats.append(_stat(
                "xfg", xfg, pct_text(xfg), "shotqualityshotlog.shotQuality",
                f"这一投预期命中率 {pct_text(xfg)}", f"expected make {pct_text(xfg)}", unit="ratio",
            ))
        if distance is not None:
            shown = feet_text(distance)
            stats.append(_stat(
                "distance", distance, f"{shown} 英尺", "playbyplay.shotDistance",
                f"距离 {shown} 英尺", f"{shown} feet",
            ))
    if kind == "block" and defense and defense.get("personId") is not None:
        stats.extend(_blocker_lines(dash.get(int(defense["personId"])) if str(defense["personId"]).lstrip("-").isdigit() else None))
        band = _close_band(distance, False) or "near_ten_ft"
        row = _prefer(season["bands"], int(defense["personId"]), def_team, band) if defense.get("personId") is not None else None
        line = _defense_line(row, band)
        if line:
            stats.append(line)
        received = context["received"].get(offense.get("personId") if offense else None)
        against = _blocked_lines(
            offense,
            ((dash.get(int(offense["personId"])) or {}).get("base") if offense and offense.get("personId") is not None else None),
            received,
        )
        if against:
            stats.append(against)
    if kind in {"and1_3", "and1_2"} and offense and offense.get("personId") is not None:
        pid = int(offense["personId"])
        base = (dash.get(pid) or {}).get("base")
        three = _prefer(season["threes"], pid, team)
        zone = _prefer(season["zones"], pid, team, area) if area else None
        stats.extend(_season_shooting(base, three, zone, area))
        band = "three_point" if kind == "and1_3" else _close_band(distance, False)
        if band and defense and defense.get("personId") is not None:
            row = _prefer(season["bands"], int(defense["personId"]), def_team, band)
            line = _defense_line(row, band)
            if line:
                stats.append(line)
        matched = _matchup(context["matchups"], offense.get("personId"), defense.get("personId") if defense else None)
        line = _matchup_line(matched)
        if line:
            stats.append(line)
    if kind == "steal" and defense and defense.get("personId") is not None:
        base = (dash.get(int(defense["personId"])) or {}).get("base") or {}
        steals = _num(base.get("STL"))
        games = _num(base.get("GP"))
        if steals is not None and (steals > 0 or games):
            per = steals / games if games else None
            zh = f"本赛季抢断 {_intish(steals)} 次" + (f"，场均 {per:.1f}" if per is not None else "")
            en = f"{_intish(steals)} steals this season" + (f", {per:.1f} per game" if per is not None else "")
            stats.append(_stat("season_steals", _intish(steals), zh, "leaguedashplayerstats.STL", zh, en))
    if kind == "dunk" and offense and offense.get("personId") is not None:
        pid = int(offense["personId"])
        base = (dash.get(pid) or {}).get("base")
        zone = _prefer(season["zones"], pid, team, area) if area else None
        # 扣篮卡只补同区命中率，避免空的三分行。
        for row in _season_shooting(None, None, zone, area):
            stats.append(row)
    off_rank = _rank_line(
        (offense or {}).get("personId"), context.get("ranks"), OFF_PICK, "进攻", "offense", "off_rank",
    )
    def_rank = _rank_line(
        (defense or {}).get("personId"), context.get("ranks"), DEF_PICK, "防守", "defense", "def_rank",
    )
    if off_rank:
        stats.insert(0, off_rank)
    if def_rank:
        stats.append(def_rank)
    title = TITLES[kind]
    return {
        "type": kind,
        "title": title,
        "shooter": offense,
        "defender": view,
        "stats": stats,
        "panels": _panels(title, offense, view, kind, stats),
        "xfg": xfg,
        "actionNumbers": [
            number for number in (
                (shot or {}).get("actionNumber"),
                (item.get("block") or {}).get("actionNumber") if isinstance(item.get("block"), dict) else None,
                (item.get("foul") or {}).get("actionNumber") if isinstance(item.get("foul"), dict) else None,
                (item.get("steal") or {}).get("actionNumber") if isinstance(item.get("steal"), dict) else None,
            ) if number is not None
        ],
    }


def _card_lines(card):
    shooter = card.get("shooter") or {}
    defender = card.get("defender") or {}
    lines = [card["title"]]
    if shooter:
        number = f" #{shooter['number']}" if shooter.get("number") else ""
        lines.append(f"投篮 {_display_name(shooter)}{number}")
    label = DEF_LABEL.get(card["type"], "防守")
    number = f" #{defender['number']}" if defender.get("named") and defender.get("number") else ""
    lines.append(f"{label} {defender.get('display') or '防守人'}{number}")
    lines.extend(stat["lineZh"] for stat in card["stats"])
    return lines


def _commentary(card):
    shooter = _display_name(card.get("shooter")) or "投篮人"
    shooter_en = (card.get("shooter") or {}).get("nameEn") or shooter
    defender = (card.get("defender") or {}).get("display") or "防守人"
    if defender == "防守人":
        defender_en = "the defender"
    else:
        defender_en = (card.get("defender") or {}).get("nameEn") or defender
    by_key = {stat["key"]: stat for stat in card["stats"]}
    head = [by_key[key] for key in ("xfg", "distance") if key in by_key]
    rest = [stat for stat in card["stats"] if stat["key"] not in {"xfg", "distance"}]
    kind = card["type"]
    if kind == "block":
        zh = f"{shooter}的投篮被{defender}盖下"
        en = f"{shooter_en}'s shot is blocked by {defender_en}"
    elif kind == "and1_3":
        zh = f"{shooter}三分打进，并造成{defender}犯规"
        en = f"{shooter_en} makes the three and draws the foul on {defender_en}"
    elif kind == "and1_2":
        zh = f"{shooter}打进，并造成{defender}犯规"
        en = f"{shooter_en} scores and draws the foul on {defender_en}"
    elif kind == "dunk":
        zh = f"{shooter}扣篮得手"
        en = f"{shooter_en} dunks it"
    else:
        zh = f"{defender}抢断"
        en = f"{defender_en} steals it"
    if head:
        zh += "，" + "，".join(stat["lineZh"] for stat in head)
        en += ", " + ", ".join(stat["lineEn"] for stat in head)
    zh += "。"
    en += "."
    sentences_zh = [zh]
    sentences_en = [en]
    if rest:
        sentences_zh.append("；".join(stat["lineZh"] for stat in rest) + "。")
        sentences_en.append("; ".join(stat["lineEn"] for stat in rest) + ".")
    return {"zh": sentences_zh[:2], "en": sentences_en[:2]}


def _public_person(person, view=None):
    if view is not None:
        official = view.get("official") or {}
        named = bool(view.get("named"))
        return {
            "display": view.get("display") or "防守人",
            "named": named,
            "reason": view.get("reason"),
            "agrees": view.get("agrees"),
            "personId": official.get("personId"),
            "team": official.get("team") or "",
            # 认人和官方对不上时，对外只留「防守人」，不把另一套名字写进给网页用的字段。
            "number": (view.get("number") or "") if named else "",
            "nameEn": (official.get("nameEn") or "") if named else "",
            "nameZh": (official.get("nameZh") or "") if named else "",
            "aliasZh": (official.get("aliasZh") or "") if named else "",
            "identity": view.get("identity"),
        }
    if not person:
        return None
    return {
        "display": _display_name(person),
        "named": bool(_display_name(person)),
        "personId": person.get("personId"),
        "team": person.get("team") or "",
        "number": person.get("number") or "",
        "nameEn": person.get("nameEn") or "",
        "nameZh": person.get("nameZh") or "",
        "aliasZh": person.get("aliasZh") or "",
    }


def _segment_plan(source, min_replay_s=MIN_REPLAY_S):
    """每段正好 min_replay_s：出手前 PRE_ROLL_S 慢放铺满前面，球离手那一帧定格 FREEZE_S。
    源片段多取关键帧后一点点，编码时按总长截掉，避免定格后面接一段空片段。"""
    key = source["key"]
    start = max(0.0, key - PRE_ROLL_S)
    pre = max(0.2, key - start)
    freeze = FREEZE_S
    speed = pre / (min_replay_s - freeze)
    return {
        "srcStart": round(start, 3),
        "srcEnd": round(key + 0.1, 3),
        "key": round(key, 3),
        "pre": round(pre, 3),
        "speed": round(speed, 4),
        "freeze": freeze,
        "duration": round(min_replay_s, 3),
    }


def _received_counts(actions):
    counts = Counter()
    for action in actions:
        if action["blocked"] and action.get("personId") is not None:
            counts[action["personId"]] += 1
        elif action["kind"] == "block" and action.get("personId") is not None:
            continue
    return counts


def _layout_hint(context, t, card, action):
    samples = _identity_samples(context.get("identity"))
    boxes, shooter_box, defender_box = [], None, None
    if t is not None and samples:
        near = [sample for sample in samples if abs(sample["t"] - t) <= 0.8]
        if near:
            stamp = min(near, key=lambda sample: abs(sample["t"] - t))["t"]
            group = [sample for sample in near if abs(sample["t"] - stamp) <= 0.05]
            boxes = [[float(value) for value in sample["box"]] for sample in group]
            shooter = card.get("shooter")
            officials = context.get("officials") or []
            for sample in group:
                if not _is_referee(sample, officials) and _same_player(sample, shooter):
                    shooter_box = [float(value) for value in sample["box"]]
                    break
            if shooter_box:
                sx, sy = _foot(tuple(shooter_box))
                best = None
                for sample in group:
                    if _is_referee(sample, officials) or _same_player(sample, shooter):
                        continue
                    if shooter and shooter.get("team") and sample.get("team") == shooter.get("team"):
                        continue
                    if not sample.get("team"):
                        continue
                    fx, fy = _foot(sample["box"])
                    dist = (fx - sx) ** 2 + (fy - sy) ** 2
                    if best is None or dist < best[0]:
                        best = (dist, [float(value) for value in sample["box"]])
                if best:
                    defender_box = best[1]
    side = str((action or {}).get("side") or "").lower()
    return {
        "shotSide": side if side in {"left", "right"} else "",
        "boxes": boxes,
        "shooterBox": shooter_box,
        "defenderBox": defender_box,
    }


def build_highlights(
    board,
    playbyplay,
    *,
    shotquality=None,
    matchups=None,
    boxscore=None,
    roster_stats=None,
    identity=None,
    season_dir=None,
    names=None,
    duration=None,
    min_replay_s=MIN_REPLAY_S,
):
    """只做识别和卡片，不调 ffmpeg。``duration`` 是原片秒数。"""
    season_path = Path(season_dir) if season_dir else SEASON
    name_map, _teams = load_names(names)
    people, officials = _people(load_doc(boxscore), name_map)
    for action_official in _actions(load_doc(playbyplay) if not isinstance(playbyplay, (dict, list)) else playbyplay):
        if action_official.get("officialId") and not any(off["personId"] == action_official["officialId"] for off in officials):
            officials.append({"personId": action_official["officialId"], "number": "", "name": ""})
    actions, found = detect_actions(load_doc(playbyplay) if not isinstance(playbyplay, (dict, list)) else playbyplay)
    if duration is None:
        times = [sample[0] for sample in ClockModel(board or {}, 0).samples]
        duration = max(times) if times else 0
    model = ClockModel(board or {}, duration)
    anchors = _anchors(actions, model)
    context = {
        "people": people,
        "names": name_map,
        "officials": officials,
        "identity": load_doc(identity) if not isinstance(identity, dict) else identity,
        "shotquality": load_doc(shotquality) if not isinstance(shotquality, dict) else shotquality,
        "matchups": load_doc(matchups) if not isinstance(matchups, dict) else matchups,
        "season": load_season(season_path),
        "dash": load_dash(roster_stats, season_path),
        "ranks": load_team_ranks(roster_stats, season_path),
        "received": _received_counts(actions),
    }
    rows = []
    for item in found:
        anchor_action = item.get("shot") or item.get("steal")
        raw, on_whistle = _place(anchor_action, model, anchors)
        mapped = model.map_clock((anchor_action or {}).get("clock"))
        if not _in_clip(raw, duration):
            continue
        source = _window(raw, on_whistle, mapped, duration)
        if not source:
            continue
        item["key"] = source["key"]
        item["matchup"] = None
        card = build_card(item, context)
        commentary = _commentary(card)
        plan = _segment_plan(source, min_replay_s)
        rows.append({
            "type": card["type"],
            "title": card["title"],
            "period": (anchor_action or {}).get("period"),
            "clock": format_clock((anchor_action or {}).get("clock")),
            "actionNumbers": card["actionNumbers"],
            "onWhistle": on_whistle,
            "sourceSeconds": {"start": source["start"], "end": source["end"], "key": source["key"]},
            "segment": plan,
            "people": {
                "shooter": _public_person(card.get("shooter")),
                "defender": _public_person(None, card.get("defender")),
            },
            "card": {
                "title": card["title"],
                "lines": _card_lines(card),
                "stats": card["stats"],
                "panels": card.get("panels") or {},
            },
            "layout": _layout_hint(context, source["key"], card, anchor_action),
            "commentary": commentary,
            "_xfg": card.get("xfg"),
        })
    rows.sort(key=lambda row: row["sourceSeconds"]["key"])
    cursor = float(duration or 0)
    events = []
    for row in rows:
        length = row["segment"]["duration"]
        row["replay"] = {"start": round(cursor, 3), "end": round(cursor + length, 3)}
        event = {
            "t": row["sourceSeconds"]["key"],
            "timecode": row["clock"],
            "label": row["title"],
            "eventType": "highlight",
            "highlight": row["type"],
            "detail": "".join(row["commentary"]["zh"]),
            "replay": dict(row["replay"]),
            "shot": {"xfg": row["_xfg"]} if row.get("_xfg") is not None else None,
        }
        events.append(event)
        cursor += length
        row.pop("_xfg", None)
    return {
        "version": "0.1",
        "duration": duration,
        "timing": model.as_dict(),
        "audio": "replay-muted",
        "highlights": rows,
        "events": events,
    }


def _font(size):
    from PIL import ImageFont

    path = font_path()
    if path:
        return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _wrap(draw, text, font, width):
    if draw.textlength(text, font=font) <= width:
        return [text]
    pieces = re.split(r"(?<=[，；、])", text) or [text]
    lines, buf = [], ""
    for piece in pieces:
        if buf and draw.textlength(buf + piece, font=font) <= width:
            buf += piece
            continue
        if buf:
            lines.append(buf)
            buf = ""
        if draw.textlength(piece, font=font) <= width:
            buf = piece
            continue
        chunk = ""
        for char in piece:
            if draw.textlength(chunk + char, font=font) <= width:
                chunk += char
            else:
                if chunk:
                    lines.append(chunk)
                chunk = char
        buf = chunk
    if buf:
        lines.append(buf)
    return lines or [text]


def _star_points(cx, cy, radius):
    import math

    points = []
    for step in range(10):
        angle = -math.pi / 2 + step * math.pi / 5
        arm = radius if step % 2 == 0 else radius * 0.45
        points.append((cx + arm * math.cos(angle), cy + arm * math.sin(angle)))
    return points


def _paint_stars(draw, x, y, size, rating):
    from PIL import Image, ImageDraw

    for index in range(5):
        cx = x + index * (size + 2) + size / 2
        cy = y + size / 2
        radius = size / 2
        draw.polygon(_star_points(cx, cy, radius), fill=(88, 80, 70, 255))
        fill = min(max(float(rating) - index, 0.0), 1.0)
        if fill <= 0:
            continue
        width = int(size) + 2
        star = Image.new("L", (width, width), 0)
        ImageDraw.Draw(star).polygon(_star_points(width / 2, width / 2, radius), fill=255)
        if fill < 1:
            star.paste(0, (int(width * fill), 0, width, width))
        draw._image.paste(Image.new("RGBA", (width, width), GOLD), (int(cx - width / 2), int(cy - width / 2)), star)


def _render_panel(rows, width, edge):
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (width, 8), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    head = _font(22)
    body = _font(18)
    title = _font(28)
    wrapped = []
    for row in rows or []:
        style = row.get("style") or "line"
        font = title if style == "title" else head if style in {"off", "def"} else body
        for piece in _wrap(draw, row.get("text") or "", font, width - 36):
            wrapped.append((style if piece == row.get("text") else "line", piece, font, row.get("stars") if piece == row.get("text") else None))
    if not wrapped:
        return image
    heights = []
    for style, _text, font, stars in wrapped:
        line_h = draw.textbbox((0, 0), "字", font=font)[3] + (10 if style in {"title", "off", "def"} else 6)
        if stars:
            line_h += 18
        heights.append(line_h)
    height = sum(heights) + 22
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width, height), fill=PANEL)
    draw.rectangle((0, 0, 5, height), fill=edge)
    y = 10
    for (style, text, font, stars), line_h in zip(wrapped, heights):
        color = RED if style == "title" else CREAM if style in {"off", "def"} else MUTED
        x = 16
        if style in {"off", "def"}:
            draw.ellipse((x, y + 6, x + 10, y + 16), fill=RED if style == "off" else GOLD)
            x += 16
        draw.text((x, y), text, font=font, fill=color)
        if stars:
            _paint_stars(draw, x, y + draw.textbbox((0, 0), "字", font=font)[3] + 2, 12, stars)
        y += line_h
    return image


def _fit_boxes(boxes, width, height):
    fitted = []
    raw = []
    for box in boxes or []:
        if not box or len(box) < 4:
            continue
        raw.append(tuple(float(value) for value in box[:4]))
    if not raw:
        return []
    max_x = max(box[0] + box[2] for box in raw)
    max_y = max(box[1] + box[3] for box in raw)
    if max_x <= 1.5 and max_y <= 1.5:
        scale_x, scale_y = width, height
    elif max_x > width * 1.05 or max_y > height * 1.05:
        scale_x = width / max_x
        scale_y = height / max_y
    else:
        scale_x = scale_y = 1
    for x, y, w, h in raw:
        fitted.append((x * scale_x, y * scale_y, w * scale_x, h * scale_y))
    return fitted


def _broadcast_bugs(width, height):
    return [
        (width * 0.70, height * 0.78, width * 0.30, height * 0.22),
        (width * 0.84, 0, width * 0.16, height * 0.18),
    ]


def _card_sides(frame, highlight):
    from card_layout import basket_side

    basket = basket_side(frame) if frame is not None else None
    layout = highlight.get("layout") or {}
    if basket is None and layout.get("shotSide") in {"left", "right"}:
        basket = layout["shotSide"]
    shooter, defender = layout.get("shooterBox"), layout.get("defenderBox")
    if basket is None and shooter and defender and len(shooter) >= 3 and len(defender) >= 3:
        shooter_x = shooter[0] + shooter[2] / 2
        defender_x = defender[0] + defender[2] / 2
        basket = "right" if defender_x >= shooter_x else "left"
    layout["basket"] = basket
    highlight["layout"] = layout
    if basket == "left":
        return "right", "left"
    return "left", "right"


def draw_overlay(highlight, frame=None):
    """1280×720 透明图层。进攻卡远离篮筐，防守卡靠近篮筐，都放在空地上。"""
    from PIL import Image

    from card_layout import place_cards

    image = Image.new("RGBA", (OUT_W, OUT_H), (0, 0, 0, 0))
    panels = (highlight.get("card") or {}).get("panels") or {}
    offense = _render_panel(panels.get("offense") or [], 360, RED)
    defense = _render_panel(panels.get("defense") or [], 360, GOLD)
    if offense.height <= 8 and defense.height <= 8:
        return image
    analysis = None
    if frame is not None:
        shot = frame if isinstance(frame, Image.Image) else Image.open(frame)
        analysis = shot.convert("RGB")
        if analysis.size != (OUT_W, OUT_H):
            analysis = analysis.resize((OUT_W, OUT_H))
    offense_side, defense_side = _card_sides(analysis, highlight)
    boxes = _fit_boxes((highlight.get("layout") or {}).get("boxes"), OUT_W, OUT_H)
    canvas = analysis if analysis is not None else Image.new("RGB", (OUT_W, OUT_H), (24, 28, 32))
    cards = []
    panels_drawn = []
    if offense.height > 8:
        cards.append({"id": "offense", "w": offense.width, "h": offense.height})
        panels_drawn.append(offense)
    if defense.height > 8:
        cards.append({"id": "defense", "w": defense.width, "h": defense.height})
        panels_drawn.append(defense)
    positions = place_cards(
        canvas, boxes, cards,
        {"offense": offense_side, "defense": defense_side},
        scorebug_boxes=_broadcast_bugs(OUT_W, OUT_H),
    )
    highlight.setdefault("layout", {})["positions"] = positions
    for panel, pos in zip(panels_drawn, positions):
        if pos["w"] != panel.width or pos["h"] != panel.height:
            panel = panel.resize((max(1, pos["w"]), max(1, pos["h"])))
        x = max(0, min(int(pos["x"]), OUT_W - panel.width))
        y = max(0, min(int(pos["y"]), OUT_H - panel.height))
        image.alpha_composite(panel, (x, y))
    return image


def burn_card(frame, highlight, dest):
    """把进攻卡和防守卡叠到一帧上，供截图验收。"""
    from PIL import Image

    base = Image.open(frame).convert("RGBA").resize((OUT_W, OUT_H))
    out = Image.alpha_composite(base, draw_overlay(highlight, base)).convert("RGB")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.save(dest)
    return dest


def _probe(video: Path):
    proc = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-show_entries", "stream=codec_type", "-of", "json", str(video),
        ],
        check=True, capture_output=True, text=True,
    )
    doc = json.loads(proc.stdout or "{}")
    duration = float((doc.get("format") or {}).get("duration") or 0)
    audio = any(stream.get("codec_type") == "audio" for stream in doc.get("streams") or [])
    return duration, audio


def _run(cmd):
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-1600:]
        raise RuntimeError(tail or f"ffmpeg 退出 {proc.returncode}")


def _encode_prefix(video: Path, dest: Path, preset: str):
    _duration, audio = _probe(video)
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(video),
    ]
    if not audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    cmd += [
        "-vf", f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=decrease,pad={OUT_W}:{OUT_H}:(ow-iw)/2:(oh-ih)/2,fps=30,format=yuv420p,setsar=1",
    ]
    if audio:
        cmd += ["-af", "aresample=48000,aformat=channel_layouts=stereo", "-shortest"]
    else:
        cmd += ["-map", "0:v:0", "-map", "1:a:0", "-shortest"]
    cmd += [
        "-c:v", "libx264", "-preset", preset, "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "48000", "-ac", "2", str(dest),
    ]
    _run(cmd)


def _encode_replay(video: Path, highlight: dict, overlay: Path, dest: Path, preset: str):
    plan = highlight["segment"]
    src = max(0.2, plan["srcEnd"] - plan["srcStart"])
    pre = plan["pre"]
    speed = plan["speed"]
    freeze = plan["freeze"]
    # 慢放整段，关键帧用 tpad 定格。回放没有现场声。
    graph = (
        f"[0:v]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=decrease,"
        f"pad={OUT_W}:{OUT_H}:(ow-iw)/2:(oh-ih)/2,fps=30,setsar=1,setpts=PTS-STARTPTS,split=3[a][b][c];"
        f"[a]trim=0:{pre:.3f},setpts=PTS/{speed:.4f}[pre];"
        f"[b]trim={pre:.3f}:{pre + 0.08:.3f},setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration={freeze:.3f}[fr];"
        f"[c]trim={pre:.3f}:{src:.3f},setpts=(PTS-STARTPTS)/{speed:.4f}[post];"
        f"[pre][fr][post]concat=n=3:v=1:a=0[cat];"
        f"[1:v]format=rgba[card];"
        f"[cat][card]overlay=0:0:format=auto[out]"
    )
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{plan['srcStart']:.3f}", "-t", f"{src:.3f}", "-i", str(video),
        "-loop", "1", "-i", str(overlay),
        "-f", "lavfi", "-t", f"{plan['duration'] + 1:.3f}", "-i", "anullsrc=r=48000:cl=stereo",
        "-filter_complex", graph,
        "-map", "[out]", "-map", "2:a:0",
        "-t", f"{max(plan['duration'], MIN_REPLAY_S):.3f}",
        "-c:v", "libx264", "-preset", preset, "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "48000", "-ac", "2", str(dest),
    ]
    try:
        _run(cmd)
    except RuntimeError:
        # 定格滤镜失败时退回整段慢放，长度仍然至少 10 秒。
        fallback = (
            f"[0:v]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=decrease,"
            f"pad={OUT_W}:{OUT_H}:(ow-iw)/2:(oh-ih)/2,fps=30,setsar=1,setpts=PTS-STARTPTS,"
            f"setpts=PTS/{speed:.4f}[cat];"
            f"[1:v]format=rgba[card];[cat][card]overlay=0:0:format=auto[out]"
        )
        cmd[cmd.index(graph)] = fallback
        _run(cmd)


def _concat(parts, dest, preset):
    """用 concat 滤镜重新编码成恒定 30 帧：每段的时间戳各自归零，不受容器里编辑列表和起点偏移影响。"""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for path in parts:
        cmd += ["-i", str(path)]
    inputs = "".join(f"[{index}:v:0][{index}:a:0]" for index in range(len(parts)))
    graph = f"{inputs}concat=n={len(parts)}:v=1:a=1[cv][ca];[cv]fps=30,format=yuv420p[v]"
    _run(cmd + [
        "-filter_complex", graph, "-map", "[v]", "-map", "[ca]",
        "-c:v", "libx264", "-preset", preset, "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(dest),
    ])


def render_highlights(
    video,
    board,
    playbyplay,
    *,
    out_video,
    out_json=None,
    work_dir=None,
    preset="veryfast",
    duration=None,
    min_replay_s=MIN_REPLAY_S,
    **kwargs,
):
    """原片后面按时间接上每段至少 10 秒的慢放回放，并写出 JSON。"""
    video = Path(video)
    probed, _audio = _probe(video)
    doc = build_highlights(
        board, playbyplay, duration=probed if duration is None else duration,
        min_replay_s=min_replay_s, **kwargs,
    )
    work = Path(work_dir) if work_dir else Path(out_video).parent / (Path(out_video).stem + "-work")
    work.mkdir(parents=True, exist_ok=True)
    prefix = work / "prefix.mp4"
    _encode_prefix(video, prefix, preset)
    parts = [prefix]
    cursor = _probe(prefix)[0]
    for index, highlight in enumerate(doc["highlights"]):
        # 源视频比计划时长短时，把窗口收进片内。
        segment = highlight["segment"]
        segment["srcStart"] = min(segment["srcStart"], max(0.0, probed - 0.5))
        segment["srcEnd"] = min(max(segment["srcEnd"], segment["srcStart"] + 0.5), probed)
        overlay = work / f"card-{index}.png"
        still = work / f"still-{index}.jpg"
        try:
            _run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{max(0.0, highlight['sourceSeconds']['key']):.3f}",
                "-i", str(video), "-frames:v", "1", str(still),
            ])
            from PIL import Image
            shot = Image.open(still)
        except (RuntimeError, OSError):
            shot = None
        draw_overlay(highlight, shot).save(overlay)
        piece = work / f"replay-{index}.mp4"
        _encode_replay(video, highlight, overlay, piece, preset)
        length = _probe(piece)[0]
        highlight["replay"] = {"start": round(cursor, 3), "end": round(cursor + length, 3)}
        highlight["segment"]["renderedDuration"] = round(length, 3)
        if index < len(doc["events"]):
            doc["events"][index]["replay"] = dict(highlight["replay"])
        cursor += length
        parts.append(piece)
    out = Path(out_video)
    out.parent.mkdir(parents=True, exist_ok=True)
    _concat(parts, out, preset)
    doc["outputVideo"] = str(out)
    doc["outputDuration"] = round(_probe(out)[0], 3)
    doc["prefixDuration"] = round(_probe(prefix)[0], 3)
    if out_json:
        Path(out_json).write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return doc


def main(argv=None):
    parser = argparse.ArgumentParser(description="给成片接上进球后的精彩回放")
    parser.add_argument("--video", required=True)
    parser.add_argument("--board", required=True, help="记分牌 JSON，含 reads")
    parser.add_argument("--playbyplay", required=True)
    parser.add_argument("--shotquality")
    parser.add_argument("--matchups")
    parser.add_argument("--boxscore")
    parser.add_argument("--identity")
    parser.add_argument("--roster-stats", nargs="*", dest="roster_stats")
    parser.add_argument("--season-dir")
    parser.add_argument("--names")
    parser.add_argument("--out-video", required=True)
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--work-dir")
    args = parser.parse_args(argv)
    doc = render_highlights(
        args.video,
        load_doc(args.board),
        args.playbyplay,
        shotquality=args.shotquality,
        matchups=args.matchups,
        boxscore=args.boxscore,
        roster_stats=args.roster_stats,
        identity=args.identity,
        season_dir=args.season_dir,
        names=args.names,
        out_video=args.out_video,
        out_json=args.out_json,
        work_dir=args.work_dir,
    )
    print(json.dumps({
        "highlights": len(doc["highlights"]),
        "output": doc.get("outputVideo"),
        "types": [item["type"] for item in doc["highlights"]],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
