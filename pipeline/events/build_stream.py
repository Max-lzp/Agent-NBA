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
  full —— 本地追踪输出（pipeline/vision/analyze.py），身份按轨迹查赛季名单
"""
from __future__ import annotations

import argparse
import json
from typing import Callable, Optional

from .possession import (FrameObs, PlayerXY, HolderSeg, INTERP_BALL_CONF,
                         holder_segments, possession_spans)
from .identity import resolve as resolve_identities
from .passes import possession_events
from .shots import ScoreTick, detect_shots
from .schema import (Event, EventStream, Possession, Keyframe, Position,
                     PlayerRef)

TURNOVER_CONF_SCALE = 0.8   # 无出手证据的球权转换（可能是篮板）记 turnover 时的置信度折扣


# ---------- 装载：results/local（整段过片，timeline 格式） ----------

def load_ball_raw(path: str) -> list[tuple[float, Optional[dict]]]:
    """pipeline/vision/ball_raw.py 的产物：逐帧 (t, {bbox, conf} | None)。"""
    with open(path, encoding="utf-8") as f:
        return [(fr["t"], fr["ball"]) for fr in json.load(f)["frames"]]


def load_local_obs(obs_path: str, ball_raw_path: Optional[str] = None,
                   identities: Optional[dict[int, tuple[PlayerRef, float]]] = None) -> list[FrameObs]:
    """timeline 每行 → FrameObs。球员取脚底点（框底边中点），球取中心点。
    给了 ball_raw 时，真检测帧按检测分数折到 0.75–1.0（ball_raw 已滤掉 0.5 以下），
    其余（插值补的）降到 INTERP_BALL_CONF 并标 ball_real=False。
    给了 identities（identity.resolve 的结果）时，按轨迹换成名单身份，不用帧上的 name / jersey。"""
    with open(obs_path, encoding="utf-8") as f:
        doc = json.load(f)
    raw = load_ball_raw(ball_raw_path) if ball_raw_path else None
    obs_list: list[FrameObs] = []
    for row in doc.get("timeline", []):
        players: list[PlayerXY] = []
        for p in row.get("players", []):
            if p.get("role") == "referee":
                continue
            box = p.get("bbox")                # [x, y, w, h] 像素
            if not box:
                continue
            if identities is not None:
                found = identities.get(p.get("track_id"))
                if not found:
                    continue
                ref = found[0]
            elif p.get("team"):
                ref = PlayerRef(team=p["team"], number=str(p["jersey"]) if p.get("jersey") else "",
                                name=p.get("name"), track_id=p.get("track_id"))
            else:
                continue
            players.append(PlayerXY(
                player=ref,
                x=box[0] + box[2] / 2,
                y=box[1] + box[3],
                height_px=box[3],
            ))
        ball, ball_conf, ball_real = None, 1.0, True
        if row.get("ball"):
            b = row["ball"]
            ball = (b[0] + b[2] / 2, b[1] + b[3] / 2)
        if raw is not None:
            t_raw, det = min(raw, key=lambda r: abs(r[0] - row["t"]))
            if det and abs(t_raw - row["t"]) < 0.05:
                x1, y1, x2, y2 = det["bbox"]
                ball, ball_conf = ((x1 + x2) / 2, (y1 + y2) / 2), 0.5 + det["conf"] / 2
            elif ball is not None:
                ball_conf, ball_real = INTERP_BALL_CONF, False
        obs_list.append(FrameObs(t_sec=float(row["t"]), players=players, ball=ball,
                                 calibration="none", ball_conf=ball_conf, ball_real=ball_real))
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
        key = holder.key() if holder else None
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

def load_full_obs(tracks_path: str, ball_raw_path: Optional[str] = None) -> list[FrameObs]:
    """pipeline/vision/analyze.py 的追踪输出 → FrameObs。身份按轨迹查赛季名单，不用帧上的 name。"""
    with open(tracks_path, encoding="utf-8") as f:
        identities = resolve_identities(json.load(f))
    return load_local_obs(tracks_path, ball_raw_path, identities)


# ---------- 关键帧 ----------

def keyframe_from_obs(obs: Optional[FrameObs], holder: Optional[PlayerRef], why: str) -> Optional[Keyframe]:
    if obs is None:
        return None
    positions: list[Position] = []
    for p in obs.players:
        if holder and p.player.key() == holder.key():
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
          video_meta: dict,
          clock_resets: Optional[list[float]] = None) -> EventStream:
    stream = EventStream(job_id=job_id, video=video_meta, mode=mode)
    clip_end = video_meta.get("duration_s") or (obs_list[-1].t_sec if obs_list else None)
    spans = possession_spans(segs, ticks, clip_start=obs_list[0].t_sec if obs_list else None, clip_end=clip_end,
                             clock_resets=clock_resets)

    for idx, span in enumerate(spans, 1):
        pid = f"P{idx:02d}"
        poss = Possession(possession_id=pid, t_start=span.t_start, t_end=span.t_end,
                          offense_team=span.team)
        poss.events.extend(possession_events(pid, span.segs))
        poss.events.extend(detect_shots(pid, obs_list, span.segs, ticks,
                                        start_index=len(poss.events) + 1))
        nxt = spans[idx] if idx < len(spans) else None
        if span.ended_by == "switch" and span.segs and nxt and nxt.segs:
            poss.events.append(Event(
                event_id="", t=nxt.t_start, type="turnover",
                actor=span.segs[-1].holder, target=nxt.segs[0].holder,
                confidence=min(span.segs[-1].confidence, nxt.segs[0].confidence) * TURNOVER_CONF_SCALE))
        poss.events.sort(key=lambda e: e.t)
        for i, e in enumerate(poss.events, 1):
            e.event_id = f"{pid}-E{i}"
            obs = min(obs_list, key=lambda o: abs(o.t_sec - e.t), default=None)
            holder = next((s.holder for s in span.segs if s.t_start <= e.t <= s.t_end), None)
            kf = keyframe_from_obs(obs, holder, e.type)
            if kf:
                poss.keyframes.append(kf)
        stream.possessions.append(poss)

    return stream


def load_ticks(path: Optional[str]) -> tuple[list[ScoreTick], Optional[list[float]]]:
    """记分牌：pipeline/vision/scoreboard.py 的产物（含进攻钟重置），或人工写的 [{t_sec, team, delta}]。
    返回 (得分跳变, 进攻钟重置时刻)；没读记分牌时重置为 None，球权层不拿它卡换回合。"""
    if not path:
        return [], None
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    rows = doc["ticks"] if isinstance(doc, dict) else doc
    ticks = [ScoreTick(t_sec=float(k["t_sec"]), team=k["team"], delta=int(k["delta"]), t_reset=k.get("t_reset"))
             for k in rows]
    resets = [float(r["t_sec"]) for r in doc["resets"]] if isinstance(doc, dict) and "resets" in doc else None
    return ticks, resets


def run(job_id: str, mode: str, obs_path: str, frame_map: Optional[str] = None,
        ball_raw: Optional[str] = None, ticks_path: Optional[str] = None) -> EventStream:
    video_meta: dict = {"duration_s": 0.0, "fps": 0.0}
    if frame_map:
        with open(frame_map, encoding="utf-8") as f:
            fm = json.load(f)
        video_meta.update({"source": fm.get("video", ""), "game_id": fm.get("game_id", ""),
                           "duration_s": fm.get("duration_sec", 0.0), "fps": fm.get("fps", 0.0)})

    with open(obs_path, encoding="utf-8") as f:
        sniff = json.load(f)
    if mode == "full" or "timeline" in sniff:  # full：本地追踪；vlm：大模型整段过片
        obs_list = load_full_obs(obs_path, ball_raw) if mode == "full" else load_local_obs(obs_path, ball_raw)
        segs = holder_segments(obs_list)
        ticks, resets = load_ticks(ticks_path)
        video_meta["fps"] = video_meta["fps"] or sniff.get("sampled_fps") or 0.0
        video_meta["duration_s"] = video_meta["duration_s"] or sniff.get("duration_sec", 0.0)
    else:                                     # results/json 单帧探针
        frames = load_probe_frames(obs_path)
        obs_list = []
        segs = segments_from_probe(frames)
        ticks, resets = ticks_from_probe(frames), None

    return build(job_id, mode, segs, ticks, obs_list, video_meta, resets)


def main() -> None:
    ap = argparse.ArgumentParser(description="观测 → 事件流结构化 JSON")
    ap.add_argument("--job-id", required=True)
    ap.add_argument("--mode", choices=["vlm", "full"], default="vlm")
    ap.add_argument("--obs", required=True,
                    help="full: results/local/{clip}.json（本地追踪）；vlm: results/local/{clip}.{模型}.json 或 results/json/*.json")
    ap.add_argument("--frame-map", help="frame_map_*.json，取视频元信息")
    ap.add_argument("--ball-raw", help="results/local/*.ball_raw.json，区分真检测与插值球")
    ap.add_argument("--ticks", help="results/local/{clip}.scoreboard.json，或人工写的 [{t_sec, team, delta}]")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    stream = run(args.job_id, args.mode, args.obs, args.frame_map, args.ball_raw, args.ticks)
    stream.write(args.out)
    print(f"[build_stream] {args.out}: {len(stream.possessions)} possessions, "
          f"{sum(len(p.events) for p in stream.possessions)} events, "
          f"{sum(len(p.keyframes) for p in stream.possessions)} keyframes")


if __name__ == "__main__":
    main()
