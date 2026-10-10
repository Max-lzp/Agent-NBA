"""2025-26 赛季三区与资料，挂到出手上。只按 player_id 关联，不按名字。

只读下面三份 CSV 的这些列，其余列不读、也不写进 pack。
不用 priority_team_shooting.csv（近框是限制区，和防守的 6 英尺对不上）、
priority_team_efficiency.csv、player_profiles.csv、raw/、references/、
visualizations/、user-submissions/、visualization_data.json、
defensive_visualization_data.json。页面和成片都从这三份 CSV 来。

进攻 data/season/2025-26/player_shooting_distance_aligned.csv
  只保留 scope 为 HOU、DAL 的行。scope 只用来丢掉 NBA 的 582 行，并在
  球员同时有两队记录时对上这一球的球队；不写入 pack，也不读 source_key。
  读入列：player_id、player_name、
  near_six_ft_fgm / near_six_ft_fga / near_six_ft_fg_pct、
  other_two_point_ge_six_ft_fgm / other_two_point_ge_six_ft_fga / other_two_point_ge_six_ft_fg_pct、
  three_point_fgm / three_point_fga / three_point_fg_pct。
  player_name 只随白名单读入，不参与关联，也不写入 pack。

防守 data/season/2025-26/priority_team_defensive_shooting.csv
  band 只取 near_six_ft、other_two_point_ge_six_ft、three_point。
  不读 overall、near_ten_ft、two_point、greater_fifteen_ft，
  也不读 tracking_games、frequency_official、status、source_key。
  读入列：player_id、player_name、band、opponent_fgm、opponent_fga、opponent_fg_pct、
  opponents_normal_fg_pct_official、diff_from_opponents_normal_official。
  后两列写成「对手在他面前比平时低/高 X 个百分点」。
  other_two_point_ge_six_ft 由两分减去 6 英尺内推算，官方没有平时命中率，
  这一区不显示差值，只显示命中率。

资料 data/season/2025-26/priority_team_profiles_reviewed.csv
  读入列：player_id、player_name、scope、height_cm_profile、height_ft_in_profile、
  position_profile、draft_year、draft_round、draft_overall_pick、draft_status。
  臂展 wingspan_cm_official_combine 只在 wingspan_status 等于
  official_historical_measurement 时进入 pack。
  不读带 user 的列：jersey_number_user_unverified、
  position_five_way_user_interpretation、wingspan_cm_user_unverified、
  primary_role_user_subjective、secondary_role_user_subjective、
  idol_analogy_user_subjective。
  也不读 gp_for_team、team_label、team_count、各种 source_key、draft_eligibility。
  未选秀写「落选」。

百分比字段是 0–1，展示时乘 100。0 次出手或空值不显示，不能写成 0%。
这一投的区域用已有的出手距离和分区：6 英尺以内是近框，其余两分是两分里
6 英尺及以上，三分单独一档。展示顺序从远到近：三分 → 其余两分 → 近框。
"""
from __future__ import annotations

import csv
import json
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SEASON = REPO / "data" / "season" / "2025-26"
CLIPS = REPO / "web" / "replay" / "clips.js"

# 攻防对照的三档。近框是 6 英尺以内，不是限制区。
BANDS = (
    ("three", "三分", "three_point"),
    ("other_two", "其余两分", "other_two_point_ge_six_ft"),
    ("near", "近框", "near_six_ft"),
)
BAND_BY_KEY = {key: (label, column) for key, label, column in BANDS}
THREE_ZONES = {"top3", "corner3_l", "corner3_r", "wing3_l", "wing3_r"}
TWO_ZONES = {"restricted", "paint", "midrange"}
OFFICIAL_WINGSPAN = "official_historical_measurement"
TEAM_SCOPES = {"HOU", "DAL"}
DEFENSE_BANDS = {"near_six_ft", "other_two_point_ge_six_ft", "three_point"}

OFFENSE_COLUMNS = (
    "scope", "player_id", "player_name",
    "near_six_ft_fgm", "near_six_ft_fga", "near_six_ft_fg_pct",
    "other_two_point_ge_six_ft_fgm", "other_two_point_ge_six_ft_fga", "other_two_point_ge_six_ft_fg_pct",
    "three_point_fgm", "three_point_fga", "three_point_fg_pct",
)
DEFENSE_COLUMNS = (
    "player_id", "player_name", "band",
    "opponent_fgm", "opponent_fga", "opponent_fg_pct",
    "opponents_normal_fg_pct_official", "diff_from_opponents_normal_official",
)
PROFILE_COLUMNS = (
    "player_id", "player_name", "scope",
    "height_cm_profile", "height_ft_in_profile", "position_profile",
    "draft_year", "draft_round", "draft_overall_pick", "draft_status",
    "wingspan_cm_official_combine", "wingspan_status",
)
POSITION_ZH = {
    "Guard": "后卫",
    "Forward": "前锋",
    "Center": "中锋",
    "Guard-Forward": "后卫/前锋",
    "Forward-Guard": "前锋/后卫",
    "Forward-Center": "前锋/中锋",
    "Center-Forward": "中锋/前锋",
}


