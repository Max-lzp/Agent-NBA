"""事件流总装：观测 → 结构化 JSON。CLI 入口。

用法：
  # 整段 VLM 过片（analyze_vlm.py 的 results/local/*.json，推荐）
  python -m pipeline.events.build_stream --job-id test01 --mode vlm \
    --obs pipeline/vision/results/local/amen.qwen3_vl.json \
    --frame-map pipeline/vision/results/frame_map_amen.json \
    --out structured_test01.json

  # 单帧探针（results/json/*.json）也能跑，自动识别格式

两种输入模式：
  vlm  —— VLM 帧级观测（球框 + 球员框 → 距离判定持球人，不信任 VLM 的持球结论）
  full —— ByteTrack 追踪输出（pipeline/vision 追踪层），接口已定，实现待填
"""
from __future__ import annotations

import argparse
import json
from typing import Callable, Optional

from .possession import FrameObs, PlayerXY, HolderSeg, holder_segments
from .passes import possession_events
from .shots import ScoreTick, detect_shots
from .schema import (EventStream, Possession, Keyframe, Position,
                     PlayerRef)


# ---------- 装载：results/local（整段过片，timeline 格式） ----------

def load_local_obs(obs_path: str) -> list[FrameObs]:
    """timeline 每行 → FrameObs。球员取脚底点（框底边中点），球取中心点。"""
    with open(obs_path, encoding="utf-8") as f:
        doc = json.load(f)
    obs_list: list[FrameObs] = []
    for row in doc.get("timeline", []):
        players: list[PlayerXY] = []
        for p in row.get("players", []):
            if p.get("role") == "referee":
                continue
            if not p.get("team"):
                continue                       # 没分出队伍的观测不参与持球判定
            box = p.get("bbox")                # [x, y, w, h] 像素
            if not box:
                continue
            players.append(PlayerXY(
                player=PlayerRef(team=p["team"],
                                 number=str(p["jersey"]) if p.get("jersey") else "",
                                 name=p.get("name"), track_id=p.get("track_id")),
                x=box[0] + box[2] / 2,
                y=box[1] + box[3],
                height_px=box[3],
            ))
        ball = None
        if row.get("ball"):
            b = row["ball"]
            ball = (b[0] + b[2] / 2, b[1] + b[3] / 2)
        obs_list.append(FrameObs(t_sec=float(row["t"]), players=players, ball=ball,
                                 calibration="none"))
    obs_list.sort(key=lambda o: o.t_sec)
    return obs_list


# ---------- 装载：results/json（单帧探针，calls 格式） ----------

def load_probe_frames(obs_path: str) -> list[dict]:
    with open(obs_path, encoding="utf-8") as f:
        raw = json.load(f)
    frames = [c["parsed"] for c in raw.get("calls", [])
              if c.get("json_ok") and isinstance(c.get("parsed"), dict)]
    frames.sort(key=lambda p: p.get("frame_t", 0.0))
    return frames


def _find_player(frame: dict, name: Optional[str]) -> Optional[PlayerRef]:
    if not name:
        return None
    for p in frame.get("players", []):
        if p.get("name") == name:
            return PlayerRef(team=p.get("team", ""), number=str(p.get("jersey", "")),
                             name=p.get("name"))
    return None


def segments_from_probe(frames: list[dict]) -> list[HolderSeg]:
    """探针格式没有球框，只能信任 VLM 的 ball_handler 字段。"""
    segs: list[HolderSeg] = []
    cur: Optional[HolderSeg] = None
    cur_key = None
    for fr in frames:
        holder = _find_player(fr, fr.get("ball_handler"))
        t = float(fr.get("frame_t", 0.0))
        key = (holder.team, holder.number) if holder else None
        if key and key == cur_key and cur:
            cur.t_end = t
            continue
        if key:
            segs.append(HolderSeg(holder=holder, t_start=t, t_end=t, confidence=0.6))
            cur, cur_key = segs[-1], key
        else:
            cur, cur_key = None, None
    return segs


def ticks_from_probe(frames: list[dict]) -> list[ScoreTick]:
    ticks: list[ScoreTick] = []
    prev: Optional[dict] = None
    for fr in frames:
        sb = fr.get("scoreboard") or {}
        t = float(fr.get("frame_t", 0.0))
        if prev is not None:
            for side in ("home", "away"):
                d = sb.get(side, 0) - prev.get(side, 0)
                if d in (2, 3):
                    ticks.append(ScoreTick(t_sec=t, team=sb.get(f"{side}_team", ""), delta=d))
        prev = sb
    return ticks


# ---------- full 模式装载 ----------

def load_full_obs(tracks_path: str) -> list[FrameObs]:
    """ByteTrack 输出 → FrameObs 序列。追踪层定稿后实现。"""
    raise NotImplementedError("等 pipeline/vision 追踪层输出格式定稿")


