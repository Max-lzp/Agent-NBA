"""从 Textract 的 LINE 里找比分、节次和比赛时钟。解析失败不抛错。"""

from __future__ import annotations

import re

_QUARTER = re.compile(r"\b(Q[1-4]|OT)\b", re.IGNORECASE)
_CLOCK = re.compile(r"\b(\d{1,2}:\d{2})\b")
_NUMBER = re.compile(r"^\d{1,3}$")


def parse_scoreboard(blocks: list[dict]) -> dict:
    lines = _lines(blocks)
    quarter = ""
    clock = ""
    for line in lines:
        if not quarter:
            match = _QUARTER.search(line["text"])
            if match:
                quarter = match.group(1).upper()
        if not clock:
            match = _CLOCK.search(line["text"])
            if match:
                clock = match.group(1)

    digits = [line for line in lines if _NUMBER.match(line["text"])]
    digits.sort(key=lambda line: (-line["height"], line["left"]))
    pair = digits[:2]
    if len(pair) < 2:
        return {
            "home": None,
            "away": None,
            "quarter": quarter,
            "clock": clock,
            "ocr_confidence": 0,
        }
    ordered = sorted(pair, key=lambda line: line["left"])
    confidence = 0.8 if quarter and clock else 0.4
    return {
        "home": int(ordered[0]["text"]),
        "away": int(ordered[1]["text"]),
        "quarter": quarter,
        "clock": clock,
        "ocr_confidence": confidence,
    }


def _lines(blocks: list[dict]) -> list[dict]:
    lines = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        kind = block.get("BlockType") or block.get("blockType")
        if kind not in (None, "LINE"):
            continue
        text = str(block.get("Text") or block.get("text") or "").strip()
        if not text:
            continue
        box = (block.get("Geometry") or {}).get("BoundingBox") or {}
        lines.append(
            {
                "text": text,
                "height": float(box.get("Height") or block.get("height") or 0),
                "left": float(box.get("Left") or block.get("left") or 0),
            }
        )
    return lines
