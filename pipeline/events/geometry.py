"""几何层：像素 → 球场英尺坐标。

策略（与 docs/event-stream-architecture.md 第六节一致）：
- 只在主机位连续段谈距离；切镜即作废。
- 主机位段内逐帧重解单应矩阵 H；高置信关键点 ≥4 才解，不足沿用上一帧并标 stale。
- 半场坐标系与 stats.nba.com 惯例一致：篮筐中心为原点，单位英尺。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

# ---- 半场常量（英尺，篮筐原点）----
HOOP_FT = (0.0, 0.0)
BACKBOARD_Y = -4.0
THREE_PT_R = 23.75          # 弧顶三分半径
THREE_PT_CORNER_X = 22.0    # 底角三分线横向位置
PAINT_HALF_W = 8.0          # 禁区半宽
PAINT_Y = 19.0              # 罚球线纵坐标
RESTRICTED_R = 4.0          # 合理冲撞区半径（4 ft）

MAX_STALE_FRAMES = 15       # 关键点不足时 H 最多沿用的帧数
MIN_KEYPOINTS = 4           # 解单应所需最少点数


@dataclass
class Calib:
    status: str             # fresh / stale / none
    n_points: int = 0
    # H 留给实现者挂 numpy 矩阵；骨架不引入 numpy 依赖


class HomographyEstimator:
    """逐帧单应估计器骨架。

    实现要点（给填肉的人）：
    1. keypoints_px / keypoints_ft 按同一索引对齐，先按置信度过滤；
    2. cv2.findHomography(src, dst, cv2.RANSAC, 5.0)；
    3. 解不出 → status=stale 并沿用上一帧 H，超过 MAX_STALE_FRAMES → none；
    4. 镜头检测（上游直方图突变）触发 reset()，H 立即作废。
    """

    def __init__(self) -> None:
        self._H = None
        self._stale = 0

    def reset(self) -> None:
        self._H = None
        self._stale = 0

    def update(self,
               keypoints_px: Sequence[tuple[float, float]],
               keypoints_ft: Sequence[tuple[float, float]]) -> Calib:
        # TODO: cv2.findHomography。骨架阶段：点不够直接降级。
        if len(keypoints_px) < MIN_KEYPOINTS:
            self._stale += 1
            if self._H is not None and self._stale <= MAX_STALE_FRAMES:
                return Calib(status="stale", n_points=len(keypoints_px))
            self._H = None
            return Calib(status="none", n_points=len(keypoints_px))
        raise NotImplementedError("接入 cv2.findHomography 后删除此行")

    def to_court(self, x_px: float, y_px: float) -> Optional[tuple[float, float]]:
        """像素点 → (x_ft, y_ft)。无有效 H 返回 None。"""
        if self._H is None:
            return None
        raise NotImplementedError("接入 H 矩阵乘法后删除此行")


def dist_ft(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def qual_distance(px_gap: float, player_height_px: float) -> str:
    """标定失败时的定性档位：用球员身高像素做参照物。
    tight=贴身 / one_step=一步 / two_plus=两步开外。阈值白天用训练视频调。
    """
    if player_height_px <= 0:
        return "two_plus"
    ratio = px_gap / player_height_px
    if ratio < 0.35:
        return "tight"
    if ratio < 0.8:
        return "one_step"
    return "two_plus"


def shot_zone(x_ft: float, y_ft: float) -> str:
    """出手区域，口径对齐 stats.nba.com 常用分区。"""
    r = math.hypot(x_ft - HOOP_FT[0], y_ft - HOOP_FT[1])
    is_three = r > THREE_PT_R or abs(x_ft) >= THREE_PT_CORNER_X
    if is_three:
        if abs(x_ft) >= THREE_PT_CORNER_X and y_ft < 14:
            return "corner3_l" if x_ft < 0 else "corner3_r"
        if abs(x_ft) < 8:
            return "top3"
        return "wing3_l" if x_ft < 0 else "wing3_r"
    if r <= RESTRICTED_R:
        return "restricted"
    if abs(x_ft) <= PAINT_HALF_W and y_ft <= PAINT_Y:
        return "paint"
    return "midrange"
