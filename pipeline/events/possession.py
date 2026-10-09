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
HOLD_FRAMES = 2              # 换手需被真球证实的次数
PENDING_WINDOW_S = 0.6       # 两次证实之间最多隔多久（中间可以是看不见球的帧）
CARRY_MAX_S = 1.5            # 球不可见时持球人最多顺延的秒数（运球遮挡是常态）
REJOIN_S = 3.0               # 顺延断开后同一人在此秒数内重新确认持球，接回原段，不算一次传球
NOISE_CONF = 0.4             # 无球 / 距离超阈值时的置信度
INTERP_BALL_CONF = 0.5       # 球位置是插值补的（非真检测）时的球置信度
CONTEST_GAP_PX = 30          # 对方最近球员离球也在此像素内 → 争抢帧（贴身防守时两框重叠）
CONTEST_SCALE = 0.6          # 争抢帧的持球置信度折扣

SWITCH_HOLD_S = 2.5          # 对方连续持球多久才算球权转换（短于此视为争抢 / 认错人）；10.9 两段训练片扫参定
SWITCH_CONF = 0.55           # 对方持球段平均置信度门槛
OPENING_WINDOW_S = 3.0       # 开场按此窗口内的加权持球时长定首个进攻方
RESET_TOL_S = 1.5            # 进攻钟重置和对方开始持球之间允许的时间差
SCORE_DEAD_S = 2.0           # 得分后此窗口内得分方的持球段忽略（庆祝 / 回放 / 发球前）


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
    ball_conf: float = 1.0                       # 真检测取检测置信度，插值取 INTERP_BALL_CONF
    ball_real: bool = True                       # False = 球位置是前后插值补的，只能延续持球人、不能换人


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
    if player is None or d > POSSESSION_RADIUS_PX or not player.team:
        return None, 0.0
    conf = max(0.5, 1.0 - d / POSSESSION_RADIUS_PX * 0.5) * obs.ball_conf
    if contested(obs, player, d):
        conf *= CONTEST_SCALE
    return player, conf


def contested(obs: FrameObs, player: PlayerRef, d: float) -> bool:
    """对方也有人贴着球：贴身防守时防守人的框常比持球人离球更近，这种帧分不清是哪队拿球。"""
    rival = min((_ball_gap(obs.ball, p) for p in obs.players if p.player.team != player.team),
                default=float("inf"))
    return rival <= d + CONTEST_GAP_PX


def holder_segments(obs_list: list[FrameObs]) -> list[HolderSeg]:
    """逐帧候选 → 防抖后的连续持球段。换手须在 PENDING_WINDOW_S 内被真球证实 HOLD_FRAMES 次，
    中间夹着看不见球的帧不清零。球不可见时在 CARRY_MAX_S 内顺延当前持球人，置信度衰减。"""
    segs: list[HolderSeg] = []
    cur: Optional[HolderSeg] = None
    pending: Optional[PlayerRef] = None
    pending_n = 0
    pending_t0 = pending_last = -1e9
    last_seen = -1e9            # 当前持球人最后一次被球证实的时刻

    for obs in obs_list:
        cand, conf = raw_holder(obs)
        cur_key = cur.holder.key() if cur else None
        cand_key = cand.key() if cand else None
        if cand is not None and cand_key != cur_key:
            if not obs.ball_real:
                cand, cand_key = None, None
            elif cur is not None and cand.team != cur.holder.team and \
                    contested(obs, cand, _nearest(obs.ball, obs.players)[1]):
                cand, cand_key = None, None

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
            if pending and obs.t_sec - pending_last > PENDING_WINDOW_S:
                pending, pending_n = None, 0
            continue

        if pending and pending.key() == cand_key and obs.t_sec - pending_last <= PENDING_WINDOW_S:
            pending_n += 1
        else:
            pending, pending_n, pending_t0 = cand, 1, obs.t_sec
        pending_last = obs.t_sec

        if pending_n >= HOLD_FRAMES:
            if segs and segs[-1].holder.key() == cand_key and pending_t0 - segs[-1].t_end <= REJOIN_S:
                segs[-1].t_end = obs.t_sec
                segs[-1].confidence = max(segs[-1].confidence, conf)
            else:
                if cur is not None:
                    cur.t_end = min(cur.t_end, pending_t0)
                segs.append(HolderSeg(holder=cand, t_start=pending_t0, t_end=obs.t_sec,
                                      confidence=conf))
            cur, pending, pending_n = segs[-1], None, 0
            last_seen = obs.t_sec

    return segs


