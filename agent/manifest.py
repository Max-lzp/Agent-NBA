"""事件清单格式校验。规则与原先 AgentCore 入口一致。"""

from __future__ import annotations

SCHEMA_VERSION = "0.1"
TEAMS = {"HOU", "DAL"}


def check(stream) -> list[str]:
    if not isinstance(stream, dict):
        return ["清单不是 JSON 对象"]
    problems = []
    if stream.get("version") != SCHEMA_VERSION:
        problems.append(f"version 应为 {SCHEMA_VERSION}")
    possessions = stream.get("possessions")
    if not isinstance(possessions, list) or not possessions:
        return problems + ["possessions 为空"]
    for poss in possessions:
        if poss.get("offense_team") not in TEAMS:
            problems.append(f"{poss.get('possession_id')} 进攻方不是 HOU/DAL")
        if not isinstance(poss.get("events"), list):
            problems.append(f"{poss.get('possession_id')} 缺少 events")
    return problems


def game_id_of(stream: dict | None) -> str:
    if not isinstance(stream, dict):
        return ""
    video = stream.get("video") if isinstance(stream.get("video"), dict) else {}
    official = stream.get("official") if isinstance(stream.get("official"), dict) else {}
    return str(
        stream.get("gameId")
        or stream.get("game_id")
        or video.get("game_id")
        or official.get("game_id")
        or ""
    )


def job_id_of(stream: dict | None, fallback: str = "") -> str:
    if not isinstance(stream, dict):
        return fallback
    return str(stream.get("jobId") or stream.get("job_id") or fallback)
