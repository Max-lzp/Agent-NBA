"""轨迹 → 球员身份。一个球员可以有多条轨迹，一条轨迹只对应一个球员。

1. 每条轨迹用号码投票结果查赛季名单（roster_map.identify）。
2. 同一身份的两条轨迹在同一时刻同时出现，留读号更稳的那条，另一条退回未知。
3. 没读出号码的轨迹，若紧接着同队某人的轨迹消失处出现，接到那个人身上。
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pipeline.vision.roster_map import identify

from .schema import PlayerRef

CLIPS = json.loads((Path(__file__).resolve().parents[1] / "vision" / "clips.json").read_text(encoding="utf-8"))

MAX_OVERLAP_FRAMES = 1      # 同一身份的两条轨迹最多共存的帧数
STITCH_GAP_S = 1.0          # 前一条轨迹消失到后一条出现的最大间隔
STITCH_DIST_PX = 220        # 消失点到出现点的最大像素距离（1080p）
STITCH_CONF_SCALE = 0.7     # 接出来的身份置信度折扣


@dataclass
class TrackSpan:
    track_id: int
    team: Optional[str]
    frames: dict[float, tuple[float, float]]   # t → 脚底点

    @property
    def t_start(self) -> float:
        return min(self.frames)

    @property
    def t_end(self) -> float:
        return max(self.frames)


def _spans(doc: dict) -> dict[int, TrackSpan]:
    teams = {p["track_id"]: p.get("team") for p in doc["players"] if p.get("role") == "player"}
    spans: dict[int, TrackSpan] = {}
    for row in doc["timeline"]:
        for p in row["players"]:
            if p.get("role") != "player" or not p.get("bbox"):
                continue
            x, y, w, h = p["bbox"]
            tid = p["track_id"]
            span = spans.setdefault(tid, TrackSpan(tid, teams.get(tid), {}))
            span.frames[float(row["t"])] = (x + w / 2, y + h)
    return spans


def _overlap(a: TrackSpan, b: TrackSpan) -> int:
    return len(a.frames.keys() & b.frames.keys())


def resolve(doc: dict) -> dict[int, tuple[PlayerRef, float]]:
    """local CV 文档（pipeline/vision/analyze.py 的输出）→ {track_id: (PlayerRef, confidence)}。
    分不出队伍的轨迹 team 为空串：不能当持球人，但离球最近时让这一帧作废。"""
    clip = CLIPS.get(doc.get("clip"), {})
    color_map = clip.get("jersey_color_to_team", {})
    game_id = doc.get("game_id")
    spans = _spans(doc)
    summary = {p["track_id"]: p for p in doc["players"] if p.get("role") == "player"}

    found: dict[int, dict] = {}
    for tid, p in summary.items():
        if p.get("jersey"):
            hit = identify(p["jersey"], team=p.get("team"), color=p.get("jersey_color"),
                           color_to_team=color_map, game_id=game_id)
            if hit.get("name") and not hit.get("dnp"):
                found[tid] = hit

    owners: dict[tuple[str, str], list[int]] = defaultdict(list)
    strength = lambda tid: (summary[tid].get("jersey_votes", 0), summary[tid].get("jersey_conf", 0.0),
                            summary[tid].get("samples", 0))
    for tid in sorted(found, key=strength, reverse=True):
        key = (found[tid]["team"], found[tid]["name"])
        if tid in spans and any(_overlap(spans[tid], spans[o]) > MAX_OVERLAP_FRAMES
                                for o in owners[key] if o in spans):
            del found[tid]
            continue
        owners[key].append(tid)

    changed = True
    while changed:
        changed = False
        for tid, span in spans.items():
            if tid in found or not span.team:
                continue
            best, best_d = None, STITCH_DIST_PX
            for key, tids in owners.items():
                if key[0] != span.team:
                    continue
                if any(_overlap(span, spans[o]) > MAX_OVERLAP_FRAMES for o in tids if o in spans):
                    continue
                for o in tids:
                    if o not in spans:
                        continue
                    other = spans[o]
                    if 0 <= span.t_start - other.t_end <= STITCH_GAP_S:
                        a, b = other.frames[other.t_end], span.frames[span.t_start]
                    elif 0 <= other.t_start - span.t_end <= STITCH_GAP_S:
                        a, b = span.frames[span.t_end], other.frames[other.t_start]
                    else:
                        continue
                    d = ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
                    if d < best_d:
                        best, best_d = (key, o), d
            if best:
                key, donor = best
                found[tid] = {**found[donor], "confidence": found[donor]["confidence"] * STITCH_CONF_SCALE,
                              "source": "stitched"}
                owners[key].append(tid)
                changed = True

    out: dict[int, tuple[PlayerRef, float]] = {}
    for tid, p in summary.items():
        hit = found.get(tid)
        if hit:
            out[tid] = (PlayerRef(team=hit["team"], number=hit["jersey"], name=hit["name"], track_id=tid),
                        hit["confidence"])
        else:
            out[tid] = (PlayerRef(team=p.get("team") or "", number="", track_id=tid), 0.0)
    return out
