"""出手帧、防守人、球员赛季数据、分区 xFG → 清单里的出手事件。

出手帧和最近防守人来自 pipeline/vision/release.py（results/local/{clip}.release.json）。
画面读出防守人号码并对上名单时用画面；读不出时，用本场对位表（boxscorematchupsv3）里
防这名投篮人时间最长、且片段里在场的人兜底，source 写明是对位表，不当成这一投的防守人。
分区 xFG 用 data/ 下所有同时有逐回合和投篮质量日志的比赛，按 7 个出手区域汇总。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

from .join import _side, _zone
from .official import DATA, calibrate_side, load_game, period_number, clock_seconds

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


def _cv_defender(release: dict, game: dict) -> Optional[dict]:
    d = release.get("defender") or {}
    if not d:
        return None
    out = {"team": d.get("team") or "", "number": d.get("number") or "", "source": "cv",
           "foot_gap_px": d.get("foot_gap_px")}
    if d.get("name"):
        out["name"] = d["name"]
        pid = next((i for i, p in game["players"].items() if p["team"] == out["team"] and p["jersey"] == out["number"]), None)
        if pid:
            out["person_id"] = pid
            out["full_name"] = game["players"][pid].get("full_name")
    return out


def attach(stream: dict, clip_id: str, game: dict, game_id: str) -> dict:
    releases = load_release(clip_id)
    table = _matchups(game_id)
    season = _season()
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
            guards = [g for g in table.get(shooter, []) if not court or g["person_id"] in court]
            main = guards[0] if guards else None
            defense = {}
            if nearest:
                defense["nearest"] = nearest
                if rel.get("defender", {}).get("box"):
                    defense["nearest_box"] = rel["defender"]["box"]
            if main:
                defense["matchup"] = {**main, "source": "boxscorematchupsv3"}
            chosen = nearest if nearest and nearest.get("name") else (
                {**{k: main[k] for k in ("team", "number", "name", "full_name", "person_id")}, "source": "matchup"} if main else None)
            if chosen:
                official["defender"] = chosen
                pid = chosen.get("person_id")
                vs = next((g for g in table.get(shooter, []) if g["person_id"] == pid), None)
                if vs:
                    official["defender"]["vs_shooter"] = {k: vs[k] for k in ("seconds", "possessions", "fgm", "fga", "points", "blocks")}
                if pid in season:
                    official["defender"]["season"] = season[pid]
            if defense:
                ev["defense"] = defense
            if shooter in season:
                official["shooter_season"] = season[shooter]
            if shooter:
                official["zone_chart"] = zone_chart(shooter)
    return stream
