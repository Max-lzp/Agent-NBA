"""事件流清单 + 记分牌读数 + 官方数据 → 增强版清单。

对时间：记分牌每次读数给出（视频秒, 节次, 比赛钟）。比赛钟显示是截断到整秒的，
一条官方记录的视频时刻 = 第一次读到「同节、钟 ≤ 记录时钟」的那一刻，误差约一次采样（0.33 秒）。

对进球：进球后的比分是精确钥匙。记分牌确认的得分（客, 主）直接查官方逐回合里比分相同的命中，
不依赖时钟精度；查不到再退回时钟窗口。

    python3 -m pipeline.enrich.join --clip amen --game-id 0022500491
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

from .margin import season_margin
from .official import DATA, calibrate_side, clock_seconds, game_roster, load_game, on_court, period_number

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "pipeline" / "vision" / "results" / "local"
OUT = ROOT / "pipeline" / "events" / "out"
ROSTER_FILE = ROOT / "pipeline" / "vision" / "roster.json"

MATCH_WINDOW_S = 3.0        # 清单里的出手和官方出手在视频时间上最多差多少
OFFICIAL_CONF = 0.95
KINDS_AS_EVENTS = {"shot": "shot", "rebound": "rebound", "turnover": "turnover"}


class Clock:
    """视频秒 ↔ （节次, 比赛钟秒）。"""

    def __init__(self, scoreboard: dict):
        self.reads = []
        for r in scoreboard.get("reads", []):
            c, p = clock_seconds(r.get("clock")), period_number(r.get("period"))
            if c is not None:
                self.reads.append((r["t"], p, c))
        periods = [p for _, p, _ in self.reads if p]
        self.period = max(set(periods), key=periods.count) if periods else None
        self.away, self.home = scoreboard.get("away"), scoreboard.get("home")

    def window(self) -> Optional[tuple[float, float]]:
        clocks = [c for _, p, c in self.reads if p in (None, self.period)]
        return (max(clocks) + 1.0, min(clocks)) if clocks else None

    def video_t(self, period: int, clock_s: float) -> Optional[float]:
        if period != self.period:
            return None
        for t, p, c in self.reads:
            if p in (None, self.period) and c <= clock_s:
                return t
        return None

    def at(self, t: float) -> Optional[float]:
        best = None
        for rt, _, c in self.reads:
            if rt <= t + 1e-6:
                best = c
        return best


def _ref(players: dict, pid: Optional[int], team: str = "", name: str = "") -> dict:
    p = players.get(pid or -1)
    if p:
        return {"team": p["team"], "number": p["jersey"], "name": p["name"]}
    return {"team": team, "number": "", **({"name": name} if name else {})}


CENTER_FT = 3.0   # 离中线不到 3 英尺算正中，不分左右


def _side(play: dict, x_negative: str) -> str:
    if play.get("side") in {"left", "right"}:
        return play["side"]
    x = play.get("x_ft")
    if x is None or abs(x) < CENTER_FT:
        return ""
    return x_negative if x < 0 else ("right" if x_negative == "left" else "left")


def _zone(x: Optional[float], y: Optional[float], value: Optional[int], side: str = "") -> Optional[str]:
    """出手区域；左右按投篮人面向篮筐，side 为空时不分左右的区域照常给。"""
    if x is None or y is None:
        return None
    s = {"left": "l", "right": "r"}.get(side)
    if value == 3:
        if abs(x) < 8:
            return "top3"
        if not s:
            return None
        return f"corner3_{s}" if y <= 9.0 else f"wing3_{s}"
    d = (x * x + y * y) ** 0.5
    if d <= 4.0:
        return "restricted"
    if abs(x) <= 8 and y <= 14.0:
        return "paint"
    return "midrange"


def _shot_quality(shots: list, play: dict) -> dict:
    """投篮质量日志：先按 eventNum == actionNumber 精确对，对不上再按节次 + 比赛钟 ±1s + 投篮人。"""
    hit = next((s for s in shots if s.get("eventNum") is not None and s.get("eventNum") == play["action_number"]), None)
    if hit is None:
        hit = next((s for s in shots
                    if period_number(s.get("period")) == play["period"]
                    and abs((clock_seconds(s.get("gameClock")) or -99) - (play["clock_s"] or -1)) <= 1.0
                    and s.get("playerId") in (None, play["person_id"])), None)
    if hit is None:
        return {}
    xfg = next((hit[k] for k in ("shotQuality", "xFGPercentage") if hit.get(k) is not None), None)
    out = {"xfg": xfg, "defender_id": hit.get("defenderId"),
           "video_url": hit.get("largeUrl") or hit.get("mediumUrl") or None,
           "thumbnail": hit.get("largeThumbnail") or hit.get("mediumThumbnail") or None}
    return {k: v for k, v in out.items() if v not in (None, "")}


def plays_in_clip(game: dict, clock: Clock) -> list[dict]:
    win = clock.window()
    if not win or not game["plays"]:
        return []
    hi, lo = win
    out = []
    for play in game["plays"]:
        if play["period"] != clock.period or play["clock_s"] is None or not (lo - 0.5 <= play["clock_s"] <= hi):
            continue
        t = clock.video_t(play["period"], play["clock_s"])
        if t is None:
            continue
        out.append({**play, "t": t})
    return out


def _made_by_score(game: dict, clock: Clock, scoreboard: dict) -> dict[float, dict]:
    """记分牌确认的得分 → 官方命中记录，用得分后的比分精确对。"""
    hits = {}
    reads = scoreboard.get("reads", [])
    for tick in scoreboard.get("ticks", []):
        after = next((r["scores"] for r in reads if r["t"] >= tick["t_sec"] and r.get("scores")), None)
        if not after:
            continue
        away, home = after
        for play in game["plays"]:
            if (play["kind"] == "shot" and play["made"] and play["score_away"] == away
                    and play["score_home"] == home and play["team"] == tick["team"]):
                hits[tick.get("t_reset") or tick["t_sec"]] = play
                break
    return hits


def official_detail(play: dict, players: dict, shots: list) -> dict:
    detail = {
        "action_number": play["action_number"],
        "person_id": play["person_id"],
        "period": play["period"],
        "clock_s": play["clock_s"],
        "kind": play["kind"],
        "description": play["description"],
    }
    if play["kind"] == "shot":
        detail.update({"made": play["made"], "value": play["value"], "shot_type": play["sub_type"],
                       "distance_ft": play["distance_ft"]})
        if play["assist_id"] or play["assist_name"]:
            detail["assist"] = _ref(players, play["assist_id"], play["team"], play["assist_name"] or "")
        quality = _shot_quality(shots, play)
        for key in ("xfg", "video_url", "thumbnail"):
            if key in quality:
                detail[key] = quality[key]
        if quality.get("defender_id"):
            detail["defender"] = _ref(players, quality["defender_id"])
    return detail


def enrich(stream: dict, scoreboard: dict, game: dict, game_id: str) -> dict:
    clock = Clock(scoreboard)
    players, shots = game["players"], game["shots"]
    clip_plays = plays_in_clip(game, clock)
    x_negative, side_basis = calibrate_side(game["plays"])
    by_score = _made_by_score(game, clock, scoreboard)
    used: set[int] = set()
    named: dict[int, dict] = {}      # 轨迹 → 官方确认的人
    assists: list[tuple] = []        # （出手时刻, 投篮人轨迹, 助攻者）

    def possession_at(t: float) -> dict:
        poss = stream["possessions"]
        return next((p for p in poss if p["t_start"] - 1e-6 <= t <= p["t_end"] + 1e-6), poss[-1])

    def apply(ev: dict, play: dict) -> None:
        used.add(play["action_number"])
        actor = _ref(players, play["person_id"], play["team"], play["name"])
        track = ev["actor"].get("track_id")
        if ev["actor"].get("name") != actor.get("name"):
            ev["actor"] = actor
        else:
            ev["actor"].update(actor)
        if track is not None:
            ev["actor"]["track_id"] = track
            if actor.get("name"):
                named[track] = {k: actor[k] for k in ("team", "number", "name") if k in actor}
                if play["kind"] == "shot" and play.get("made") and (play["assist_id"] or play["assist_name"]):
                    assists.append((ev["t"], track, _ref(players, play["assist_id"], play["team"],
                                                         play["assist_name"] or "")))
        ev["confidence"] = max(ev["confidence"], OFFICIAL_CONF)
        ev["official"] = official_detail(play, players, shots)
        margin = season_margin(play, players, clock.away, clock.home)
        if margin:
            ev["official"]["season_margin"] = margin
        if play["kind"] == "shot" and play["x_ft"] is not None:
            geom = ev.setdefault("geometry", {})
            geom.update({"x_ft": play["x_ft"], "y_ft": play["y_ft"]})
            side = _side(play, x_negative)
            zone = _zone(play["x_ft"], play["y_ft"], play["value"], side)
            if zone:
                geom["shot_zone"] = zone
            if play.get("court_half"):
                ev["official"]["court_half"] = play["court_half"]
            if side:
                ev["official"]["side"] = side
                ev["official"]["side_basis"] = "official" if play.get("side") else side_basis

    # 1. 清单里已有的出手：先按比分精确对，再按时间窗口对
    for poss in stream["possessions"]:
        for ev in poss["events"]:
            if ev["type"] != "shot":
                continue
            play = next((p for t, p in by_score.items() if abs(t - ev["t"]) <= MATCH_WINDOW_S), None)
            if play is None:
                cands = [p for p in clip_plays if p["kind"] == "shot" and p["action_number"] not in used
                         and p["team"] == ev["actor"].get("team") and abs(p["t"] - ev["t"]) <= MATCH_WINDOW_S]
                play = min(cands, key=lambda p: abs(p["t"] - ev["t"]), default=None)
            if play:
                apply(ev, play)

    # 2. 官方有、清单没有的出手 / 篮板 / 失误补进来
    added = 0
    for play in clip_plays:
        kind = KINDS_AS_EVENTS.get(play["kind"])
        if not kind or play["action_number"] in used:
            continue
        poss = possession_at(play["t"])
        ev = {"event_id": f"{poss['possession_id']}-O{play['action_number']}", "t": round(play["t"], 2),
              "type": kind, "actor": {"team": play["team"], "number": ""}, "confidence": OFFICIAL_CONF,
              "source": "official"}
        apply(ev, play)
        poss["events"].append(ev)
        added += 1

    # 2.5 官方确认的人回填到同一条轨迹上没名字的引用；轨迹已被号码认成别人时不改，记冲突
    filled, conflicts = _fill_tracks(stream, named, assists)

    # 2.6 片段开始时双方场上 10 人；号码认出的人不在场上，多半是号码读错
    win = clock.window()
    lineup = on_court(game, clock.period, win[0]) if win else {}
    numbers_on = {team: {players[i]["jersey"] for i in ids} for team, ids in lineup.items()}
    for poss in stream["possessions"]:
        for ev in poss["events"]:
            for key in ("actor", "target"):
                ref = ev.get(key) or {}
                court = numbers_on.get(ref.get("team"), set())
                if ref.get("number") and ref.get("named_by") != "official" and len(court) == 5 \
                        and ref["number"] not in court:
                    conflicts.append({"event_id": ev["event_id"], "track_id": ref.get("track_id"),
                                      "cv": f"#{ref['number']} {ref.get('name', '')}".strip(), "official": "not_on_court"})

    # 3. 官方在片段里没记的失误，是视觉误判，删掉
    official_to = [p["t"] for p in clip_plays if p["kind"] == "turnover"]
    dropped = 0
    if clip_plays:
        for poss in stream["possessions"]:
            keep = []
            for ev in poss["events"]:
                if ev["type"] == "turnover" and ev["source"] != "official" \
                        and not any(abs(t - ev["t"]) <= MATCH_WINDOW_S for t in official_to):
                    dropped += 1
                    continue
                keep.append(ev)
            poss["events"] = sorted(keep, key=lambda e: e["t"])

    # 4. 回合级：节次、比赛钟、比分
    reads = [r for r in scoreboard.get("reads", []) if r.get("scores")]
    for poss in stream["possessions"]:
        s0 = next((r["scores"] for r in reads if r["t"] >= poss["t_start"]), None)
        s1 = next((r["scores"] for r in reversed(reads) if r["t"] <= poss["t_end"]), None)
        poss["official"] = {"period": clock.period, "clock_start_s": clock.at(poss["t_start"]),
                            "clock_end_s": clock.at(poss["t_end"]),
                            "score_start": {clock.away: s0[0], clock.home: s0[1]} if s0 else None,
                            "score_end": {clock.away: s1[0], clock.home: s1[1]} if s1 else None}

    stream["video"]["game_id"] = game_id
    stream["official"] = {
        "game_id": game_id,
        "files": game["files"],
        "period": clock.period,
        "clock_window_s": list(win) if win else None,
        "matched_by_score": len(by_score),
        "x_negative_side": x_negative,
        "side_basis": side_basis,
        "added_events": added,
        "dropped_cv_turnovers": dropped,
        "on_court": {team: [{k: players[i][k] for k in ("jersey", "name", "full_name") if k in players[i]} for i in ids]
                     for team, ids in lineup.items()},
        "named_from_official": filled,
        "track_conflicts": conflicts,
        "plays": [{k: p[k] for k in ("t", "action_number", "clock_s", "team", "name", "kind", "made", "value",
                                     "description")} for p in clip_plays],
    }
    return stream


def _fill_tracks(stream: dict, named: dict[int, dict], assists: list[tuple]) -> tuple[int, list[dict]]:
    filled, conflicts = 0, []
    events = sorted((e for p in stream["possessions"] for e in p["events"]), key=lambda e: e["t"])
    for shot_t, track, helper in assists:
        last = next((e for e in reversed(events) if e["t"] < shot_t and e["type"] == "pass"
                     and e.get("target", {}).get("track_id") == track), None)
        actor = last["actor"] if last else {}
        if helper.get("name") and actor.get("track_id") is not None and not actor.get("name") \
                and actor.get("team") == helper.get("team"):
            named.setdefault(actor["track_id"], {k: helper[k] for k in ("team", "number", "name") if k in helper})
    for ev in events:
        for key in ("actor", "target"):
            ref = ev.get(key)
            who = named.get(ref.get("track_id")) if ref else None
            if not who or ref.get("team") not in (None, "", who["team"]):
                continue
            if ref.get("name") and ref["name"] != who["name"]:
                conflicts.append({"event_id": ev["event_id"], "track_id": ref["track_id"],
                                  "cv": ref["name"], "official": who["name"]})
            elif not ref.get("name"):
                ref.update(who)
                ref["named_by"] = "official"
                filled += 1
    return filled, conflicts


def clip_on_court(game: dict, scoreboard: dict) -> dict[str, list[str]]:
    """片段开始和结束时在场的人取并集（中途换人也算），返回 {队: [号码]}；认不齐 5 人的队不给。"""
    clock = Clock(scoreboard)
    win = clock.window()
    if not win or not game["players"]:
        return {}
    court: dict[str, set] = {}
    for at in win:
        for team, ids in on_court(game, clock.period, at).items():
            if len(ids) == 5:
                court.setdefault(team, set()).update(game["players"][i]["jersey"] for i in ids)
    return {team: sorted(nums) for team, nums in sorted(court.items())}


def write_game_roster(game_id: str, players: dict) -> None:
    roster = json.loads(ROSTER_FILE.read_text(encoding="utf-8"))
    roster["games"][game_id] = game_roster(players)
    ROSTER_FILE.write_text(json.dumps(roster, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="清单 + 官方数据 → 增强版清单")
    ap.add_argument("--clip", required=True)
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--data-dir", default=str(DATA))
    ap.add_argument("--stream", help="默认 pipeline/events/out/{clip}.json")
    ap.add_argument("--out", help="默认覆盖 --stream")
    ap.add_argument("--write-roster", action="store_true", help="把 boxscore 的出场名单写进 roster.json（之后重建清单）")
    args = ap.parse_args()

    game = load_game(args.game_id, Path(args.data_dir))
    if not game["plays"]:
        raise SystemExit(f"{args.data_dir} 下没有 {args.game_id} 的逐回合数据（{args.game_id}_playbyplay*.json）")
    if args.write_roster and game["players"]:
        write_game_roster(args.game_id, game["players"])
        print(f"[名单] roster.json games[{args.game_id}] 已写入 {len(game['players'])} 人")
    stream_path = Path(args.stream or OUT / f"{args.clip}.json")
    stream = json.loads(stream_path.read_text(encoding="utf-8"))
    board = json.loads((RESULTS / f"{args.clip}.scoreboard.json").read_text(encoding="utf-8"))
    enriched = enrich(stream, board, game, args.game_id)
    Path(args.out or stream_path).write_text(json.dumps(enriched, ensure_ascii=False, indent=2) + "\n",
                                             encoding="utf-8")
    info = enriched["official"]
    print(f"[官方] {args.game_id} 第 {info['period']} 节 窗口 {info['clock_window_s']}，片段内 {len(info['plays'])} 条记录，"
          f"比分对上 {info['matched_by_score']}，补入 {info['added_events']}，删掉视觉失误 {info['dropped_cv_turnovers']}")


if __name__ == "__main__":
    main()
