"""出手检测（骨架）。

两条线索交叉验证，单线索不硬出：
1. 球轨迹：球与篮筐水平距离收敛 + 持球段恰好中断（full 模式主力）
2. 记分牌跳变：Textract 检出比分变化，向前回溯 ±SCORE_LOOKBACK_S 内
   最后一个持球人记为 shooter（vlm 降级模式兜底）

出手事件的附加值（评审最看重的部分）：
- def_dist_ft：出手瞬间最近防守人距离（对应官方 DEF DIST 口径）
- shot_zone：出手区域（geometry.shot_zone）
- 是否命中：记分牌跳变方向 / 球过筐方向
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .geometry import dist_ft, shot_zone, qual_distance
from .possession import FrameObs, HolderSeg
from .schema import Event, Geometry, PlayerRef

SCORE_LOOKBACK_S = 3.0     # 从球进的时刻（进攻钟重置，没有就用比分跳变）往前找出手人的窗口
SHOT_TO_MADE_S = 1.0       # 出手到球进（进攻钟重置）最多隔多久；最后看清持球更早时，出手时刻按这个往回推
RIM_LOCK_RADIUS_FT = 5.0   # 球进入篮筐此半径内视为出手迹象
MIN_CONF = 0.6             # 出手事件硬门槛，低于此不出


@dataclass
class ScoreTick:
    """一次记分牌变化（pipeline/vision/scoreboard.py 或 lambdas/ocr_scoreboard）。"""
    t_sec: float
    team: str
    delta: int               # 2 或 3
    t_reset: Optional[float] = None   # 比分跳变前最近一次进攻钟重置：球进的那一刻，比分常晚 1–2 秒才更新

    @property
    def t_made(self) -> float:
        return self.t_reset if self.t_reset is not None else self.t_sec


def _nearest_defender(obs: FrameObs, shooter: PlayerRef) -> tuple[Optional[PlayerXY_like], float]:
    """出手帧上离 shooter 最近的对方球员。"""
    best, best_d = None, float("inf")
    for p in obs.players:
        if p.player.team == shooter.team:
            continue
        d = dist_ft((p.x, p.y), (_sx, _sy)) if False else None  # 占位，见下
    # TODO(填肉): 用 obs.players 中射手本人的坐标做基准计算最近防守人；
    # obs.calibration == "none" 时改走 qual_distance(像素距, 身高像素)。
    raise NotImplementedError


def detect_shots(possession_id: str,
                 obs_list: list[FrameObs],
                 holder_segs: list[HolderSeg],
                 score_ticks: list[ScoreTick],
                 start_index: int) -> list[Event]:
    """骨架：先只实现记分牌跳变路径（vlm 模式可跑通）；
    球轨迹路径留给 full 模式填肉。"""
    events: list[Event] = []
    n = start_index

    for tick in score_ticks:
        shooter: Optional[PlayerRef] = None
        t_shot: Optional[float] = None
        for seg in reversed(holder_segs):          # 球进之前最后一个持球人
            if seg.t_start <= tick.t_made and tick.t_made - seg.t_end <= SCORE_LOOKBACK_S:
                shooter = seg.holder
                t_shot = max(min(seg.t_end, tick.t_made), tick.t_made - SHOT_TO_MADE_S)
                break
        if shooter is None or shooter.team != tick.team:
            continue
        conf = 0.7 if tick.delta in (2, 3) else 0.55
        if conf < MIN_CONF:
            continue
        events.append(Event(
            event_id=f"{possession_id}-E{n}", t=t_shot, type="shot",
            actor=shooter, confidence=conf,
            geometry=Geometry()))                  # def_dist / zone 由几何层回填
        n += 1

    return events


# 类型占位，避免骨架引入循环依赖；实现时换成 possession.PlayerXY
class PlayerXY_like:  # pragma: no cover
    player: PlayerRef
    x: float
    y: float
    height_px: float
