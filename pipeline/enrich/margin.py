"""2025-26 独行侠赛季分差进攻效率。

数据文件：data/2025-26/dal_margin_offense.json
新视频在 enrich 对上官方出手后，按出手前分差取对应一档。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

from .official import DATA

FILE = DATA / "2025-26" / "dal_margin_offense.json"
SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}


def bucket_of(margin: int) -> str:
    if margin <= -10:
        return "trail10"
    if -5 <= margin <= 5:
        return "close5"
    if margin >= 10:
        return "lead10"
    return "other"


def _last_name(name: str) -> str:
    parts = [p for p in name.replace(".", " ").split() if p]
    while parts and parts[-1].lower().rstrip(".") in SUFFIXES:
        parts.pop()
    return parts[-1] if parts else ""


@lru_cache(maxsize=1)
def load() -> dict:
    if not FILE.exists():
        return {}
    return json.loads(FILE.read_text(encoding="utf-8"))


def _aliases(doc: dict) -> dict[str, str]:
    """姓 → 全名。同姓多于一人时不建简称，避免对错人。"""
    owners: dict[str, list[str]] = {}
    for full in doc.get("players") or {}:
        last = _last_name(full).lower()
        if last:
            owners.setdefault(last, []).append(full)
    return {last: names[0] for last, names in owners.items() if len(names) == 1}


def find_player(doc: dict, *names: str) -> Optional[dict]:
    players = doc.get("players") or {}
    aliases = _aliases(doc)
    for raw in names:
        name = (raw or "").strip()
        if not name:
            continue
        if name in players:
            return players[name]
        hit = aliases.get(name.lower()) or aliases.get(_last_name(name).lower())
        if hit:
            return players[hit]
    return None


def pre_shot_margin(play: dict, away: Optional[str], home: Optional[str]) -> Optional[int]:
    """出手前，投篮球队得分减去对手得分。官方比分是这次出手之后的。"""
    if not away or not home:
        return None
    home_score, away_score = play.get("score_home"), play.get("score_away")
    if home_score is None or away_score is None:
        return None
    team = play.get("team") or ""
    if play.get("kind") == "shot" and play.get("made") and play.get("value"):
        if team == home:
            home_score -= play["value"]
        elif team == away:
            away_score -= play["value"]
    if team == home:
        return home_score - away_score
    if team == away:
        return away_score - home_score
    return None


def season_margin(play: dict, players: dict, away: Optional[str], home: Optional[str]) -> Optional[dict]:
    """独行侠出手贴上赛季这一档的效率。对不上人、没有比分、或文件不在时返回 None。"""
    if play.get("kind") != "shot":
        return None
    info = (players or {}).get(play.get("person_id") or -1) or {}
    team = info.get("team") or play.get("team") or ""
    doc = load()
    if team != doc.get("team"):
        return None
    margin = pre_shot_margin(play, away, home)
    if margin is None:
        return None
    row = find_player(doc, info.get("full_name") or "", info.get("name") or "", play.get("name") or "")
    if not row:
        return None
    key = bucket_of(margin)
    stats = row.get(key) or {}
    meta = (doc.get("buckets") or {}).get(key) or {}
    team_row = (doc.get("team_totals") or {}).get(key) or {}
    return {
        "season": doc.get("season"),
        "team": team,
        "margin": margin,
        "bucket": key,
        "bucket_zh": meta.get("zh") or key,
        "games": doc.get("games"),
        "fga": stats.get("fga"),
        "efg": stats.get("efg"),
        "ts": stats.get("ts"),
        "ppp": stats.get("ppp"),
        "team_ts": team_row.get("ts"),
        "team_ppp": team_row.get("ppp"),
    }
