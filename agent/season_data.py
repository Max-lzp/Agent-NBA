"""火箭、独行侠赛季攻防三区和资料。百分数在这里乘 100，空值保持 null。"""

from __future__ import annotations

import csv
import json
from pathlib import Path

SLICE_PATH = Path(__file__).with_name("season_hou_dal.json")
TEAMS = {"HOU", "DAL"}
OFFENSE_ZONES = ("near_six_ft", "other_two_point_ge_six_ft", "three_point")
DEFENSE_BANDS = ("near_six_ft", "other_two_point_ge_six_ft", "three_point")
_CACHE: dict | None = None


def blank(value) -> bool:
    return value is None or str(value).strip() == ""


def as_int(value):
    if blank(value):
        return None
    return int(float(value))


def as_float(value):
    if blank(value):
        return None
    return float(value)


def percent(value):
    """0–1 的比例改成 0–100，保留一位。空字符串不是 0。"""
    number = as_float(value)
    if number is None:
        return None
    return round(number * 100, 1)


def offense_zone(row: dict, prefix: str) -> dict:
    fga = as_int(row.get(f"{prefix}_fga"))
    fgm = as_int(row.get(f"{prefix}_fgm"))
    pct = percent(row.get(f"{prefix}_fg_pct"))
    if fga in (None, 0):
        pct = None
    return {"fgm": fgm, "fga": fga, "fg_pct": pct}


def defense_zone(row: dict) -> dict:
    fga = as_int(row.get("opponent_fga"))
    fgm = as_int(row.get("opponent_fgm"))
    pct = percent(row.get("opponent_fg_pct"))
    if fga in (None, 0):
        pct = None
    return {
        "opponent_fgm": fgm,
        "opponent_fga": fga,
        "opponent_fg_pct": pct,
        "normal_fg_pct": percent(row.get("opponents_normal_fg_pct_official")),
        "diff_pct_points": percent(row.get("diff_from_opponents_normal_official")),
        "tracking_games": as_int(row.get("tracking_games")),
        "derived": str(row.get("status") or "").startswith("derived"),
    }


def profile_of(row: dict) -> dict:
    status = str(row.get("wingspan_status") or "")
    official_span = status.startswith("official")
    return {
        "gp_for_team": as_int(row.get("gp_for_team")),
        "height_cm": as_float(row.get("height_cm_profile")),
        "height_ft_in": row.get("height_ft_in_profile") or None,
        "position": row.get("position_profile") or None,
        "draft_year": as_int(row.get("draft_year")),
        "draft_round": as_int(row.get("draft_round")),
        "draft_overall_pick": as_int(row.get("draft_overall_pick")),
        "draft_status": row.get("draft_status") or None,
        "wingspan_cm": as_float(row.get("wingspan_cm_official_combine")) if official_span else None,
        "wingspan_year": as_int(row.get("wingspan_measurement_year")) if official_span else None,
    }


def _rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def build_slice(csv_dir: Path) -> dict:
    """只留 HOU/DAL，以及解说会用到的列。用户主观字段不进这张表。"""
    players: dict[tuple[str, str], dict] = {}

    def slot(team: str, player_id: str, name: str) -> dict:
        key = (team, str(player_id))
        current = players.get(key)
        if current is None:
            current = {
                "team": team,
                "player_id": str(player_id),
                "player_name": name,
                "offense": {},
                "defense": {},
                "profile": None,
            }
            players[key] = current
        elif name:
            current["player_name"] = name
        return current

    offense_path = csv_dir / "player_shooting_distance_aligned.csv"
    for row in _rows(offense_path):
        team = (row.get("scope") or "").upper()
        if team not in TEAMS:
            continue
        player = slot(team, row["player_id"], row.get("player_name") or "")
        for zone in OFFENSE_ZONES:
            player["offense"][zone] = offense_zone(row, zone)

    defense_path = csv_dir / "priority_team_defensive_shooting.csv"
    for row in _rows(defense_path):
        team = (row.get("scope") or "").upper()
        band = row.get("band") or ""
        if team not in TEAMS or band not in DEFENSE_BANDS:
            continue
        player = slot(team, row["player_id"], row.get("player_name") or "")
        player["defense"][band] = defense_zone(row)

    profile_path = csv_dir / "priority_team_profiles_reviewed.csv"
    for row in _rows(profile_path):
        team = (row.get("scope") or "").upper()
        if team not in TEAMS:
            continue
        player = slot(team, row["player_id"], row.get("player_name") or "")
        player["profile"] = profile_of(row)

    ordered = sorted(players.values(), key=lambda item: (item["team"], item["player_name"], item["player_id"]))
    return {
        "season": "2025-26",
        "teams": ["DAL", "HOU"],
        "note": "百分数已经是 0–100。null 表示没有这项数据，不是 0。出手数为 0 时命中率是 null。",
        "players": ordered,
    }


def load_slice() -> dict:
    global _CACHE
    if _CACHE is None:
        _CACHE = json.loads(SLICE_PATH.read_text(encoding="utf-8"))
    return _CACHE


def _norm(name: str) -> str:
    return " ".join(name.lower().replace(".", " ").split())


def find_player(doc: dict, name: str, team: str = "") -> tuple[dict | None, list[dict]]:
    query = _norm(name)
    if len(query) < 2:
        return None, []
    team = team.upper()
    pool = [item for item in doc.get("players") or [] if not team or item.get("team") == team]
    exact = [item for item in pool if _norm(item["player_name"]) == query]
    if len(exact) == 1:
        return exact[0], []
    if len(exact) > 1:
        return None, exact
    last = [item for item in pool if _norm(item["player_name"]).split()[-1] == query]
    if len(last) == 1:
        return last[0], []
    if len(last) > 1:
        return None, last
    if len(query) >= 4:
        partial = [item for item in pool if query in _norm(item["player_name"])]
        if len(partial) == 1:
            return partial[0], []
        if len(partial) > 1:
            return None, partial
    return None, []


def write_slice(csv_dir: Path, dest: Path = SLICE_PATH) -> None:
    payload = build_slice(csv_dir)
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    write_slice(root / "data" / "season" / "2025-26")
    print(f"wrote {SLICE_PATH}")
