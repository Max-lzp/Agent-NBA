"""掩护几何谓词：战术识别的地基。

screen_set 候选（三条同时成立）：
  1. 非持球进攻球员 S 与持球人的防守者 D 距离 < SCREEN_DIST_FT
  2. S 自身移动速度 < STATIONARY_FT_S（站住了才叫掩护）
  3. 持续 >= MIN_HOLD_S

screen_use（掩护被利用）：
  screen_set 后 USE_WINDOW_S 内，持球人与 S 的距离先降后升
  （贴着掩护绕过去），且 S 与 D 的相对位置发生交换。

这些谓词的输出只是「候选 + 置信度」，战术命名交给
match_tactics（规则清单 + Claude 仲裁），见 docs/event-stream-architecture.md 第八节。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .geometry import dist_ft
from .possession import FrameObs, PlayerXY
from .schema import Event, PlayerRef

SCREEN_DIST_FT = 3.5       # 掩护人与被掩护防守者的距离阈值
STATIONARY_FT_S = 1.5      # 掩护人速度上限
MIN_HOLD_S = 0.5           # 持续时长下限
USE_WINDOW_S = 1.5         # 掩护后利用窗口
MIN_CONF = 0.55


@dataclass
class ScreenCandidate:
    t: float
    screener: PlayerRef
    screened_defender: PlayerRef
    confidence: float


def _speed_ft_s(trail: list[tuple[float, float, float]]) -> float:
    """trail: [(t, x_ft, y_ft), ...] 近 N 帧轨迹 → 平均速度。"""
    if len(trail) < 2:
        return float("inf")
    (t0, x0, y0), (t1, x1, y1) = trail[0], trail[-1]
    dt = t1 - t0
    return dist_ft((x0, y0), (x1, y1)) / dt if dt > 0 else float("inf")


def detect_screens(possession_id: str,
                   obs_list: list[FrameObs],
                   ballhandler_segs,
                   start_index: int) -> list[Event]:
    """骨架：接口与阈值定死，判定循环留填肉。

    实现步骤（给填肉的人）：
    1. 按持球段确定每帧的持球人与其最近防守者 D；
    2. 对其他四名进攻球员维护近 1s 轨迹窗，过 _speed_ft_s；
    3. 满足三条谓词且持续达标 → ScreenCandidate；
    4. USE_WINDOW_S 内持球人轨迹与 screener 距离先降后升 → 升 screen_use。
    注意：obs.calibration == "none" 的帧跳过（没有英尺坐标不谈掩护）。
    """
    raise NotImplementedError("三条谓词的判定循环留待实现，常量已标定初值")
