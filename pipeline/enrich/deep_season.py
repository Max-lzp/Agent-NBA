"""2025-26 赛季出手质量、引力、关键度。

优先读仓库里的 data/deep（队友提交的官方 CSV）。
没有时再读数据目录的 2025-26/deep 或 deep。
这是赛季汇总。对得上球员才贴到这一次出手旁边，不当成这一球的胜率变化。
"""
from __future__ import annotations

import csv
import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

from .official import DATA

REPO = Path(__file__).resolve().parents[2]
SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
TEAM_BY_ID = {1610612745: "HOU", 1610612742: "DAL"}


def _dirs() -> list[Path]:
    return [REPO / "data" / "deep", DATA / "2025-26" / "deep", DATA / "deep"]


def _find(name: str) -> Optional[Path]:
    for folder in _dirs():
        path = folder / name
        if path.exists():
            return path
    return None


def _last_name(name: str) -> str:
    parts = [part for part in (name or "").replace(".", " ").replace("'", " ").split() if part]
    while parts and parts[-1].lower().rstrip(".") in SUFFIXES:
        parts.pop()
    return parts[-1].lower() if parts else ""


def _jersey(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.lower() == "none":
        return ""
    if text == "00":
        return "00"
    if text.isdigit():
        return str(int(text))
    return text


def _num(row: dict, key: str) -> Optional[float]:
    raw = str(row.get(key) or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _pid(row: dict) -> Optional[int]:
    raw = str(row.get("PLAYERID") or "").strip()
    if not raw:
        return None
    try:
        return int(float(raw))
    except ValueError:
        return None


def _read_csv(path: Optional[Path]) -> list[dict]:
    if path is None:
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _display_name(row: dict) -> str:
    full = (row.get("PLAYERNAME") or "").strip()
    if full:
        return full
    return " ".join(part for part in ((row.get("FIRSTNAME") or "").strip(), (row.get("LASTNAME") or "").strip()) if part)


def resolve(by_id: dict, jersey_index: dict, *, player_id=None, team=None, number=None, name=None) -> Optional[dict]:
    """给出了球员 ID 却对不上表时，不再用号码猜别人。"""
    if player_id not in (None, ""):
        try:
            pid = int(player_id)
        except (TypeError, ValueError):
            return None
        return by_id.get(pid)
    team = (team or "").upper()
    jersey = _jersey(number)
    last = _last_name(name or "")
    candidates = []
    if team and jersey:
        candidates = [by_id[pid] for pid in jersey_index.get((team, jersey), []) if pid in by_id]
    if len(candidates) > 1 and last:
        narrowed = [row for row in candidates if _last_name(row["name"]) == last]
        candidates = narrowed if len(narrowed) == 1 else []
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        return None
    if team and last:
        named = [row for row in by_id.values() if row["team"] == team and _last_name(row["name"]) == last]
        if len(named) == 1:
            return named[0]
    return None


def _public(row: dict, baseline: Optional[float]) -> dict:
    out = {
        "player_id": row["player_id"],
        "name": row["name"],
        "team": row["team"],
        "source": "data/deep",
    }
    for key in ("tracked_fga", "xfg", "fg", "fg_above", "gravity", "leverage"):
        if row.get(key) is not None:
            out[key] = row[key]
    if baseline is not None and row.get("xfg") is not None:
        out["baseline_xfg"] = baseline
        out["quality_ppt"] = (float(row["xfg"]) - baseline) * 100
    return out


@lru_cache(maxsize=1)
def load() -> dict:
    difficulty = _read_csv(_find("shot_difficulty.csv"))
    gravity = _read_csv(_find("player_gravity.csv"))
    leverage = _read_csv(_find("player_leverage.csv"))
    if not (difficulty or gravity or leverage):
        return {}
    by_id: dict[int, dict] = {}

    def row_for(pid: int, sample: dict) -> dict:
        current = by_id.get(pid)
        if current:
            return current
        current = {
            "player_id": pid,
            "name": _display_name(sample),
            "team": (sample.get("TEAMABBREVIATION") or "").upper(),
            "tracked_fga": None,
            "xfg": None,
            "fg": None,
            "fg_above": None,
            "gravity": None,
            "leverage": None,
        }
        by_id[pid] = current
        return current

    weighted = 0.0
    weight = 0.0
    for sample in difficulty:
        pid = _pid(sample)
        if pid is None:
            continue
        current = row_for(pid, sample)
        current["name"] = _display_name(sample) or current["name"]
        fga = _num(sample, "FGA")
        current["tracked_fga"] = int(fga) if fga is not None and fga.is_integer() else fga
        current["xfg"] = _num(sample, "AVGSHOTQUALITY")
        current["fg"] = _num(sample, "FGPCT")
        current["fg_above"] = _num(sample, "FGPCTABOVEEXPECTED")
        if fga and current["xfg"] is not None:
            weighted += fga * current["xfg"]
            weight += fga
    for sample in gravity:
        pid = _pid(sample)
        if pid is None:
            continue
        row_for(pid, sample)["gravity"] = _num(sample, "AVGGRAVITYSCORE")
    for sample in leverage:
        pid = _pid(sample)
        if pid is None:
            continue
        row_for(pid, sample)["leverage"] = _num(sample, "FULL")
    return {
        "by_id": by_id,
        "jersey": _jersey_index(),
        "baseline_xfg": (weighted / weight) if weight else None,
        "difficulty_players": len(difficulty),
    }


def _jersey_index() -> dict[tuple[str, str], list[int]]:
    index: dict[tuple[str, str], list[int]] = {}
    folder = DATA / "2025-26" / "rosters"
    if not folder.exists():
        return index
    for path in sorted(folder.glob("commonteamroster_*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for block in doc.get("resultSets") or []:
            headers = block.get("headers") or []
            if "PLAYER_ID" not in headers or "NUM" not in headers:
                continue
            for raw in block.get("rowSet") or []:
                item = dict(zip(headers, raw))
                team = TEAM_BY_ID.get(item.get("TeamID")) or TEAM_BY_ID.get(item.get("TEAM_ID"))
                jersey = _jersey(item.get("NUM"))
                try:
                    pid = int(item.get("PLAYER_ID"))
                except (TypeError, ValueError):
                    continue
                if not team or not jersey:
                    continue
                index.setdefault((team, jersey), [])
                if pid not in index[(team, jersey)]:
                    index[(team, jersey)].append(pid)
    return index


def season_profile(*, player_id=None, team=None, number=None, name=None) -> Optional[dict]:
    doc = load()
    if not doc:
        return None
    row = resolve(
        doc["by_id"], doc["jersey"],
        player_id=player_id, team=team, number=number, name=name,
    )
    if not row:
        return None
    return _public(row, doc.get("baseline_xfg"))


def season_profile_for_play(play: dict, players: dict) -> Optional[dict]:
    if play.get("kind") != "shot":
        return None
    info = (players or {}).get(play.get("person_id") or -1) or {}
    return season_profile(
        player_id=play.get("person_id") or info.get("player_id"),
        team=info.get("team") or play.get("team"),
        number=info.get("jersey") or info.get("number") or play.get("number"),
        name=info.get("full_name") or info.get("name") or play.get("name"),
    )
