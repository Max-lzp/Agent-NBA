"""出手帧、防守人、球员赛季数据、分区 xFG → 清单里的出手事件。

出手帧、持球进攻人和领防人来自 pipeline/vision/release.py（results/local/{clip}.release.json）。
领防人是进球那次出手前几秒最常贴着投篮人的对方球员。号码只在当时在场名单里认，票数不够就不写名字，
只报号码没读出；不拿本场对位表顶替。对位表只用来给已经认出的领防人补本场对位数据。
分区 xFG 用 data/ 下所有同时有逐回合和投篮质量日志的比赛，按 7 个出手区域汇总。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

from .join import _side, _zone
from .official import DATA, calibrate_side, load_game, period_number, clock_seconds
from .season_zones import attach_event

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "pipeline" / "vision" / "results" / "local"
ZONES = ("restricted", "paint", "midrange", "corner3_l", "corner3_r", "wing3_l", "wing3_r", "top3")
MIN_ZONE_SHOTS = 3


def load_release(clip_id: str) -> dict:
    path = RESULTS / f"{clip_id}.release.json"
    if not path.exists():
        return {}
    return {s["event_id"]: s for s in json.loads(path.read_text(encoding="utf-8")).get("shots") or []}


@lru_cache(maxsize=None)
def _matchups(game_id: str) -> dict[int, list[dict]]:
    """进攻人 personId → 防他的人（按对位时间降序）。外层是进攻人：一个人各行 playerPoints 之和等于他的得分。"""
    for path in sorted(DATA.rglob(f"{game_id}_boxscorematchups*.json")):
        doc = json.loads(path.read_text(encoding="utf-8")).get("boxScoreMatchups") or {}
        out: dict[int, list[dict]] = {}
        for side in ("homeTeam", "awayTeam"):
            team = doc.get(side) or {}
            other = (doc.get("awayTeam" if side == "homeTeam" else "homeTeam") or {}).get("teamTricode")
            for p in team.get("players") or []:
                rows = []
                for m in p.get("matchups") or []:
                    st = m.get("statistics") or {}
                    rows.append({
                        "person_id": m["personId"], "team": other, "number": m.get("jerseyNum") or "",
                        "name": m.get("familyName") or "", "full_name": f"{m.get('firstName', '')} {m.get('familyName', '')}".strip(),
                        "seconds": st.get("matchupMinutesSort") or 0, "possessions": st.get("partialPossessions"),
                        "fgm": st.get("matchupFieldGoalsMade"), "fga": st.get("matchupFieldGoalsAttempted"),
                        "points": st.get("playerPoints"), "blocks": st.get("matchupBlocks"),
                    })
                out[p["personId"]] = sorted(rows, key=lambda r: -r["seconds"])
        return out
    return {}


@lru_cache(maxsize=1)
def _season() -> dict[int, dict]:
    """leaguedashplayerstats（赛季合计）→ 场均。"""
    out = {}
    for path in sorted(DATA.rglob("leaguedashplayerstats_*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        label = (doc.get("parameters") or {}).get("Season")
        for rs in doc.get("resultSets") or []:
            head = rs.get("headers") or []
            for row in rs.get("rowSet") or []:
                r = dict(zip(head, row))
                gp = r.get("GP") or 0
                if not gp:
                    continue
                per = lambda k: round(r[k] / gp, 1) if r.get(k) is not None else None  # noqa: E731
                out[r["PLAYER_ID"]] = {
                    "season": label,
                    "team": r.get("TEAM_ABBREVIATION"), "gp": gp, "pts": per("PTS"), "reb": per("REB"),
                    "ast": per("AST"), "stl": per("STL"), "blk": per("BLK"), "dreb": per("DREB"),
                    "fg_pct": r.get("FG_PCT"), "fg3_pct": r.get("FG3_PCT"), "ft_pct": r.get("FT_PCT"),
                }
    return out


RANK_MIN_GP = 20
RANK_MIN_MPG = 15.0
RANK_METRICS = {  # 名 → (取值, 越小越好)
    "pts": (lambda b, a: b["PTS"] / b["GP"], False),
    "fg_pct": (lambda b, a: b.get("FG_PCT"), False),
    "ts_pct": (lambda b, a: a.get("TS_PCT"), False),
    "stl": (lambda b, a: b["STL"] / b["GP"], False),
    "blk": (lambda b, a: b["BLK"] / b["GP"], False),
    "dreb": (lambda b, a: b["DREB"] / b["GP"], False),
    "def_rating": (lambda b, a: a.get("DEF_RATING"), True),
}


def stars(rank: int) -> float:
    """队内第 1 名五星，每往后一名少半星，最少半星。"""
    return max(5.0 - 0.5 * (rank - 1), 0.5)


def _rows(path: Path) -> list[dict]:
    rs = (json.loads(path.read_text(encoding="utf-8")).get("resultSets") or [{}])[0]
    return [dict(zip(rs.get("headers") or [], r)) for r in rs.get("rowSet") or []]


@lru_cache(maxsize=1)
def _team_rank() -> dict[int, dict]:
    """同队轮换球员（≥20 场、场均 ≥15 分钟）里的赛季排名。基础数据来自 leaguedashplayerstats，
    真实命中率和防守效率来自 leaguedashadvanced（同一接口 MeasureType=Advanced）。"""
    out: dict[int, dict] = {}
    for path in sorted(DATA.rglob("leaguedashplayerstats_*.json")):
        team_id = path.stem.rsplit("_", 1)[-1]
        adv_path = path.with_name(f"leaguedashadvanced_{team_id}.json")
        adv = {r["PLAYER_ID"]: r for r in _rows(adv_path)} if adv_path.exists() else {}
        pool = [r for r in _rows(path) if (r.get("GP") or 0) >= RANK_MIN_GP and r["MIN"] / r["GP"] >= RANK_MIN_MPG]
        for name, (get, low) in RANK_METRICS.items():
            vals = [(get(r, adv.get(r["PLAYER_ID"], {})), r["PLAYER_ID"]) for r in pool]
            vals = sorted(((v, pid) for v, pid in vals if v is not None), key=lambda x: x[0], reverse=not low)
            for i, (v, pid) in enumerate(vals):
                out.setdefault(pid, {})[name] = {"value": round(v, 3), "rank": i + 1, "of": len(vals), "stars": stars(i + 1)}
    return out


def _quality(shots: list, play: dict) -> Optional[float]:
    hit = next((s for s in shots if s.get("eventNum") == play["action_number"]), None)
    if hit is None:
        hit = next((s for s in shots
                    if period_number(s.get("period")) == play["period"]
                    and abs((clock_seconds(s.get("gameClock")) or -99) - (play["clock_s"] or -1)) <= 1.0
                    and s.get("playerId") in (None, play["person_id"])), None)
    return (hit or {}).get("shotQuality")


@lru_cache(maxsize=1)
def _zone_shots() -> tuple[tuple, ...]:
    """data/ 下每场有坐标、有 xFG 的出手：(personId, zone, made, xfg, gameId)。"""
    rows = []
    ids = sorted({p.name.split("_")[0] for p in DATA.rglob("*_shotqualityshotlog.json")})
    for gid in ids:
        game = load_game(gid)
        if not game["plays"] or not game["shots"]:
            continue
        x_negative, _basis = calibrate_side(game["plays"])
        for play in game["plays"]:
            if play["kind"] != "shot" or play.get("x_ft") is None:
                continue
            zone = _zone(play["x_ft"], play["y_ft"], play["value"], _side(play, x_negative))
            xfg = _quality(game["shots"], play)
            if zone and xfg is not None:
                rows.append((play["person_id"], zone, bool(play["made"]), float(xfg), gid))
    return tuple(rows)


def zone_chart(person_id: int) -> dict:
    rows = _zone_shots()
    games = sorted({r[4] for r in rows})
    league: dict[str, list] = {z: [] for z in ZONES}
    mine: dict[str, list] = {z: [] for z in ZONES}
    for pid, zone, made, xfg, _gid in rows:
        league.setdefault(zone, []).append((made, xfg))
        if pid == person_id:
            mine.setdefault(zone, []).append((made, xfg))
    zones = {}
    for z in ZONES:
        own, base = mine.get(z) or [], league.get(z) or []
        zones[z] = {
            "fga": len(own), "fgm": sum(1 for made, _ in own if made),
            "xfg": round(sum(x for _, x in own) / len(own), 3) if len(own) >= MIN_ZONE_SHOTS else None,
            "league_xfg": round(sum(x for _, x in base) / len(base), 3) if base else None,
            "league_fga": len(base),
        }
    return {"games": games, "min_shots": MIN_ZONE_SHOTS, "zones": zones}


def _on_court_ids(stream: dict, game: dict) -> set[int]:
    names = {(p.get("full_name") or p.get("name")) for team in ((stream.get("official") or {}).get("on_court") or {}).values()
             for p in team}
    return {pid for pid, p in game["players"].items() if p.get("full_name") in names or p.get("name") in names}


def _person(game: dict, team: str, number: str) -> Optional[int]:
    return next((i for i, p in game["players"].items() if p["team"] == team and p["jersey"] == number), None)


def _cv_defender(release: dict, game: dict) -> Optional[dict]:
    d = release.get("defender") or {}
    if not d:
        return None
    out = {"team": d.get("team") or "", "number": d.get("number") or "", "source": "cv",
           "foot_gap_px": d.get("foot_gap_px")}
    if d.get("name"):
        out["name"] = d["name"]
        pid = _person(game, out["team"], out["number"])
        if pid:
            out["person_id"] = pid
            out["full_name"] = game["players"][pid].get("full_name")
    return out


def _lead_defender(release: dict, game: dict) -> Optional[dict]:
    d = (release.get("lead") or {}).get("defender") or {}
    if not d or d.get("status") == "none_close":
        return None
    out = {"team": d.get("team") or "", "source": "cv_lead", "guard_seconds": d.get("guard_seconds"),
           "ocr": {k: d.get(k) for k in ("votes", "reads", "share", "conf", "candidate")}}
    if d.get("status") == "ok" and d.get("number") and d.get("name"):
        out.update({"number": d["number"], "name": d["name"]})
        pid = _person(game, out["team"], d.get("jersey") or d["number"])
        if pid:
            out["person_id"] = pid
            out["full_name"] = game["players"][pid].get("full_name")
    else:
        out.update({"number": "", "status": "number_unread" if d.get("status") == "ok" else "short_guard"})
    return out


def attach(stream: dict, clip_id: str, game: dict, game_id: str) -> dict:
    releases = load_release(clip_id)
    table = _matchups(game_id)
    season = _season()
    ranks = _team_rank()
    court = _on_court_ids(stream, game)
    for poss in stream.get("possessions") or []:
        for ev in poss.get("events") or []:
            official = ev.get("official") or {}
            if ev.get("type") != "shot" or not official:
                continue
            rel = releases.get(ev["event_id"]) or {}
            if rel.get("t") is not None:
                ev["release"] = {"t": rel["t"], "source": rel["source"], "shooter_box": rel["shooter_box"],
                                 "ball_box": rel.get("ball_box"), "t_official": rel.get("t_official")}
            shooter = official.get("person_id")
            nearest = _cv_defender(rel, game)
            lead = _lead_defender(rel, game)
            guards = [g for g in table.get(shooter, []) if not court or g["person_id"] in court]
            defense = {}
            if nearest:
                defense["nearest"] = nearest
                if rel.get("defender", {}).get("box"):
                    defense["nearest_box"] = rel["defender"]["box"]
            if lead:
                defense["lead_box"] = ((rel.get("lead") or {}).get("defender") or {}).get("box_at_release")
                defense["lead_path"] = ((rel.get("lead") or {}).get("defender") or {}).get("path")
            if guards:
                defense["matchup"] = {**guards[0], "source": "boxscorematchupsv3"}
            sh = (rel.get("lead") or {}).get("shooter")
            if sh and ev.get("release"):
                ev["release"]["shooter_path"] = sh.get("path")
            if sh:
                official["shooter_check"] = {k: sh.get(k) for k in (
                    "number", "official_number", "votes", "reads", "seconds", "hold_seconds")}
            if lead:
                official["defender"] = lead
                pid = lead.get("person_id")
                vs = next((g for g in table.get(shooter, []) if pid and g["person_id"] == pid), None)
                if vs:
                    official["defender"]["vs_shooter"] = {k: vs[k] for k in ("seconds", "possessions", "fgm", "fga", "points", "blocks")}
                if pid in season:
                    official["defender"]["season"] = season[pid]
                if pid in ranks:
                    official["defender"]["team_rank"] = ranks[pid]
            else:
                official.pop("defender", None)
            if defense:
                ev["defense"] = defense
            if shooter in season:
                official["shooter_season"] = season[shooter]
            if shooter in ranks:
                official["shooter_rank"] = ranks[shooter]
            if shooter:
                official["zone_chart"] = zone_chart(shooter)
            attach_event(ev)
    return stream
