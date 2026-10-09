#!/usr/bin/env python3
"""号码 + 球队 → 名单短名。模型给的 name 只作对照，不作为最终身份。"""

from __future__ import annotations

import json
from pathlib import Path

VISION = Path(__file__).resolve().parent
ROSTER = json.loads((VISION / "roster.json").read_text(encoding="utf-8"))
ROSTERS = VISION / "rosters"
DEFAULT_SEASON = "2025-26"
_seasons: dict[str, dict] = {}


def season_roster(season: str = DEFAULT_SEASON) -> dict:
    if season not in _seasons:
        _seasons[season] = json.loads((ROSTERS / f"{season}.json").read_text(encoding="utf-8"))
    return _seasons[season]


def _season_rows(season: str, number: str, date: str | None) -> list[dict]:
    rows = []
    for team, block in season_roster(season)["teams"].items():
        for index, player in enumerate(block["players"]):
            if normalize_jersey(player["jersey"]) != number:
                continue
            if date and player.get("since") and date < player["since"]:
                continue
            if date and player.get("until") and date > player["until"]:
                continue
            rows.append({**player, "team": team, "_order": index})
    return rows


def _by_playing_time(row: dict) -> tuple:
    minutes = row.get("minutes")
    games = row.get("games")
    return (minutes if minutes is not None else -1, games if games is not None else -1, -row["_order"])


def identify(jersey, team: str | None = None, color: str | None = None,
             color_to_team: dict | None = None, game_id: str | None = None,
             season: str = DEFAULT_SEASON) -> dict:
    """号码 → 球员。队伍先看 team，再看球衣颜色。

    这场有出场名单时只在出场的人里挑；有 on_court（片段里在场的人，来自官方换人记录）时只在场上的人里挑。
    两队同号且队伍不明时按上场时间取人，confidence 降到 0.6。
    """
    number = normalize_jersey(jersey)
    game = ROSTER["games"].get(game_id) if game_id else None
    team = (team or "").upper() or (color_to_team or {}).get(color or "")
    base = {"name": None, "team": team or None, "jersey": number, "dnp": False, "confidence": 0.0}
    if not number:
        return {**base, "source": "missing_key"}

    rows = _season_rows(season, number, game.get("date") if game else None)
    if game:
        # 按号码比：官方 boxscore 的短名（Thompson）和赛季名单的叫法（Amen / Klay）不一定一样
        active = [r for r in rows if number in game["active"].get(r["team"], {})]
        if active:
            rows = active
        elif rows and all(number in game["dnp"].get(r["team"], {}) for r in rows):
            return {**base, "source": "dnp", "dnp": True}
        court = game.get("on_court")
        if court:
            rows = [r for r in rows if number in court.get(r["team"], [])]
            if not rows:
                return {**base, "source": "not_on_court"}
    if team:
        same_team = [r for r in rows if r["team"] == team]
        if not same_team and len(rows) == 1:
            # 颜色常被灯光带偏，号码只在另一队出现时信号码。
            only = rows[0]
            return {**base, "name": only["name"], "team": only["team"],
                    "source": "season_number_over_color", "confidence": 0.7}
        rows = same_team
    if not rows:
        return {**base, "source": "not_in_roster"}

    rows.sort(key=_by_playing_time, reverse=True)
    best = rows[0]
    if team:
        source, confidence = ("season_team", 0.9) if len(rows) == 1 else ("season_team_minutes", 0.75)
    else:
        source, confidence = ("season_unique", 0.85) if len(rows) == 1 else ("season_minutes", 0.6)
    found = {**base, "name": best["name"], "team": best["team"], "source": source, "confidence": confidence}
    if best.get("player_id"):
        found["player_id"] = best["player_id"]
    if len(rows) > 1:
        found["candidates"] = [f"{r['team']} {r['name']}" for r in rows]
    return found