def _read_columns(path: Path, columns: tuple[str, ...]) -> list[dict]:
    """只留下白名单列。csv 解析出行后，其他单元格不进入结果。"""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        index = {name: pos for pos, name in enumerate(header)}
        missing = [name for name in columns if name not in index]
        if missing:
            raise RuntimeError(f"{path.name} 缺少列 {missing}")
        rows = []
        for raw in reader:
            if not raw or all(not cell.strip() for cell in raw):
                continue
            rows.append({
                name: raw[index[name]].strip() if index[name] < len(raw) else ""
                for name in columns
            })
        return rows


def _blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _float(value):
    if _blank(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _count(value):
    number = _float(value)
    if number is None:
        return None
    return int(number) if number.is_integer() else number


def _pid(value):
    number = _count(value)
    if isinstance(number, int) and number > 0:
        return number
    return None


def _positive_int(value):
    number = _count(value)
    if isinstance(number, int) and number > 0:
        return number
    return None


def _team(value) -> str:
    team = str(value or "").strip().upper()
    return team if team in TEAM_SCOPES else ""


def format_pct(value) -> str | None:
    """0–1 乘 100。空值返回 None，不写成 0%。"""
    number = _float(value)
    if number is None:
        return None
    return f"{number * 100:.1f}%"


def format_cm(value) -> str | None:
    number = _float(value)
    if number is None or number <= 0:
        return None
    text = f"{number:.2f}".rstrip("0").rstrip(".")
    return f"{text} cm"


def shown_pct(pct, fga) -> str | None:
    """没有出手数，或 0 次出手，都不显示命中率。"""
    attempts = _count(fga)
    if attempts is None or attempts <= 0:
        return None
    return format_pct(pct)


def count_text(fgm, fga) -> str | None:
    makes, attempts = _count(fgm), _count(fga)
    if makes is None or attempts is None or attempts <= 0:
        return None
    return f"{makes}/{attempts}"


def band_of(*, distance_ft=None, value=None, shot_zone=None) -> str | None:
    """这一投落在哪一档。距离优先于分区；6 英尺整归入其余两分。"""
    zone = shot_zone or ""
    points = _positive_int(value)
    if points == 3 or zone in THREE_ZONES:
        return "three"
    distance = _float(distance_ft)
    if distance is not None:
        if distance < 6:
            return "near"
        if points == 2 or zone in TWO_ZONES:
            return "other_two"
        return None
    if zone == "restricted":
        return "near"
    if zone == "midrange":
        return "other_two"
    return None


def _rates(fgm, fga, pct) -> dict:
    out = {}
    makes, attempts = _count(fgm), _count(fga)
    if makes is not None:
        out["fgm"] = makes
    if attempts is not None:
        out["fga"] = attempts
    shown = shown_pct(pct, attempts)
    if shown is not None:
        out["fg_pct"] = _float(pct)
    return out


def _match(index: dict, player_id: int | None, team: str = ""):
    if not player_id:
        return None
    row = index.get(player_id)
    if not row:
        return None
    scope = row.get("scope") or ""
    if team and scope and scope != team:
        return None
    return row


@lru_cache(maxsize=1)
def load():
    offense_rows = _read_columns(SEASON / "player_shooting_distance_aligned.csv", OFFENSE_COLUMNS)
    defense_rows = _read_columns(SEASON / "priority_team_defensive_shooting.csv", DEFENSE_COLUMNS)
    profile_rows = _read_columns(SEASON / "priority_team_profiles_reviewed.csv", PROFILE_COLUMNS)

    offense = {}
    for row in offense_rows:
        if row["scope"] not in TEAM_SCOPES:
            continue
        pid = _pid(row["player_id"])
        if pid is None:
            continue
        bands = {}
        for key, _label, column in BANDS:
            bands[key] = _rates(row[f"{column}_fgm"], row[f"{column}_fga"], row[f"{column}_fg_pct"])
        offense[pid] = {"scope": row["scope"], "bands": bands}

    defense = {}
    for row in defense_rows:
        if row["band"] not in DEFENSE_BANDS:
            continue
        pid = _pid(row["player_id"])
        if pid is None:
            continue
        key = next(item[0] for item in BANDS if item[2] == row["band"])
        cell = {
            "opponent_fgm": _count(row["opponent_fgm"]),
            "opponent_fga": _count(row["opponent_fga"]),
        }
        pct = shown_pct(row["opponent_fg_pct"], cell["opponent_fga"])
        if pct is not None:
            cell["opponent_fg_pct"] = _float(row["opponent_fg_pct"])
        if key != "other_two":
            normal = _float(row["opponents_normal_fg_pct_official"])
            diff = _float(row["diff_from_opponents_normal_official"])
            if normal is not None:
                cell["opponents_normal_fg_pct_official"] = normal
            if diff is not None:
                cell["diff_from_opponents_normal_official"] = diff
        defense.setdefault(pid, {})[key] = cell

    profiles = {}
    for row in profile_rows:
        pid = _pid(row["player_id"])
        scope = row["scope"] if row["scope"] in TEAM_SCOPES else ""
        if pid is None or not scope:
            continue
        profile = {"player_id": pid, "scope": scope}
        height_cm = _float(row["height_cm_profile"])
        if height_cm is not None and height_cm > 0:
            profile["height_cm"] = height_cm
        if row["height_ft_in_profile"]:
            profile["height_ft_in"] = row["height_ft_in_profile"]
        if row["position_profile"]:
            profile["position"] = row["position_profile"]
            mapped = POSITION_ZH.get(row["position_profile"])
            if mapped:
                profile["position_zh"] = mapped
        status = row["draft_status"]
        if status:
            profile["draft_status"] = status
        for src, dest in (
            ("draft_year", "draft_year"),
            ("draft_round", "draft_round"),
            ("draft_overall_pick", "draft_overall_pick"),
        ):
            number = _positive_int(row[src])
            if number is not None and status != "undrafted":
                profile[dest] = number
        if row["wingspan_status"] == OFFICIAL_WINGSPAN:
            span = format_cm(row["wingspan_cm_official_combine"])
            if span is not None:
                profile["wingspan_cm"] = _float(row["wingspan_cm_official_combine"])
        profiles[pid] = profile
    return {"offense": offense, "defense": defense, "profiles": profiles}


def profile_for(player_id, team: str = "") -> dict | None:
    row = _match(load()["profiles"], _pid(player_id), _team(team))
    if not row:
        return None
    # scope 只用于核对球队，不进 pack。
    return {key: value for key, value in row.items() if key != "scope"}


def _draft_text(profile: dict) -> str | None:
    if profile.get("draft_status") == "undrafted":
        return "落选"
    year = profile.get("draft_year")
    rnd = profile.get("draft_round")
    pick = profile.get("draft_overall_pick")
    text = f"{year}年" if year else ""
    if rnd and pick:
        text += f"第{rnd}轮第{pick}顺位"
    elif rnd:
        text += f"第{rnd}轮"
    elif pick:
        text += f"第{pick}顺位"
    return text or None


def profile_line(profile: dict | None) -> str | None:
    if not profile:
        return None
    parts = []
    feet = profile.get("height_ft_in") or ""
    cm = format_cm(profile.get("height_cm"))
    if feet and cm:
        parts.append(f"身高 {feet}（{cm}）")
    elif feet:
        parts.append(f"身高 {feet}")
    elif cm:
        parts.append(f"身高 {cm}")
    position = profile.get("position_zh") or ""
    if position:
        parts.append(position)
    draft = _draft_text(profile)
    if draft:
        parts.append(draft)
    span = format_cm(profile.get("wingspan_cm"))
    if span:
        parts.append(f"臂展 {span}")
    return " · ".join(parts) or None


def diff_phrase(band: str | None, row: dict | None) -> str | None:
    """其余两分是推算档，不显示和「平时」的差值。"""
    if band == "other_two" or not row:
        return None
    diff = row.get("diff_from_opponents_normal_official")
    if diff is None:
        return None
    points = abs(float(diff) * 100)
    normal = format_pct(row.get("opponents_normal_fg_pct_official"))
    direction = "低" if float(diff) < 0 else "高"
    if f"{points:.1f}" == "0.0":
        base = f"平时 {normal}" if normal else "平时"
        return f"对手在他面前与{base}持平"
    if normal:
        return f"对手在他面前比平时 {normal} {direction} {points:.1f} 个百分点"
    return f"对手在他面前比平时{direction} {points:.1f} 个百分点"


def _defense_bands(player_id, team: str) -> list[dict]:
    pid = _pid(player_id)
    row = load()["defense"].get(pid or -1)
    if not row:
        return []
    # 防守表不读 scope。传了球队时，用资料表的 scope 核对是不是这个队。
    if _team(team) and not _match(load()["profiles"], pid, _team(team)):
        return []
    bands = []
    for key, label, _column in BANDS:
        cell = dict(row.get(key) or {})
        item = {"band": key, "label": label}
        for field in ("opponent_fgm", "opponent_fga", "opponent_fg_pct"):
            if cell.get(field) is not None:
                item[field] = cell[field]
        if key != "other_two":
            for field in ("opponents_normal_fg_pct_official", "diff_from_opponents_normal_official"):
                if cell.get(field) is not None:
                    item[field] = cell[field]
        bands.append(item)
    return bands


def build_zone(shooter_id, defender_id, band: str | None, shooter_team: str = "", defender_team: str = "") -> dict | None:
    if not band and not _pid(shooter_id) and not _pid(defender_id):
        return None
    zone = {}
    if band:
        zone["band"] = band
        zone["label"] = BAND_BY_KEY[band][0]
    shooter = _match(load()["offense"], _pid(shooter_id), _team(shooter_team))
    if shooter and band:
        stats = shooter["bands"].get(band) or {}
        if stats:
            zone["shooter"] = {"player_id": _pid(shooter_id), **stats}
    bands = _defense_bands(defender_id, defender_team) if _pid(defender_id) else []
    if bands:
        zone["defender_bands"] = bands
        current = next((item for item in bands if item["band"] == band), None)
        if current:
            zone["defender"] = {"player_id": _pid(defender_id), **{k: v for k, v in current.items() if k not in ("band", "label")}}
        elif _pid(defender_id):
            zone["defender"] = {"player_id": _pid(defender_id)}
    elif _pid(defender_id) and band:
        zone["defender"] = {"player_id": _pid(defender_id)}
    if not zone:
        return None
    zone["lines"] = zone_lines(zone)
    return zone


def zone_lines(zone: dict | None) -> list[str]:
    if not zone or not zone.get("band"):
        return []
    lines = []
    parts = []
    for band in zone.get("defender_bands") or []:
        pct = shown_pct(band.get("opponent_fg_pct"), band.get("opponent_fga"))
        mark = "（这一投）" if band.get("band") == zone["band"] else ""
        if pct:
            parts.append(f"{band['label']} {pct}{mark}")
        elif _count(band.get("opponent_fga")) == 0:
            parts.append(f"{band['label']} 无出手{mark}")
    if parts:
        lines.append("对手命中率，越低越好")
        lines.append(" · ".join(parts))
    current = zone.get("defender") or {}
    label = zone["label"]
    pct = shown_pct(current.get("opponent_fg_pct"), current.get("opponent_fga"))
    count = count_text(current.get("opponent_fgm"), current.get("opponent_fga"))
    if pct or count:
        detail = f"这一投{label}"
        if pct:
            detail += f" {pct}"
        if count:
            detail += f"（{count}）"
        lines.append(detail)
    elif current.get("player_id"):
        lines.append(f"这一投{label}没有可用的对手命中率")
    phrase = diff_phrase(zone["band"], current)
    if phrase:
        lines.append(phrase)
    shooter = zone.get("shooter") or {}
    shooter_pct = shown_pct(shooter.get("fg_pct"), shooter.get("fga"))
    shooter_count = count_text(shooter.get("fgm"), shooter.get("fga"))
    if shooter_pct or shooter_count:
        text = f"投篮人同区{label}"
        if shooter_pct:
            text += f" {shooter_pct}"
        if shooter_count:
            text += f"（{shooter_count}）"
        lines.append(text)
    return lines


def decorate_shot(shot: dict, *, shooter_id=None, defender_id=None, shooter_team: str = "", defender_team: str = "") -> dict:
    """把资料和这一投所在区写进网页/成片用的 shot。不读名字。"""
    sid = _pid(shooter_id)
    did = _pid(defender_id)
    team = _team(shooter_team or shot.get("team"))
    other = _team(defender_team or (shot.get("defender") or {}).get("team"))
    distance = shot.get("distance")
    if distance is None:
        distance = shot.get("distance_ft")
    band = band_of(distance_ft=distance, value=shot.get("value"),
                   shot_zone=shot.get("zoneKey") or shot.get("shot_zone") or "")
    profile = profile_for(sid, team) if sid else None
    if profile:
        line = profile_line(profile)
        shot["shooterProfile"] = {**profile, **({"line": line} if line else {})}
    else:
        shot.pop("shooterProfile", None)
    defender = shot.get("defender") if isinstance(shot.get("defender"), dict) else None
    if defender is not None:
        dprof = profile_for(did, other) if did else None
        if dprof:
            line = profile_line(dprof)
            defender["profile"] = {**dprof, **({"line": line} if line else {})}
        else:
            defender.pop("profile", None)
    zone = build_zone(sid, did, band, team, other)
    if zone:
        shot["seasonZone"] = zone
    else:
        shot.pop("seasonZone", None)
    return shot


def attach_event(event: dict) -> dict:
    """清单里的出手。player_id / person_id 和领防 person_id 才能对上赛季表。"""
    official = event.get("official")
    if not isinstance(official, dict):
        return event
    if event.get("type") not in (None, "shot") and official.get("kind") != "shot":
        return event
    if event.get("type") != "shot" and official.get("kind") != "shot":
        return event
    defender = official.get("defender") if isinstance(official.get("defender"), dict) else None
    actor = event.get("actor") if isinstance(event.get("actor"), dict) else {}
    fake = {
        "team": actor.get("team") or "",
        "distance": official.get("distance_ft"),
        "value": official.get("value"),
        "zoneKey": (event.get("geometry") or {}).get("shot_zone") or "",
        "defender": defender,
    }
    decorate_shot(
        fake,
        shooter_id=official.get("player_id") or official.get("person_id"),
        defender_id=(defender or {}).get("person_id"),
        shooter_team=actor.get("team") or "",
        defender_team=(defender or {}).get("team") or "",
    )
    if fake.get("shooterProfile"):
        official["player_profile"] = fake["shooterProfile"]
    else:
        official.pop("player_profile", None)
    if defender is not None:
        if (fake.get("defender") or {}).get("profile"):
            defender["profile"] = fake["defender"]["profile"]
        else:
            defender.pop("profile", None)
    if fake.get("seasonZone"):
        official["season_zone"] = fake["seasonZone"]
    else:
        official.pop("season_zone", None)
    return event


def _shot_matches(shot: dict, event: dict) -> bool:
    official = event.get("official") or {}
    actor = event.get("actor") or {}
    team = actor.get("team") or ""
    if team and shot.get("team") and shot.get("team") != team:
        return False
    if shot.get("value") != official.get("value"):
        return False
    left, right = shot.get("distance"), official.get("distance_ft")
    if left is None or right is None:
        return False
    return abs(float(left) - float(right)) <= 0.05


def patch_clips(path: Path = CLIPS) -> int:
    """给已经生成的网页包补字段。只用包里和清单里现成的 player_id。"""
    text = path.read_text(encoding="utf-8")
    prefix = "window.DEEPCOURT_CLIPS="
    if not text.startswith(prefix):
        raise RuntimeError(f"{path} 不是网页回放包")
    packs = json.loads(text[len(prefix):].rstrip().rstrip(";"))
    changed = 0
    for clip, pack in packs.items():
        events = []
        stream_path = REPO / "pipeline" / "events" / "out" / f"{clip}.json"
        if stream_path.exists():
            stream = json.loads(stream_path.read_text(encoding="utf-8"))
            events = [ev for poss in stream.get("possessions") or [] for ev in poss.get("events") or []
                      if ev.get("type") == "shot"]
        for shot in pack.get("shots") or []:
            matched = next((ev for ev in events if _shot_matches(shot, ev)), None)
            official = (matched or {}).get("official") or {}
            shooter_id = official.get("player_id") or official.get("person_id")
            if shooter_id in (None, ""):
                shooter_id = (shot.get("season_profile") or {}).get("player_id")
            defender_id = (shot.get("defender") or {}).get("person_id")
            if defender_id in (None, "") and isinstance(official.get("defender"), dict):
                defender_id = official["defender"].get("person_id")
            before = json.dumps({"p": shot.get("shooterProfile"), "z": shot.get("seasonZone"),
                                 "d": (shot.get("defender") or {}).get("profile")}, ensure_ascii=False, sort_keys=True)
            decorate_shot(
                shot,
                shooter_id=shooter_id,
                defender_id=defender_id,
                shooter_team=shot.get("team") or "",
                defender_team=(shot.get("defender") or {}).get("team") or "",
            )
            after = json.dumps({"p": shot.get("shooterProfile"), "z": shot.get("seasonZone"),
                                "d": (shot.get("defender") or {}).get("profile")}, ensure_ascii=False, sort_keys=True)
            if before != after:
                changed += 1
    path.write_text(prefix + json.dumps(packs, ensure_ascii=False, separators=(",", ":")) + ";\n", encoding="utf-8")
    return changed


if __name__ == "__main__":
    print(f"补了 {patch_clips()} 条出手")