@dataclass
class Span:
    """一个回合的球权段：offense 从 t_start 到 t_end，只含进攻方自己的持球段。"""
    team: str
    t_start: float
    t_end: float
    segs: list[HolderSeg] = field(default_factory=list)
    ended_by: str = "clip_end"    # score / switch / clip_end


def _opening_team(segs: list[HolderSeg]) -> str:
    t0 = segs[0].t_start
    weight: dict[str, float] = {}
    for s in segs:
        if s.t_start - t0 > OPENING_WINDOW_S:
            break
        weight[s.holder.team] = weight.get(s.holder.team, 0.0) + (s.t_end - s.t_start + 0.1) * s.confidence
    return max(weight, key=weight.get)


def possession_spans(segs: list[HolderSeg], score_ticks: Optional[list] = None,
                     clip_start: Optional[float] = None, clip_end: Optional[float] = None,
                     clock_resets: Optional[list[float]] = None) -> list[Span]:
    """持球段 → 回合。片子总是从某个回合中间切进来，给了 clip_start / clip_end 时首尾回合延到片头片尾。
    持球人换队不等于换回合，只有两种情况才换：
    1. 记分牌跳变（score_ticks，元素需有 t_sec / team）：得分方回合结束，对方接管；
    2. 对方连续持球 ≥ SWITCH_HOLD_S 且平均置信度 ≥ SWITCH_CONF。
       读到了记分牌（clock_resets 不为 None）时，还要求这段时间里进攻钟重置过：进攻钟没重置就没换球权。
    其余对方持球段视为争抢或认错人，丢弃。"""
    if not segs:
        return []
    ticks = sorted(score_ticks or [], key=lambda k: k.t_sec)
    if clock_resets is not None:            # 得分造成的重置交给得分处理，不给持球换边作证
        made = {getattr(k, "t_reset", None) for k in ticks}
        clock_resets = [r for r in clock_resets if r not in made]
    t0 = segs[0].t_start if clip_start is None else min(clip_start, segs[0].t_start)
    spans = [Span(team=_opening_team(segs), t_start=t0, t_end=t0)]
    challenge: list[HolderSeg] = []
    dead_team, dead_until = None, -1e9

    def switch(team: str, t: float, why: str) -> None:
        spans[-1].t_end, spans[-1].ended_by = t, why
        spans.append(Span(team=team, t_start=t, t_end=t))

    for seg in segs:
        while ticks and getattr(ticks[0], "t_made", ticks[0].t_sec) <= seg.t_start:
            tick = ticks.pop(0)
            t_made = getattr(tick, "t_made", tick.t_sec)
            other = next((s.holder.team for s in segs if s.holder.team != tick.team), "")
            if spans[-1].team == tick.team:
                switch(other, t_made, "score")
            dead_team, dead_until = tick.team, t_made + SCORE_DEAD_S
            challenge = []

        cur = spans[-1]
        if seg.holder.team == dead_team and seg.t_start <= dead_until:
            continue
        if seg.holder.team == cur.team:
            challenge = []
            cur.segs.append(seg)
            cur.t_end = seg.t_end
            continue

        challenge.append(seg)
        held = challenge[-1].t_end - challenge[0].t_start
        mean_conf = sum(s.confidence for s in challenge) / len(challenge)
        t_switch = challenge[0].t_start
        if clock_resets is not None:
            hits = [r for r in clock_resets if t_switch - RESET_TOL_S <= r <= seg.t_end + RESET_TOL_S]
            if not hits:
                continue
            t_switch = max(t_switch, hits[0])
        if held >= SWITCH_HOLD_S and mean_conf >= SWITCH_CONF:
            switch(seg.holder.team, t_switch, "switch")
            spans[-1].segs, spans[-1].t_end = list(challenge), seg.t_end
            challenge = []

    for tick in ticks:                    # 片尾的得分：后面已经没有持球段来触发
        t_made = getattr(tick, "t_made", tick.t_sec)
        if clip_end is not None and t_made > clip_end:
            break
        if spans[-1].team == tick.team:
            spans[-1].t_end = max(spans[-1].t_end, t_made)
            switch(next((s.holder.team for s in segs if s.holder.team != tick.team), ""), t_made, "score")

    spans[-1].t_end = max(spans[-1].t_end, segs[-1].t_end, clip_end or 0.0)
    return [s for i, s in enumerate(spans)
            if s.segs or s.ended_by == "score" or (i > 0 and spans[i - 1].ended_by == "score")]