# ---------- 关键帧 ----------

def keyframe_from_obs(obs: Optional[FrameObs], holder: Optional[PlayerRef], why: str) -> Optional[Keyframe]:
    if obs is None:
        return None
    positions: list[Position] = []
    for p in obs.players:
        if holder and (p.player.team, p.player.number) == (holder.team, holder.number):
            role = "ballhandler"
        elif holder and p.player.team == holder.team:
            role = "off_ball"
        else:
            role = "help_defender"      # on_ball_defender 判定留 full 模式的几何层
        positions.append(Position(player=p.player, role=role, confidence=0.5))
    return Keyframe(t=obs.t_sec, why=why, calibration=obs.calibration, positions=positions)


# ---------- 总装 ----------

def build(job_id: str, mode: str,
          segs: list[HolderSeg],
          ticks: list[ScoreTick],
          obs_list: list[FrameObs],
          video_meta: dict) -> EventStream:
    stream = EventStream(job_id=job_id, video=video_meta, mode=mode)
    poss: Optional[Possession] = None
    poss_segs: list[HolderSeg] = []
    p_idx = 0

    def holder_at(t: float) -> Optional[PlayerRef]:
        for s in poss_segs:
            if s.t_start <= t <= s.t_end:
                return s.holder
        return None

    def flush(end_t: float) -> None:
        nonlocal poss, poss_segs, p_idx
        if poss is None:
            return
        poss.t_end = end_t
        poss.events.extend(possession_events(poss.possession_id, poss_segs))
        poss.events.extend(detect_shots(poss.possession_id, obs_list, poss_segs, ticks,
                                        start_index=len(poss.events) + 1))
        poss.events.sort(key=lambda e: e.t)
        for i, e in enumerate(poss.events, 1):
            e.event_id = f"{poss.possession_id}-E{i}"
            obs = min(obs_list, key=lambda o: abs(o.t_sec - e.t), default=None)
            kf = keyframe_from_obs(obs, holder_at(e.t), e.type)
            if kf:
                poss.keyframes.append(kf)
        stream.possessions.append(poss)
        poss, poss_segs = None, []

    for seg in segs:
        if poss is None or seg.holder.team != poss.offense_team:
            flush(end_t=seg.t_start)
            p_idx += 1
            poss = Possession(possession_id=f"P{p_idx:02d}", t_start=seg.t_start,
                              t_end=seg.t_end, offense_team=seg.holder.team)
            poss_segs = []
        poss_segs.append(seg)
    flush(end_t=segs[-1].t_end if segs else 0.0)

    return stream


def main() -> None:
    ap = argparse.ArgumentParser(description="观测 → 事件流结构化 JSON")
    ap.add_argument("--job-id", required=True)
    ap.add_argument("--mode", choices=["vlm", "full"], default="vlm")
    ap.add_argument("--obs", required=True,
                    help="vlm: results/local/*.json 或 results/json/*.json（自动识别）；full: 追踪输出")
    ap.add_argument("--frame-map", help="frame_map_*.json，取视频元信息")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    video_meta: dict = {"duration_s": 0.0, "fps": 0.0}
    if args.frame_map:
        with open(args.frame_map, encoding="utf-8") as f:
            fm = json.load(f)
        video_meta.update({"source": fm.get("video", ""), "game_id": fm.get("game_id", ""),
                           "duration_s": fm.get("duration_sec", 0.0), "fps": fm.get("fps", 0.0)})

    if args.mode == "full":
        load_full_obs(args.obs)               # 抛 NotImplementedError
        return

    with open(args.obs, encoding="utf-8") as f:
        sniff = json.load(f)
    if "timeline" in sniff:                   # results/local 整段过片
        obs_list = load_local_obs(args.obs)
        segs = holder_segments(obs_list)
        ticks: list[ScoreTick] = []           # local 格式无记分牌，出手检测待 full 模式
        video_meta.setdefault("fps", sniff.get("sampled_fps") or 0.0)
        video_meta["duration_s"] = video_meta["duration_s"] or sniff.get("duration_sec", 0.0)
    else:                                     # results/json 单帧探针
        frames = load_probe_frames(args.obs)
        obs_list = []
        segs = segments_from_probe(frames)
        ticks = ticks_from_probe(frames)

    stream = build(args.job_id, args.mode, segs, ticks, obs_list, video_meta)
    stream.write(args.out)
    print(f"[build_stream] {args.out}: {len(stream.possessions)} possessions, "
          f"{sum(len(p.events) for p in stream.possessions)} events, "
          f"{sum(len(p.keyframes) for p in stream.possessions)} keyframes")


if __name__ == "__main__":
    main()
