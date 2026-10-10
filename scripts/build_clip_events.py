#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给每个投篮剪辑挂上「时间窗内的官方非投篮事件」（篮板/抢断/失误/犯规/盖帽）。

输入: games/*_shotqualityshotlog.json + games/*_playbyplay.json + games/*_boxscore.json
输出: data/2025-26/clips/_clip_events.json
  key = {gameId}_{eventNum}（与 clips/ 下 mp4 同名）
  每条: 投篮人(官方背号) + window(剪辑覆盖的比赛钟区间) + events[](带 actor 背号、
        与投篮的相对秒数 deltaSec——负值=发生在投篮之后)
用途: 管线里「拿到球的轨迹→官方事件 actor」对人，以及投票结果的旁证。
"""
import json
import re
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data" / "2025-26"  # team-repo/scripts -> 工作区根

roster = {}
for f in DATA.glob("games/*_boxscore.json"):
    d = json.load(open(f))
    for side in ("homeTeam", "awayTeam"):
        for p in d["game"][side]["players"]:
            roster[p["personId"]] = {"name": p.get("name"), "number": p.get("jerseyNum")}


def clock_to_sec(gc):
    """支持 'M:SS' 和 ISO 'PT00M46.30S' 两种官方格式"""
    gc = str(gc)
    if gc.startswith("PT"):
        m = re.search(r"PT(\d+)M([\d.]+)S", gc)
        return int(m.group(1)) * 60 + float(m.group(2)) if m else None
    m, s = gc.split(":")
    return int(m) * 60 + int(s)


EVENT_TYPES = {"rebound", "steal", "turnover", "foul", "block", "violation"}
out, stats = {}, {"clips": 0, "with_events": 0, "events": 0,
                  "name_shooter": 0, "name_teammate": 0, "name_opponent": 0}

for f in sorted(DATA.glob("games/*_shotqualityshotlog.json")):
    d = json.load(open(f))
    gid = d["parameters"]["gameId"]
    pbp = json.load(open(DATA / f"games/{gid}_playbyplay.json"))
    actions = pbp["game"]["actions"]
    for s in d["shots"]:
        key = f"{gid}_{s['eventNum']}"
        shot_sec = clock_to_sec(s["gameClock"])
        dur = (s.get("largeDuration") or 11000) / 1000
        # 剪辑 ≈ [投篮前 dur-4.5 秒, 投篮后 4.5 秒]（比赛钟倒数）
        end, start = shot_sec - (dur - 4.5), shot_sec + 4.5
        evs = []
        for a in actions:
            if a["period"] != s["period"]:
                continue
            try:
                asec = clock_to_sec(a["clock"])
            except Exception:
                continue
            if not (end - 2 <= asec <= start + 2):
                continue
            at = (a.get("actionType") or "").lower()
            if at not in EVENT_TYPES:
                continue
            pid = a.get("personId")
            who = roster.get(pid, {})
            evs.append({
                "type": at, "sub": a.get("subType"), "clock": a["clock"],
                "deltaSec": round(shot_sec - asec, 1),  # 负=投篮之后发生
                "playerId": pid, "name": who.get("name"), "number": who.get("number"),
                "team": a.get("teamTricode"), "desc": (a.get("description") or "")[:60],
            })
        out[key] = {
            "gameId": gid, "eventNum": s["eventNum"], "period": s["period"],
            "shotClock": s["gameClock"], "shooter": s["playerName"],
            "shooterId": s["playerId"],
            "shooterNumber": (roster.get(s["playerId"]) or {}).get("number"),
            "shooterTeam": s["teamAbbreviation"],
            "window": [round(end, 1), round(start, 1)],
            "events": evs,
        }
        stats["clips"] += 1
        if evs:
            stats["with_events"] += 1
        stats["events"] += len(evs)
        for e in evs:
            if e["playerId"] == s["playerId"]:
                stats["name_shooter"] += 1
            elif e["team"] == s["teamAbbreviation"]:
                stats["name_teammate"] += 1
            else:
                stats["name_opponent"] += 1

json.dump(out, open(DATA / "clips" / "_clip_events.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps(stats, ensure_ascii=False))
print(json.dumps(out["0022500160_165"], ensure_ascii=False)[:500])
