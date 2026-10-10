"""把数据卡放到画面空地上。

``place_cards(frame, player_boxes, cards, side_hint)`` 不调用模型。画面切成小格，
扣掉球员框（外扩一圈）、记分牌和边缘，再在剩下的格子里找颜色最均匀、面积最大的矩形。
``side_hint`` 指定每张卡优先左半边还是右半边。放不下就缩小一档，还不行就退到该侧上角。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

CELL = 16
EDGE = 12
BOX_PAD = 14
SCALES = (1.0, 0.85, 0.72, 0.58, 0.46, 0.36)


def _array(frame):
    if isinstance(frame, np.ndarray):
        image = frame
    else:
        from PIL import Image

        if not isinstance(frame, Image.Image):
            frame = Image.open(frame)
        image = np.asarray(frame.convert("RGB"))
    if image.ndim == 2:
        image = np.repeat(image[:, :, None], 3, axis=2)
    return image[:, :, :3]


def _box_xywh(box):
    if box is None:
        return None
    if isinstance(box, dict):
        if all(key in box for key in ("x", "y", "w", "h")):
            return (float(box["x"]), float(box["y"]), float(box["w"]), float(box["h"]))
        if all(key in box for key in ("x1", "y1", "x2", "y2")):
            return (float(box["x1"]), float(box["y1"]), float(box["x2"]) - float(box["x1"]), float(box["y2"]) - float(box["y1"]))
        return None
    if len(box) < 4:
        return None
    return tuple(float(value) for value in box[:4])


def _features(image, cell):
    height, width = image.shape[:2]
    rows, cols = height // cell, width // cell
    if rows < 2 or cols < 2:
        blank = np.zeros((1, 1), dtype=np.float32)
        return blank, np.zeros((1, 1), dtype=bool)
    crop = image[: rows * cell, : cols * cell]
    blocks = crop.reshape(rows, cell, cols, cell, 3).astype(np.float32)
    mean = blocks.mean(axis=(1, 3))
    std = blocks.std(axis=(1, 3)).mean(axis=2)
    lum = mean.mean(axis=2)
    chroma = mean.max(axis=2) - mean.min(axis=2)
    # 地板是浅色、低饱和。看台偏暗，球衣偏红或高饱和，都不当空地。
    floor = (lum > 112) & (std < 40) & (chroma < 62)
    loose = (lum > 96) & (std < 58) & (chroma < 88)
    return std.astype(np.float32), _close(floor, 1) & loose


def _mark_rect(mask, x, y, w, h, cell, value=True):
    rows, cols = mask.shape
    x1 = max(0, int(x // cell))
    y1 = max(0, int(y // cell))
    x2 = min(cols, int(np.ceil((x + w) / cell)))
    y2 = min(rows, int(np.ceil((y + h) / cell)))
    if x2 > x1 and y2 > y1:
        mask[y1:y2, x1:x2] = value


def _shift_or(mask, dy, dx):
    out = np.zeros_like(mask)
    height, width = mask.shape
    y1, y2 = max(0, dy), height + min(0, dy)
    x1, x2 = max(0, dx), width + min(0, dx)
    src_y1 = max(0, -dy)
    src_x1 = max(0, -dx)
    out[y1:y2, x1:x2] = mask[src_y1:src_y1 + (y2 - y1), src_x1:src_x1 + (x2 - x1)]
    return out


def _dilate(mask, radius=1):
    out = mask.copy()
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dy or dx:
                out |= _shift_or(mask, dy, dx)
    return out


def _close(mask, radius=1):
    """补上球场线这种一格宽的缝，避免空地被切碎。硬遮罩之外的区域不会被补上。"""
    return ~_dilate(~_dilate(mask, radius), radius)


def _candidates(free, limit=8):
    """面积最大的若干全真矩形，外加偏宽的，方便横卡落进空地。"""
    height, width = free.shape
    hist = [0] * width
    found = []
    for row in range(height):
        for col in range(width):
            hist[col] = hist[col] + 1 if free[row, col] else 0
        stack = [-1]
        extended = hist + [0]
        for col, bar in enumerate(extended):
            while stack[-1] != -1 and extended[stack[-1]] >= bar:
                bar_h = extended[stack.pop()]
                left = stack[-1]
                bar_w = col - left - 1
                if bar_h >= 4 and bar_w >= 6:
                    found.append((bar_w * bar_h, left + 1, row - bar_h + 1, bar_w, bar_h))
            stack.append(col)
    found.sort(reverse=True)
    kept = []
    for area, x, y, w, h in found:
        if any(abs(x - ox) < 3 and abs(y - oy) < 3 and abs(w - ow) < 3 for _a, ox, oy, ow, _oh in kept):
            continue
        kept.append((area, x, y, w, h))
        if len(kept) >= limit:
            break
    return [(x, y, w, h) for _area, x, y, w, h in kept]
    """二值图里面积最大的全真矩形。返回 (x, y, w, h)，单位是格子。"""
    height, width = free.shape
    hist = [0] * width
    best = (0, 0, 0, 0)
    best_area = 0
    for row in range(height):
        for col in range(width):
            hist[col] = hist[col] + 1 if free[row, col] else 0
        stack = [-1]
        extended = hist + [0]
        for col, bar in enumerate(extended):
            while stack[-1] != -1 and extended[stack[-1]] >= bar:
                bar_h = extended[stack.pop()]
                left = stack[-1]
                bar_w = col - left - 1
                area = bar_h * bar_w
                if area > best_area:
                    best_area = area
                    best = (left + 1, row - bar_h + 1, bar_w, bar_h)
            stack.append(col)
    return best


def _side_of(card, index, side_hint):
    name = str(card.get("id") or index)
    if isinstance(side_hint, dict):
        chosen = side_hint.get(name) or side_hint.get(card.get("id"))
        if chosen in {"left", "right"}:
            return chosen
    if side_hint in {"left", "right"}:
        return side_hint
    if card.get("side") in {"left", "right"}:
        return card["side"]
    return "left" if index % 2 == 0 else "right"


def _search(std, floor, blocked, side, card_w, card_h, cell, frame_w):
    cols = std.shape[1]
    mid = max(1, cols // 2)
    best = None
    masks = []
    if floor is not None:
        masks.append(floor & ~blocked)
    for percentile in (42, 58):
        limit = float(np.percentile(std, percentile))
        masks.append(_close((std <= limit) & ~blocked, 1) & ~blocked)
    for free in masks:
        region = free.copy()
        if side == "left":
            region[:, mid:] = False
        else:
            region[:, :mid] = False
        rects = _candidates(region)
        if not rects:
            continue
        for x, y, w, h in rects:
            px, py, pw, ph = x * cell, y * cell, w * cell, h * cell
            for scale in SCALES:
                width = max(1, int(card_w * scale))
                height = max(1, int(card_h * scale))
                if width > pw or height > ph:
                    continue
                origin = px if side == "left" else min(px + pw - width, frame_w - width - EDGE)
                half = frame_w / 2
                if side == "left":
                    origin = min(max(EDGE, origin), max(EDGE, half - width))
                else:
                    origin = max(origin, half)
                origin = int(max(EDGE, min(origin, frame_w - width - EDGE)))
                step = max(cell, 8)
                chosen = None
                for top in range(int(py), int(py + ph - height) + 1, step):
                    y1, y2 = max(0, top // cell), min(std.shape[0], max(top // cell + 1, (top + height) // cell))
                    x1, x2 = max(0, origin // cell), min(std.shape[1], max(origin // cell + 1, (origin + width) // cell))
                    patch = std[y1:y2, x1:x2]
                    cost = float(patch.mean()) if patch.size else 999.0
                    cover = float(floor[y1:y2, x1:x2].mean()) if floor is not None and patch.size else 1.0
                    if cover < 0.72:
                        continue
                    if chosen is None or cost < chosen[0]:
                        chosen = (cost, top, cover)
                if chosen is None:
                    continue
                score = -chosen[0] * 8 + scale * 12 + chosen[2] * 20
                if best is None or score > best[0]:
                    best = (score, origin, int(chosen[1]), width, height, scale)
                break
    if best is None:
        return None
    return best[1:]


def _fallback(side, card_w, card_h, frame_w, frame_h, taken):
    scale = SCALES[-1]
    width = max(1, int(card_w * scale))
    height = max(1, int(card_h * scale))
    half = frame_w / 2
    if side == "left":
        x = EDGE
        width = min(width, int(half) - 2 * EDGE)
    else:
        width = min(width, int(half) - 2 * EDGE)
        x = frame_w - EDGE - width
    y = EDGE
    for _ in range(6):
        rect = (x, y, width, height)
        if not any(_hit(rect, other) for other in taken):
            break
        y += height // 3 + 4
        if y + height > frame_h - EDGE:
            y = EDGE
            break
    return int(x), int(y), int(width), int(height), scale


def _hit(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def basket_side(frame):
    """篮筐大概在画面左边还是右边。看上半区橙色篮圈哪一边更集中，分不清就返回 None。"""
    image = _array(frame)
    height, width = image.shape[:2]
    top = image[: max(1, int(height * 0.42))]
    red, green, blue = top[:, :, 0], top[:, :, 1], top[:, :, 2]
    orange = (red > 150) & (green > 50) & (green < 175) & (blue < 100) & (red > green + 28) & (red > blue + 45)
    total = int(orange.sum())
    if total < 25:
        return None
    left = int(orange[:, : width // 2].sum())
    right = total - left
    if max(left, right) / total < 0.62:
        return None
    return "left" if left > right else "right"


def place_cards(frame, player_boxes, cards, side_hint=None, scorebug_boxes=None):
    """返回与 cards 对齐的位置。每项含 id、x、y、w、h、scale、side。坐标是帧像素。"""
    image = _array(frame)
    frame_h, frame_w = image.shape[:2]
    cell = 8 if frame_w >= 320 else 4
    std, floor = _features(image, cell)
    blocked = np.zeros(std.shape, dtype=bool)
    _mark_rect(blocked, 0, 0, frame_w, EDGE, cell)
    _mark_rect(blocked, 0, frame_h - EDGE, frame_w, EDGE, cell)
    _mark_rect(blocked, 0, 0, EDGE, frame_h, cell)
    _mark_rect(blocked, frame_w - EDGE, 0, EDGE, frame_h, cell)
    for box in player_boxes or []:
        xywh = _box_xywh(box)
        if not xywh:
            continue
        x, y, w, h = xywh
        _mark_rect(blocked, x - BOX_PAD, y - BOX_PAD, w + 2 * BOX_PAD, h + 2 * BOX_PAD, cell)
    for box in scorebug_boxes or []:
        xywh = _box_xywh(box)
        if xywh:
            _mark_rect(blocked, *xywh, cell)

    placed = []
    taken = []
    for index, card in enumerate(cards or []):
        side = _side_of(card, index, side_hint)
        found = _search(std, floor, blocked, side, float(card.get("w") or 320), float(card.get("h") or 180), cell, frame_w)
        if found is None:
            found = _fallback(side, float(card.get("w") or 320), float(card.get("h") or 180), frame_w, frame_h, taken)
        x, y, w, h, scale = found
        rect = (x, y, w, h)
        taken.append(rect)
        _mark_rect(blocked, x, y, w, h, cell)
        placed.append({
            "id": card.get("id") or index,
            "x": x,
            "y": y,
            "w": w,
            "h": h,
            "scale": scale,
            "side": side,
        })
    return placed


def load_frame(path):
    return _array(Path(path))
