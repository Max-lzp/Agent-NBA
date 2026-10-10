"""传球与失误：持球权转移即事件。

规则（与 Basketball-Scoring-Analysis 的 game logic 同思路）：
- 相邻持球段，持球人同队 → pass（actor=传出者，target=接球人）
- 相邻持球段，持球人跨队 → turnover（actor=失误者，target=得球方）
- 回合开始的第一段持球 → receive

注意：连续两段之间若间隔过大（> GAP_SHOT_S），中间很可能有出手/篮板，
这里只标 receive，把判断留给 shots.py，不抢戏。
"""
from __future__ import annotations

from .possession import HolderSeg
from .schema import Event, PlayerRef

GAP_SHOT_S = 1.2      # 持球段间隔超过它，先怀疑中间有出手，不打 pass
MIN_CONF = 0.5        # 低于此置信度的段不参与转移判定


def possession_events(possession_id: str, segs: list[HolderSeg],
                      start_index: int = 1) -> list[Event]:
    events: list[Event] = []
    prev: HolderSeg | None = None
    n = start_index

    for seg in segs:
        if seg.confidence < MIN_CONF:
            continue
        if prev is not None and seg.holder.key() == prev.holder.key():
            prev = seg
            continue
        if prev is None:
            events.append(Event(
                event_id=f"{possession_id}-E{n}", t=seg.t_start, type="receive",
                actor=seg.holder, confidence=seg.confidence))
            n += 1
        else:
            gap = seg.t_start - prev.t_end
            same_team = seg.holder.team == prev.holder.team
            if gap > GAP_SHOT_S:
                etype = "receive"       # 中间多半有出手/篮板，交给 shots.py
                conf = min(prev.confidence, seg.confidence) * 0.8
            elif same_team:
                etype = "pass"
                conf = min(prev.confidence, seg.confidence)
            else:
                etype = "turnover"
                conf = min(prev.confidence, seg.confidence)
            events.append(Event(
                event_id=f"{possession_id}-E{n}", t=seg.t_start, type=etype,
                actor=prev.holder, target=seg.holder, confidence=conf))
            n += 1
        prev = seg

    return events
