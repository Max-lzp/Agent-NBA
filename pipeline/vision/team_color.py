"""用躯干颜色区分白球衣和深色球衣。这两段训练视频都是火箭白、独行侠深蓝。"""

from __future__ import annotations

import cv2
import numpy as np


def torso_crop(frame: np.ndarray, bbox: list[float]) -> np.ndarray | None:
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = [int(round(v)) for v in bbox]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(width, x2), min(height, y2)
    if x2 - x1 < 8 or y2 - y1 < 16:
        return None
    top = y1 + int((y2 - y1) * 0.15)
    bottom = y1 + int((y2 - y1) * 0.55)
    crop = frame[top:bottom, x1:x2]
    if crop.size == 0:
        return None
    return crop


def jersey_color(frame: np.ndarray, bbox: list[float]) -> str | None:
    crop = torso_crop(frame, bbox)
    if crop is None:
        return None
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    saturation = float(hsv[:, :, 1].mean())
    value = float(hsv[:, :, 2].mean())
    if value >= 130 and saturation <= 90:
        return "white"
    if value < 110:
        return "dark"
    return None
