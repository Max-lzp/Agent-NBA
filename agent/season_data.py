"""火箭、独行侠赛季攻防三区和资料。百分数在这里乘 100，空值保持 null。"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

SLICE_PATH = Path(__file__).with_name("season_hou_dal.json")
NAMES_PATH = Path(__file__).resolve().parents[1] / "data" / "names" / "zh-names.json"
TEAMS = {"HOU", "DAL"}
# 口语译名。运行时 zip 只带这个文件，带不走 data/names/，所以表写在这里。
# 和 data/names/zh-names.json 的 players 对齐，测试会核对。
ROSTER = (
    ('HOU', 'Aaron Holiday', '阿隆·霍勒迪', '霍勒迪'),
    ('HOU', 'Alperen Sengun', '阿尔佩伦·申京', '申京'),
    ('HOU', 'Amen Thompson', '阿门·汤普森', '阿门'),
    ('HOU', 'Clint Capela', '克林特·卡佩拉', '卡佩拉'),
    ('HOU', 'Dorian Finney-Smith', '多利安·芬尼-史密斯', '芬尼-史密斯'),
    ('HOU', 'Isaiah Crawford', '以赛亚·克劳福德', '克劳福德'),
    ('HOU', 'JD Davison', 'JD.戴维森', 'JD.戴维森'),
    ('HOU', 'Jabari Smith Jr.', '贾巴里·史密斯二世', '小史密斯'),
    ('HOU', "Jae'Sean Tate", '杰肖恩·泰特', '泰特'),
    ('HOU', 'Jeff Green', '杰夫·格林', '格林'),
    ('HOU', 'Josh Okogie', '约什·奥科吉', '奥科吉'),
    ('HOU', 'Kevin Durant', '凯文·杜兰特', '杜兰特'),
    ('HOU', 'Reed Sheppard', '里德·谢泼德', '谢泼德'),
    ('HOU', 'Steven Adams', '斯蒂文·亚当斯', '亚当斯'),
    ('HOU', 'Tari Eason', '塔里·伊森', '伊森'),
    ('HOU', 'Tristen Newton', '特里斯滕·牛顿', '牛顿'),
    ('DAL', 'AJ Johnson', 'AJ.约翰逊', '约翰逊'),
    ('DAL', 'Anthony Davis', '安东尼·戴维斯', '戴维斯'),
    ('DAL', 'Brandon Williams', '布兰登·威廉姆斯', '威廉姆斯'),
    ('DAL', 'Caleb Martin', '凯莱布·马丁', '马丁'),
    ('DAL', 'Cooper Flagg', '库珀·弗拉格', '弗拉格'),
    ('DAL', "D'Angelo Russell", '德安吉洛·拉塞尔', '拉塞尔'),
    ('DAL', 'Daniel Gafford', '丹尼尔·加福德', '加福德'),
    ('DAL', 'Dereck Lively II', '德雷克·莱夫利二世', '小莱夫利'),
    ('DAL', 'Dwight Powell', '德怀特·鲍威尔', '鲍威尔'),
    ('DAL', 'Jaden Hardy', '杰登·哈迪', '哈迪'),
    ('DAL', 'Jeremiah Robinson-Earl', '杰里迈亚·罗宾逊-厄尔', '罗宾逊-厄尔'),
    ('DAL', 'John Poulakidas', '约翰·普拉基达斯', '普拉基达斯'),
    ('DAL', 'Khris Middleton', '克里斯·米德尔顿', '米德尔顿'),
    ('DAL', 'Klay Thompson', '克莱·汤普森', '汤普森'),
    ('DAL', 'Marvin Bagley III', '马文·巴格利三世', '巴格利三世'),
    ('DAL', 'Max Christie', '马克斯·克里斯蒂', '克里斯蒂'),
    ('DAL', 'Miles Kelly', '迈尔斯·凯利', '凯利'),
    ('DAL', 'Moussa Cisse', '穆萨·西塞', '西塞'),
    ('DAL', 'Naji Marshall', '纳吉·马绍尔', '马绍尔'),
    ('DAL', 'P.J. Washington', 'P.J.华盛顿', '华盛顿'),
    ('DAL', 'Ryan Nembhard', '瑞安·内姆哈德', '内姆哈德'),
    ('DAL', 'Tyler Smith', '泰勒·史密斯', '史密斯'),
    ('DAL', 'Tyus Jones', '泰鲁斯·琼斯', '琼斯'),
)
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
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


def team_zones(doc: dict, team: str) -> dict | None:
    """队内球员计数相加。防守是各人被记成防守人的对手出手之和，不是另一张球队表。"""
    team = (team or "").upper()
    if team not in TEAMS:
        return None
    players = [item for item in doc.get("players") or [] if item.get("team") == team]
    if not players:
        return None

    def pack(side: str, made_key: str, attempt_key: str) -> dict:
        zones = {}
        for zone in ("near_six_ft", "other_two_point_ge_six_ft", "three_point"):
            made = attempts = 0
            for player in players:
                block = (player.get(side) or {}).get(zone) or {}
                if block.get(made_key) is None or block.get(attempt_key) is None:
                    continue
                made += int(block[made_key])
                attempts += int(block[attempt_key])
            pct = round(100 * made / attempts, 1) if attempts else None
            zone_row = {"fgm": made, "fga": attempts, "fg_pct": pct}
            if side == "defense":
                zone_row["opponent_fgm"] = made
                zone_row["opponent_fga"] = attempts
                zone_row["opponent_fg_pct"] = pct
            zones[zone] = zone_row
        return zones

    return {
        "team": team,
        "players": len(players),
        "offense": pack("offense", "fgm", "fga"),
        "defense": pack("defense", "opponent_fgm", "opponent_fga"),
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


def roster() -> list[dict]:
    people = []
    for team, name_en, name_zh, alias_zh in ROSTER:
        people.append({
            "team": team,
            "name_en": name_en,
            "name_zh": name_zh,
            "alias_zh": alias_zh,
            "last": _last_token(name_en),
            "forms": _zh_forms(name_zh, alias_zh),
        })
    return people


def _en_tokens(name: str) -> list[str]:
    parts = re.findall(r"[A-Za-z]+(?:-[A-Za-z]+)*", name or "")
    while parts and parts[-1].lower().rstrip(".") in _SUFFIXES:
        parts.pop()
    return parts


def _last_token(name: str) -> str:
    parts = _en_tokens(name)
    return parts[-1].lower() if parts else ""


def _zh_forms(name_zh: str, alias_zh: str) -> tuple[str, ...]:
    forms = []
    for form in (alias_zh, name_zh):
        if form and form not in forms:
            forms.append(form)
    return tuple(forms)


def _near_zh(text: str, people: list[dict]) -> dict | None:
    near = []
    for person in people:
        alias = person["alias_zh"]
        if alias and len(text) == len(alias) and _hamming(text, alias) == 1:
            near.append(person)
    if len(near) == 1:
        return near[0]
    return None


def match_name(name: str) -> dict | None:
    """英文名、简称或差一个字的译名。Thompson、Smith 这种姓不唯一时不对。"""
    text = str(name or "").strip()
    if len(text) < 2:
        return None
    people = roster()
    folded = text.lower()
    exact = [
        person for person in people
        if person["name_en"].lower() == folded or folded in {form.lower() for form in person["forms"]}
    ]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return None
    tokens = _en_tokens(text)
    if tokens:
        last = tokens[-1].lower()
        by_last = [person for person in people if person["last"] == last]
        lowered = [tok.lower() for tok in tokens]
        if len(by_last) == 1:
            return by_last[0]
        if len(lowered) >= 2:
            hits = [
                person for person in by_last
                if [part.lower() for part in _en_tokens(person["name_en"])][-len(lowered):] == lowered
            ]
            if len(hits) == 1:
                return hits[0]
            if len(tokens[0]) == 1:
                initial = tokens[0][0].lower()
                initial_hits = [
                    person for person in by_last
                    if (parts := _en_tokens(person["name_en"])) and parts[0][0].lower() == initial
                ]
                if len(initial_hits) == 1:
                    return initial_hits[0]
    return _near_zh(text, people)


def _hamming(left: str, right: str) -> int:
    if len(left) != len(right):
        return 99
    return sum(a != b for a, b in zip(left, right))


def write_slice(csv_dir: Path, dest: Path = SLICE_PATH) -> None:
    payload = build_slice(csv_dir)
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    write_slice(root / "data" / "season" / "2025-26")
    print(f"wrote {SLICE_PATH}")
