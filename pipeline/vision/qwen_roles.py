#!/usr/bin/env python3
"""Qwen3 VL 只做三件事，而且每次都是单帧。

1. 读记分牌时钟
2. 判一个战术事件
3. 兜底本地号码没读出来的难帧

不要把这个模块放进逐帧循环。
"""

from __future__ import annotations

import json

from converse_frame import converse

SCOREBOARD_PROMPT = """只读这一帧转播记分牌。只输出一个 JSON 对象，不要解释。
{"period": null, "clock": null, "home_team": null, "away_team": null, "home": null, "away": null}
clock 用 M:SS 或 MM:SS。看不清的字段写 null。"""

EVENT_PROMPT = """这是一帧 NBA 转播画面。只判断这一刻最主要的事件。
只输出一个 JSON 对象，不要解释。
{"type": "shot|pass|drive|screen|rebound|none", "actor_jersey": null, "actor_team": "DAL|HOU|null"}
看不清就 type=none，不要猜球星名字。"""

FALLBACK_PROMPT = """这是一名场上球员的裁切图。只读球衣号码和球衣主色。
只输出一个 JSON 对象，不要解释。
{"jersey": null, "color": null}
color 只能填 white、dark 或 null 三者之一，不要把选项用竖线连在一起。
号码必须是字符串。看不清就 jersey 和 color 都填 null，不要猜姓名。"""


def _json_only(text: str) -> dict:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        return {"raw": text, "json_ok": False}
    return json.loads(text[start : end + 1])


def read_scoreboard(image: Path) -> dict:
    result = converse(image, SCOREBOARD_PROMPT, system_text=None, max_tokens=200)
    result["parsed"] = _json_only(result["text"])
    result["role"] = "scoreboard"
    return result


def judge_event(image: Path) -> dict:
    result = converse(image, EVENT_PROMPT, system_text=None, max_tokens=200)
    result["parsed"] = _json_only(result["text"])
    result["role"] = "tactical_event"
    return result


def fallback_number(image: Path) -> dict:
    result = converse(image, FALLBACK_PROMPT, system_text=None, max_tokens=120)
    result["parsed"] = _json_only(result["text"])
    result["role"] = "hard_frame"
    return result
