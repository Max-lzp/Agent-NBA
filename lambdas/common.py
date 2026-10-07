"""Lambda 共用：HTTP 响应、事件 JSON、对象键。只依赖标准库。"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

EVENT_TYPES = {"shot", "rebound", "turnover", "pass", "screen", "hold"}

SYSTEM_PROMPT = (
    "你是 Deep Court 的比赛事件分析器。只输出一个 JSON 对象，不要 Markdown。"
    "格式："
    '{"events":[{"eventType":"shot|rebound|turnover|pass|screen|hold",'
    '"timecode":"MM:SS","players":[],"confidence":0.0,"label":"短标签","detail":""}]}'
    "规则：有画面就只根据画面；没有画面时只能依据 gameMeta 或文件名做草稿，"
    "confidence 不得超过 0.4，detail 必须以「未抽帧」开头。"
    "不要编造输入里没出现的 xFG%、GRAV 或球员全名。"
    "事件 3 到 8 条，按时间排序。"
)


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def json_response(status: int, payload: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json; charset=utf-8",
            "access-control-allow-origin": "*",
        },
        "body": json.dumps(payload, ensure_ascii=False),
    }


def parse_body(event: dict) -> dict:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64

        raw = base64.b64decode(raw).decode("utf-8")
    if isinstance(raw, dict):
        return raw
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("body must be a JSON object")
    return data


def extract_json_object(text: str) -> dict:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("model did not return JSON")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("model JSON must be an object")
    return data


def safe_filename(name: str) -> str:
    base = (name or "clip.mp4").replace("\\", "/").split("/")[-1].strip()
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip(".-")
    if not base:
        base = "clip.mp4"
    return base[:120]


def new_job_id() -> str:
    return str(uuid.uuid4())


def object_key(job_id: str, filename: str) -> str:
    return f"raw/{job_id}/{safe_filename(filename)}"


def normalize_events(data: dict) -> list[dict]:
    raw_events = data.get("events") if isinstance(data, dict) else None
    if not isinstance(raw_events, list):
        raise ValueError("events must be a list")
    clean: list[dict] = []
    for ev in raw_events[:8]:
        if not isinstance(ev, dict):
            continue
        kind = str(ev.get("eventType") or "hold")
        if kind not in EVENT_TYPES:
            kind = "hold"
        players = ev.get("players") if isinstance(ev.get("players"), list) else []
        try:
            confidence = float(ev.get("confidence") or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        clean.append(
            {
                "eventType": kind,
                "timecode": str(ev.get("timecode") or "")[:16],
                "players": [str(p)[:40] for p in players[:6]],
                "confidence": round(confidence, 3),
                "label": str(ev.get("label") or kind)[:80],
                "detail": str(ev.get("detail") or "")[:240],
            }
        )
    if not clean:
        raise ValueError("no usable events")
    return clean
