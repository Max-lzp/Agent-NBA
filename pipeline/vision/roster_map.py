#!/usr/bin/env python3
"""号码 + 球队 → 名单短名。模型给的 name 只作对照，不作为最终身份。"""

from __future__ import annotations

import json
from pathlib import Path

VISION = Path(__file__).resolve().parent
ROSTER = json.loads((VISION / "roster.json").read_text(encoding="utf-8"))


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