def normalize_jersey(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in {"", "null", "none", "未知", "?"}:
        return None
    if text.isdigit():
        if text == "0":
            return "0"
        return str(int(text)) if text != "00" else "00"
    return text


def lookup_number(game_id: str, jersey, color: str | None = None, color_to_team: dict | None = None) -> dict:
    """号码能唯一对上出场名单时直接用。两队同号才看球衣颜色。"""
    game = ROSTER["games"].get(game_id)
    number = normalize_jersey(jersey)
    if game is None and number:
        return identify(number, color=color, color_to_team=color_to_team)
    if game is None or not number:
        return {"name": None, "source": "missing_key", "dnp": False, "jersey": number, "team": None}
    active_hits = []
    dnp_hits = []
    for team in ("DAL", "HOU"):
        active = game["active"].get(team, {})
        dnp = game["dnp"].get(team, {})
        if number in active:
            active_hits.append((team, active[number]))
        elif number in dnp:
            dnp_hits.append((team, dnp[number]))
    if len(active_hits) == 1:
        team, name = active_hits[0]
        return {"name": name, "source": "qwen_unique", "dnp": False, "jersey": number, "team": team}
    if len(active_hits) > 1:
        mapped = None
        if color in {"white", "dark"}:
            mapped = (color_to_team or {}).get(color)
        for team, name in active_hits:
            if team == mapped:
                return {"name": name, "source": "qwen_color", "dnp": False, "jersey": number, "team": team}
        return {
            "name": None,
            "source": "qwen_collision",
            "dnp": False,
            "jersey": number,
            "team": None,
            "candidates": [f"{team} {name}" for team, name in active_hits],
        }
    if len(dnp_hits) == 1:
        team, name = dnp_hits[0]
        return {"name": name, "source": "dnp", "dnp": True, "jersey": number, "team": team}
    return {"name": None, "source": "not_in_roster", "dnp": False, "jersey": number, "team": None}


def lookup(game_id: str, team: str | None, jersey) -> dict:
    game = ROSTER["games"].get(game_id)
    if game is None:
        return {"name": None, "source": "unknown_game", "dnp": False}
    team_code = (team or "").upper()
    number = normalize_jersey(jersey)
    if not number or team_code not in {"DAL", "HOU"}:
        return {"name": None, "source": "missing_key", "dnp": False, "jersey": number, "team": team_code or None}
    active = game["active"].get(team_code, {})
    dnp = game["dnp"].get(team_code, {})
    if number in active:
        return {"name": active[number], "source": "active", "dnp": False, "jersey": number, "team": team_code}
    if number in dnp:
        return {
            "name": dnp[number],
            "source": "dnp",
            "dnp": True,
            "jersey": number,
            "team": team_code,
        }
    return {"name": None, "source": "not_in_roster", "dnp": False, "jersey": number, "team": team_code}


def to_xywh(bbox) -> tuple[list | None, str]:
    """把模型常见的 [x1, y1, x2, y2] 收成任务书要求的 [x, y, w, h]。

    判定：后两个数都大于前两个数，且落在 1920x1080 内，宽不超过 450、高不超过 900。
    任务书示例 [812, 220, 96, 210] 的宽小于 x，不会被误转。
    """
    if bbox is None:
        return None, "null"
    if not isinstance(bbox, list) or len(bbox) != 4:
        return bbox, "unchanged"
    try:
        x1, y1, x2, y2 = [int(round(float(value))) for value in bbox]
    except (TypeError, ValueError):
        return bbox, "unchanged"
    width = x2 - x1
    height = y2 - y1
    if (
        width > 0
        and height > 0
        and width <= 450
        and height <= 900
        and x2 <= 1920
        and y2 <= 1080
        and x1 >= 0
        and y1 >= 0
    ):
        return [x1, y1, width, height], "xyxy_to_xywh"
    return [x1, y1, x2, y2], "as_given"


def confirm_document(game_id: str, doc: dict) -> dict:
    """用名单覆盖 players[].name，并把 bbox 收成 [x, y, w, h]。"""
    confirmed = json.loads(json.dumps(doc))
    notes = []
    for player in confirmed.get("players") or []:
        guessed = player.get("name")
        found = lookup(game_id, player.get("team"), player.get("jersey"))
        player["name_from_model"] = guessed
        player["name"] = found["name"]
        player["identity_source"] = found["source"]
        converted, how = to_xywh(player.get("bbox"))
        player["bbox_raw"] = player.get("bbox")
        player["bbox"] = converted
        player["bbox_format"] = how
        if found["dnp"]:
            notes.append(f"{found['team']} #{found['jersey']} 映射到 DNP 球员 {found['name']}")
        if guessed and found["name"] and guessed != found["name"]:
            notes.append(f"模型写 {guessed}，名单是 {found['team']} #{found['jersey']} {found['name']}")
    handler = confirmed.get("ball_handler")
    names = [p.get("name") for p in confirmed.get("players") or [] if p.get("name")]
    if handler and handler not in names:
        notes.append(f"ball_handler={handler} 不在确认后的球员名单里，置为 null")
        confirmed["ball_handler"] = None
    for event in confirmed.get("events") or []:
        actor = event.get("actor")
        if actor and actor not in names:
            notes.append(f"event.actor={actor} 无法对应到确认后的球员")
            event["actor"] = None
    confirmed["identity_notes"] = notes
    return confirmed
