"""Lambda 共用：HTTP 响应、事件 JSON、对象键。只依赖标准库。"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

EVENT_TYPES = {"shot", "rebound", "turnover", "pass", "screen", "hold"}
STREAM_TYPES = {
    "receive": "hold",
    "pass": "pass",
    "turnover": "turnover",
    "shot": "shot",
    "rebound": "rebound",
    "screen_set": "screen",
    "screen_use": "screen",
    "dribble_handoff": "screen",
}
TEAM_ZH = {"HOU": "火箭", "DAL": "独行侠"}


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


def _who(ref: dict | None) -> str:
    if not ref:
        return ""
    if ref.get("name"):
        return str(ref["name"])
    team = TEAM_ZH.get(ref.get("team") or "", ref.get("team") or "")
    return f"{team} {ref['number']} 号" if ref.get("number") else f"{team}球员"


def timeline_from_stream(stream: dict) -> list[dict]:
    """事件流结构化文件（pipeline/events，schema 0.1）→ 回放页的事件列表，按时间排序。"""
    if not isinstance(stream, dict) or not isinstance(stream.get("possessions"), list):
        raise ValueError("不是事件流文件：缺少 possessions")
    rows: list[dict] = []
    for poss in stream["possessions"]:
        offense = TEAM_ZH.get(poss.get("offense_team") or "", poss.get("offense_team") or "")
        for ev in poss.get("events") or []:
            kind = STREAM_TYPES.get(ev.get("type"))
            if not kind:
                continue
            actor, target = _who(ev.get("actor")), _who(ev.get("target"))
            label = {
                "hold": f"{actor} 接球",
                "pass": f"{actor} → {target} 传球",
                "turnover": f"失误 · {actor} → {target}" if target else f"{actor} 失误",
                "shot": f"{actor} 出手",
                "rebound": f"{actor} 篮板",
                "screen": f"{actor} 掩护",
            }[kind]
            official = ev.get("official") or {}
            if kind == "shot" and official.get("made") is not None:
                label = f"{actor} {official.get('value') or 2} 分{'命中' if official['made'] else '不中'}"
                if official.get("assist"):
                    label += f"（{_who(official['assist'])} 助攻）"
                if official.get("xfg") is not None:
                    label += f" · xFG {float(official['xfg']):.0%}"
            t = float(ev.get("t") or 0.0)
            confidence = max(0.0, min(1.0, float(ev.get("confidence") or 0.0)))
            rows.append({
                "eventType": kind,
                "t": round(t, 2),
                "timecode": f"{int(t) // 60:02d}:{int(t) % 60:02d}",
                "players": [name for name in (actor, target) if name],
                "confidence": round(confidence, 3),
                "label": label[:80],
                "detail": f"{poss.get('possession_id', '')} · {offense}进攻 · 置信度 {confidence:.2f}",
                "source": ev.get("source") or "cv",
            })
    rows.sort(key=lambda row: row["t"])
    return rows
