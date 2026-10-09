"""结构化文件的 Python 表达，字段与 event_stream.schema.json 一一对应。

全队合同：上游 vision / VLM 只负责产出观测，下游 enrich / 战术 / 渲染 / 解说
只读这里定义的结构。改字段先改 event_stream.schema.json，再改这里。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Optional

SCHEMA_VERSION = "0.1"


@dataclass
class PlayerRef:
    team: str                 # HOU / DAL
    number: str               # 字符串，保留 "00"
    name: Optional[str] = None
    track_id: Optional[int] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class Geometry:
    x_ft: Optional[float] = None
    y_ft: Optional[float] = None
    def_dist_ft: Optional[float] = None      # 最近防守人距离（英尺）
    def_dist_qual: Optional[str] = None      # 标定失败时的定性档位：tight / one_step / two_plus
    shot_zone: Optional[str] = None
    nearest_defender: Optional[PlayerRef] = None

    def to_dict(self) -> dict:
        d = {k: v for k, v in asdict(self).items() if v is not None}
        if self.nearest_defender:
            d["nearest_defender"] = self.nearest_defender.to_dict()
        return d


EVENT_TYPES = (
    "receive", "pass", "turnover", "screen_set", "screen_use",
    "dribble_handoff", "shot", "rebound", "scoreboard_sync",
)


@dataclass
class Event:
    event_id: str             # P01-E3
    t: float                  # 视频时间（秒）
    type: str                 # EVENT_TYPES 之一
    actor: PlayerRef
    confidence: float
    source: str = "cv"        # cv / vlm / manual
    target: Optional[PlayerRef] = None
    geometry: Optional[Geometry] = None

    def to_dict(self) -> dict:
        d = {"event_id": self.event_id, "t": round(self.t, 3), "type": self.type,
             "actor": self.actor.to_dict(), "confidence": round(self.confidence, 3),
             "source": self.source}
        if self.target:
            d["target"] = self.target.to_dict()
        if self.geometry:
            d["geometry"] = self.geometry.to_dict()
        return d


@dataclass
class Position:
    player: PlayerRef
    role: str                 # ballhandler / on_ball_defender / help_defender / off_ball
    confidence: float
    x_ft: Optional[float] = None
    y_ft: Optional[float] = None

    def to_dict(self) -> dict:
        d = {"player": self.player.to_dict(), "role": self.role,
             "confidence": round(self.confidence, 3)}
        if self.x_ft is not None:
            d["x_ft"] = round(self.x_ft, 2)
            d["y_ft"] = round(self.y_ft, 2)
        return d


@dataclass
class Keyframe:
    t: float
    why: str                  # 触发快照的事件类型
    calibration: str          # fresh / stale / none
    positions: list[Position] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"t": round(self.t, 3), "why": self.why, "calibration": self.calibration,
                "positions": [p.to_dict() for p in self.positions]}


@dataclass
class Possession:
    possession_id: str        # P01
    t_start: float
    t_end: float
    offense_team: str
    events: list[Event] = field(default_factory=list)
    keyframes: list[Keyframe] = field(default_factory=list)
    tactics: list[dict] = field(default_factory=list)   # ④ 战术识别回填

    def to_dict(self) -> dict:
        d = {"possession_id": self.possession_id, "t_start": round(self.t_start, 3),
             "t_end": round(self.t_end, 3), "offense_team": self.offense_team,
             "events": [e.to_dict() for e in self.events],
             "keyframes": [k.to_dict() for k in self.keyframes]}
        if self.tactics:
            d["tactics"] = self.tactics
        return d


@dataclass
class EventStream:
    job_id: str
    video: dict               # source / game_id / duration_s / fps
    mode: str                 # full / vlm
    possessions: list[Possession] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict:
        from datetime import datetime, timezone
        return {
            "version": SCHEMA_VERSION,
            "job_id": self.job_id,
            "video": self.video,
            "possessions": [p.to_dict() for p in self.possessions],
            "meta": {
                "mode": self.mode,
                "generator": "pipeline/events",
                "created_at": datetime.now(timezone.utc).isoformat(),
                **({"notes": self.notes} if self.notes else {}),
            },
        }

    def write(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
