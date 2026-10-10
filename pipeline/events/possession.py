"""持球判定：每一帧谁拿着球。

输入是归一化观测 FrameObs（full / vlm 两种模式都转成它）。
规则：球附近 POSSESSION_RADIUS 内最近的球员为候选持球人；
候选需连续 HOLD_FRAMES 帧才确认换手（防抖，避免球在两手之间抖动）。

参考：HanaFEKI/AI_BasketBall_Analysis_v1 的 ball_acquisition 模块；
其 README 明确说持球判定是全系统最难的一环——所以这里带置信度和防抖。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from .schema import PlayerRef

POSSESSION_RADIUS_PX = 90    # 球离身体框的最大距离（像素，1080p 初值；标定上线后换英尺 ~3.5ft）
HOLD_FRAMES = 2              # 换手需连续确认的帧数
CARRY_MAX_S = 1.5            # 球不可见时持球人最多顺延的秒数（运球遮挡是常态）
CARRY_DECAY = 0.9            # 顺延每帧置信度衰减
NOISE_CONF = 0.4             # 无球 / 距离超阈值时的置信度


@dataclass
class PlayerXY:
    player: PlayerRef
    x: float                  # 像素或英尺，取决于上游模式
    y: float
    height_px: float = 0.0    # 检测框高度，定性距离兜底用


@dataclass
class FrameObs:
    """归一化逐帧观测——两种模式共用这一入口。"""
    t_sec: float
    players: list[PlayerXY]
    ball: Optional[tuple[float, float]] = None   # None = 球不可见
    calibration: str = "none"                    # fresh / stale / none


@dataclass
class HolderSeg:
    """一段连续持球：同一持球人从 t_start 到 t_end。"""
    holder: PlayerRef
    t_start: float
    t_end: float
    confidence: float


def _ball_gap(ball: tuple[float, float], p: PlayerXY) -> float:
    """球到球员身体框的最近距离（像素）。
    由脚底点和身高近似身体框：宽≈0.5×身高，框顶=脚底-身高。
    球在框内（持球常态：球在手，手在身体上方）距离为 0。
    """
    half_w = p.height_px * 0.25
    left, right = p.x - half_w, p.x + half_w
    top, bottom = p.y - p.height_px, p.y
    dx = max(left - ball[0], 0.0, ball[0] - right)
    dy = max(top - ball[1], 0.0, ball[1] - bottom)
    return math.hypot(dx, dy)


def _nearest(ball: tuple[float, float], players: list[PlayerXY]) -> tuple[Optional[PlayerRef], float]:
    best, best_d = None, float("inf")
    for p in players:
        d = _ball_gap(ball, p)
        if d < best_d:
            best, best_d = p.player, d
    return best, best_d


def raw_holder(obs: FrameObs) -> tuple[Optional[PlayerRef], float]:
    """单帧持球人候选 + 置信度。球不可见或无人够近 → (None, 0)。"""
    if obs.ball is None or not obs.players:
        return None, 0.0
    player, d = _nearest(obs.ball, obs.players)
    if player is None or d > POSSESSION_RADIUS_PX:
        return None, 0.0
    conf = max(0.5, 1.0 - d / POSSESSION_RADIUS_PX * 0.5)
    return player, conf


def holder_segments(obs_list: list[FrameObs]) -> list[HolderSeg]:
    """逐帧候选 → 防抖后的连续持球段。换手须连续 HOLD_FRAMES 帧同一人。
    球不可见（运球遮挡是常态）时在 CARRY_MAX_S 内顺延当前持球人，置信度衰减。"""
    segs: list[HolderSeg] = []
    cur: Optional[HolderSeg] = None
    pending: Optional[PlayerRef] = None
    pending_n = 0
    last_seen = -1e9            # 当前持球人最后一次被球证实的时刻

    for obs in obs_list:
        cand, conf = raw_holder(obs)
        cur_key = (cur.holder.team, cur.holder.number) if cur else None
        cand_key = (cand.team, cand.number) if cand else None

        if cand_key == cur_key and cur is not None:
            pending, pending_n = None, 0
            cur.t_end = obs.t_sec
            cur.confidence = max(cur.confidence, conf)
            last_seen = obs.t_sec
            continue

        if cand is None:
            # 球不可见：顺延当前持球人，但只在 CARRY_MAX_S 窗口内
            if cur is not None and obs.t_sec - last_seen <= CARRY_MAX_S:
                cur.t_end = obs.t_sec
                cur.confidence *= CARRY_DECAY
            pending, pending_n = None, 0
            continue

        if pending and (pending.team, pending.number) == cand_key:
            pending_n += 1
        else:
            pending, pending_n = cand, 1

        if pending_n >= HOLD_FRAMES:
            segs.append(HolderSeg(holder=cand, t_start=obs.t_sec, t_end=obs.t_sec,
                                  confidence=conf))
            cur, pending, pending_n = segs[-1], None, 0
            last_seen = obs.t_sec

    return segs
