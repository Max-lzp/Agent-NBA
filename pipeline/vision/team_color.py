"""用球衣下摆和短裤区分白球衣和深色球衣。

这两段训练视频都是火箭白、独行侠深蓝。
上身平均明度不能用：深色球衣里的白背心、旁边的浅色地板会把明度抬高，
白球衣框进深色前景又会把明度压低。号码识别另走裁切，不在这里做。
"""

from __future__ import annotations

from collections import Counter

import cv2
import numpy as np


def _xyxy(frame: np.ndarray, bbox: list[float]) -> tuple[int, int, int, int] | None:
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = [int(round(v)) for v in bbox]
    if x2 < x1:
        x1, x2 = x2, x1
    if y2 < y1:
        y1, y2 = y2, y1
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(width, x2), min(height, y2)
    if x2 - x1 < 8 or y2 - y1 < 16:
        return None
    return x1, y1, x2, y2


def torso_crop(frame: np.ndarray, bbox: list[float]) -> np.ndarray | None:
    """号码用的躯干裁切，坐标是 xyxy。"""
    box = _xyxy(frame, bbox)
    if box is None:
        return None
    x1, y1, x2, y2 = box
    top = y1 + int((y2 - y1) * 0.18)
    bottom = y1 + int((y2 - y1) * 0.62)
    left = x1 + int((x2 - x1) * 0.12)
    right = x2 - int((x2 - x1) * 0.12)
    crop = frame[top:bottom, left:right]
    if crop.size == 0:
        return None
    return crop


def jersey_color(frame: np.ndarray, bbox: list[float]) -> str | None:
    """看下摆到短裤这一段。bbox 是 xyxy。整个人框太高时多半是误检，不判。"""
    box = _xyxy(frame, bbox)
    if box is None:
        return None
    x1, y1, x2, y2 = box
    body_h = y2 - y1
    body_w = x2 - x1
    if body_h > 700 or body_w > 420:
        return None
    top = y1 + int(body_h * 0.40)
    bottom = y1 + int(body_h * 0.74)
    left = x1 + int(body_w * 0.22)
    right = x2 - int(body_w * 0.22)
    crop = frame[top:bottom, left:right]
    if crop.size == 0 or crop.shape[0] < 8 or crop.shape[1] < 8:
        return None
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hue = hsv[:, :, 0].astype(np.int16)
    saturation = hsv[:, :, 1].astype(np.int16)
    value = hsv[:, :, 2].astype(np.int16)
    court = (hue >= 8) & (hue <= 32) & (value >= 150) & (saturation >= 20) & (saturation <= 170)
    skin = (hue <= 18) & (saturation >= 25) & (saturation <= 140) & (value >= 70) & (value <= 220)
    keep = ~(court | skin)
    if float(keep.mean()) < 0.12:
        keep = np.ones(hue.shape, dtype=bool)
    hue, saturation, value = hue[keep], saturation[keep], value[keep]
    white = float(((value >= 145) & (saturation <= 85)).mean())
    dark = float(((value <= 120) & (saturation >= 20)).mean())
    blue = float(((hue >= 95) & (hue <= 135) & (saturation >= 25) & (value <= 170)).mean())
    dark_score = max(dark, blue)
    if dark_score >= 0.34 and dark_score > white + 0.05:
        return "dark"
    if white >= 0.42 and white > dark_score:
        return "white"
    return None


def stable_color(votes: list[str], minimum: float = 0.65) -> str | None:
    """一条轨迹只有颜色足够一致时才赋球队。跟踪串人时返回 None。"""
    if not votes:
        return None
    color, count = Counter(votes).most_common(1)[0]
    if count / len(votes) < minimum:
        return None
    return color
