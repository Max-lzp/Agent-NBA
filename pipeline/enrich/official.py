"""官方数据读取：逐回合（stats playbyplayv3 / CDN liveData）、boxscore、投篮质量日志 → 统一的字典。

只依赖标准库。文件按 gameId 放在 data/ 下任意层级：{gameId}_{接口名}.json，靠内容判断类型。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

DATA = Path(os.environ.get("DEEPCOURT_DATA") or Path(__file__).resolve().parents[3] / "data")
# 左右一律按投篮人面向篮筐时的左右（NBA 官方区域名 Left Corner 3 的惯例）。
# 官方坐标 x 为负对应哪一侧：数据里有左右区域名时自动校准，没有时用这个默认值。
# 2025-26 四场 259 次带左右区域名的投篮全部是「x 为负 = 左」，默认值就按这个。
X_NEGATIVE_SIDE = os.environ.get("DEEPCOURT_X_NEGATIVE", "left")
CLOCK_ISO = re.compile(r"PT(?:(\d+)M)?([\d.]+)S")
ASSIST = re.compile(r"\(([^()]+?) \d+ AST\)")


def clock_seconds(text) -> Optional[float]:
    """'PT07M25.00S' / '7:25' / '25.3' → 剩余秒数。"""
    if text is None:
        return None
    s = str(text).strip()
    m = CLOCK_ISO.fullmatch(s)
    if m:
        return int(m.group(1) or 0) * 60 + float(m.group(2))
    if ":" in s:
        mm, ss = s.split(":", 1)
        return int(mm) * 60 + float(ss)
    try:
        return float(s)
    except ValueError:
        return None


def period_number(text) -> Optional[int]:
    if text is None:
        return None
    if isinstance(text, int):
        return text
    s = str(text).upper()
    if s.startswith("OT"):
        return 4 + int(s[2:] or 1)
    m = re.match(r"(\d)", s)
    return int(m.group(1)) if m else None


def _int(v) -> Optional[int]:
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _kind(a: dict) -> str:
    t = str(a.get("actionType", "")).lower()
    if str(a.get("isFieldGoal", "")) in {"1", "True", "true"} or t in {"2pt", "3pt", "made shot", "missed shot"}:
        return "shot"
    for key, kind in (("free", "freethrow"), ("rebound", "rebound"), ("turnover", "turnover"),
                      ("foul", "foul"), ("substitution", "substitution"), ("timeout", "timeout")):
        if key in t:
            return kind
    return "other"


def normalize_play(a: dict) -> dict:
    kind = _kind(a)
    desc = str(a.get("description") or "")
    value = _int(a.get("shotValue"))
    if kind == "shot" and not value:
        value = 3 if (str(a.get("actionType")).lower() == "3pt" or "3PT" in desc) else 2
    made = None
    if kind in {"shot", "freethrow"}:
        result = str(a.get("shotResult") or "")
        made = result.lower() == "made" if result else ("MISS" not in desc.upper())
    assist_name = a.get("assistPlayerNameInitial")
    if not assist_name:
        m = ASSIST.search(desc)
        assist_name = m.group(1) if m else None
    x, y = a.get("xLegacy"), a.get("yLegacy")
    area = " ".join(str(a.get(k) or "") for k in ("area", "areaDetail", "shotZoneArea", "shotZoneBasic")).lower()
    # 投篮人左右只看区域名；CDN 的 side 是电视画面里的左 / 右半场（随 x < 50 走），另存 court_half
    side = "left" if "left" in area else "right" if "right" in area else ""
    court_half = str(a.get("side") or "").lower() if a.get("x") is not None else ""
    return {
        "action_number": _int(a.get("actionNumber")),
        "period": _int(a.get("period")),
        "clock_s": clock_seconds(a.get("clock")),
        "team": a.get("teamTricode") or "",
        "person_id": _int(a.get("personId")),
        "name": a.get("playerName") or a.get("familyName") or "",
        "kind": kind,
        "sub_type": a.get("subType") or "",
        "made": made,
        "value": value if kind == "shot" else (1 if kind == "freethrow" else None),
        "distance_ft": a.get("shotDistance"),
        "x_ft": round(x / 10, 1) if isinstance(x, (int, float)) else None,
        "y_ft": round(y / 10, 1) if isinstance(y, (int, float)) else None,
        "side": side,
        "court_half": court_half if court_half in {"left", "right"} else "",
        "assist_id": _int(a.get("assistPersonId")),
        "assist_name": assist_name,
        "score_home": _int(a.get("scoreHome")),
        "score_away": _int(a.get("scoreAway")),
        "description": desc,
    }


def _teams_with_players(doc) -> Optional[tuple[dict, dict]]:
    """boxscoresummaryv3 / boxscoretraditionalv3 / CDN boxscore 都有 homeTeam / awayTeam.players。"""
    if isinstance(doc, dict):
        home, away = doc.get("homeTeam"), doc.get("awayTeam")
        if isinstance(home, dict) and isinstance(away, dict) and "players" in home:
            return home, away
        for v in doc.values():
            hit = _teams_with_players(v)
            if hit:
                return hit
    return None


def players_from_box(doc) -> dict[int, dict]:
    hit = _teams_with_players(doc)
    if not hit:
        return {}
    out = {}
    for team in hit:
        tri = team.get("teamTricode", "")
        for p in team.get("players") or []:
            pid = _int(p.get("personId"))
            if pid is None:
                continue
            stats = p.get("statistics") or {}
            minutes = str(stats.get("minutes") or stats.get("minutesCalculated") or "")
            out[pid] = {
                "team": tri,
                "jersey": str(p.get("jerseyNum") or "").strip(),
                "name": p.get("familyName") or p.get("lastName") or str(p.get("name") or "").split(" ")[-1],
                "full_name": p.get("name") or " ".join(x for x in (p.get("firstName"), p.get("familyName")) if x),
                "position": str(p.get("position") or "").strip(),
                "played": (str(p.get("played", "")) == "1") or minutes not in {"", "PT00M00.00S", "0:00", "00:00"},
            }
    return out


def load_game(game_id: str, data_dir: Path = DATA) -> dict:
    """{plays, players, shots, files, away, home}；缺哪类就是空。"""
    found = {"plays": [], "players": {}, "shots": [], "files": [], "away": None, "home": None}
    for path in sorted(data_dir.rglob(f"{game_id}_*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        game = doc.get("game") if isinstance(doc, dict) else None
        if isinstance(game, dict) and isinstance(game.get("homeTeam"), dict) and not found["home"]:
            found["away"] = (game.get("awayTeam") or {}).get("teamTricode")
            found["home"] = game["homeTeam"].get("teamTricode")
        if isinstance(game, dict) and isinstance(game.get("actions"), list) and not found["plays"]:
            found["plays"] = [normalize_play(a) for a in game["actions"]]
            found["files"].append(path.name)
        if isinstance(doc, dict) and isinstance(doc.get("shots"), list) and doc["shots"] and not found["shots"]:
            found["shots"] = doc["shots"]
            found["files"].append(path.name)
        players = players_from_box(doc)
        if players and len(players) > len(found["players"]):
            found["players"] = players
            found["files"].append(path.name)
    _fill_roster_positions(found["players"], data_dir)
    return found


def position_zh(code: str | None) -> str:
    """G / F / C，或 SF、F-C 这种写法，收成前锋、后卫、中锋。"""
    names = {"G": "后卫", "F": "前锋", "C": "中锋", "PG": "后卫", "SG": "后卫", "SF": "前锋", "PF": "前锋"}
    raw = str(code or "").strip().upper()
    if not raw:
        return ""
    if raw in names:
        return names[raw]
    parts = [names.get(part) for part in raw.split("-")]
    if parts and all(parts):
        return "/".join(dict.fromkeys(parts))
    return ""


def _fill_roster_positions(players: dict[int, dict], data_dir: Path) -> None:
    """boxscore 只给首发写位置。全队名单里的 POSITION 覆盖它，替补才有位置。"""
    if not players:
        return
    for path in sorted(data_dir.rglob("commonteamroster_*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for rs in doc.get("resultSets") or []:
            headers = rs.get("headers") or []
            if "PLAYER_ID" not in headers or "POSITION" not in headers:
                continue
            pid_i, pos_i = headers.index("PLAYER_ID"), headers.index("POSITION")
            for row in rs.get("rowSet") or []:
                pid = _int(row[pid_i])
                code = str(row[pos_i] or "").strip()
                if pid in players and code:
                    players[pid]["position"] = code


def game_roster(players: dict[int, dict]) -> dict:
    """boxscore → roster.json 的 games 条目（active / dnp，号码 → 短名）。"""
    active: dict[str, dict] = {}
    dnp: dict[str, dict] = {}
    for p in players.values():
        if not p["jersey"] or not p["team"]:
            continue
        (active if p["played"] else dnp).setdefault(p["team"], {})[p["jersey"]] = p["name"]
    return {"active": active, "dnp": dnp, "source": "official boxscore"}


def calibrate_side(plays: list[dict]) -> tuple[str, str]:
    """返回（x 为负对应的一侧, 依据）。依据：official_calibrated = 数据里既有坐标又有左右标签，按多数定；
    assumed = 没有可用标签，用 X_NEGATIVE_SIDE。"""
    votes = {"left": 0, "right": 0}
    for p in plays:
        x = p.get("x_ft")
        if p.get("side") in votes and x not in (None, 0) and abs(x) >= 3:
            votes[p["side"] if x < 0 else ("left" if p["side"] == "right" else "right")] += 1
    if votes["left"] + votes["right"] >= 3 and votes["left"] != votes["right"]:
        return max(votes, key=votes.get), "official_calibrated"
    return X_NEGATIVE_SIDE, "assumed"


def on_court(game: dict, period: int, clock_s: float) -> dict[str, list[int]]:
    """（节次, 比赛钟）这一刻双方场上的人（personId）。逐回合不记每节首发：本节第一次出现不是被换上的，
    就算开节在场；再按比赛钟依次换人。认不齐 5 人的队照样返回已知的。"""
    players = game["players"]
    plays = sorted((p for p in game["plays"] if p["period"] == period), key=lambda p: p["action_number"] or 0)
    starters: dict[str, set] = {}
    seen: set[int] = set()
    for p in plays:
        for pid in (p["person_id"], p.get("assist_id")):
            if pid not in players or pid in seen:
                continue
            seen.add(pid)
            if not (p["kind"] == "substitution" and p["sub_type"] == "in" and pid == p["person_id"]):
                starters.setdefault(players[pid]["team"], set()).add(pid)
    court = {team: set(ids) for team, ids in starters.items()}
    for p in plays:
        if p["kind"] != "substitution" or p["clock_s"] is None or p["clock_s"] <= clock_s or p["person_id"] not in players:
            continue
        team = players[p["person_id"]]["team"]
        (court.setdefault(team, set()).add if p["sub_type"] == "in" else court.setdefault(team, set()).discard)(p["person_id"])
    return {team: sorted(ids) for team, ids in court.items()}

