#!/usr/bin/env python3
"""用同一段采样视频跑另一套开源检测，写出回放台能切换的结果。

不覆盖 basketball_analysis 已经写好的 flagg.json / amen.json。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2

VISION = Path(__file__).resolve().parent
sys.path.insert(0, str(VISION))

from analyze import RESULTS, ROOT, read_sampled  # noqa: E402
from team_color import jersey_color  # noqa: E402

COMPONENTS = json.loads((VISION / "components.json").read_text(encoding="utf-8"))
BY_ID = {item["id"]: item for item in COMPONENTS}
CLIPS = json.loads((VISION / "clips.json").read_text(encoding="utf-8"))


def component_block(engine: str) -> dict:
    item = BY_ID[engine]
    return {
        "id": item["id"],
        "label": item["label"],
        "repo": item["repo"],
        "license": item["license"],
        "role": item["role"],
        "note": item["note"],
    }


def xywh(xyxy) -> list[int]:
    x1, y1, x2, y2 = [float(v) for v in xyxy]
    return [round(x1), round(y1), round(x2 - x1), round(y2 - y1)]


def detect_yolov8n(frames):
    import supervision as sv
    from ultralytics import YOLO

    weights = VISION / "models" / "yolov8n.pt"
    model = YOLO(str(weights if weights.is_file() else "yolov8n.pt"))
    if not weights.is_file():
        # ultralytics 会把下载的权重放在当前目录，挪到 models/ 以免混进仓库根。
        downloaded = Path("yolov8n.pt")
        if downloaded.is_file():
            weights.parent.mkdir(parents=True, exist_ok=True)
            downloaded.replace(weights)
            model = YOLO(str(weights))
    names = model.names
    person_ids = [i for i, name in names.items() if str(name) == "person"]
    ball_ids = [i for i, name in names.items() if str(name) == "sports ball"]
    if not person_ids:
        raise SystemExit(f"YOLOv8n 没有 person 类: {names}")
    person_id = person_ids[0]
    ball_id = ball_ids[0] if ball_ids else None
    tracker = sv.ByteTrack()
    batch = 8
    detections = []
    for start in range(0, len(frames), batch):
        detections.extend(
            model.predict(
                frames[start : start + batch],
                conf=0.35,
                classes=[person_id] + ([ball_id] if ball_id is not None else []),
                verbose=False,
            )
        )
        print(f"yolov8n {min(start + batch, len(frames))}/{len(frames)}", flush=True)
    tracks = []
    balls = []
    for detection in detections:
        parsed = sv.Detections.from_ultralytics(detection)
        if parsed.class_id is None or len(parsed) == 0:
            tracks.append(parsed)
            balls.append(None)
            tracker.update_with_detections(parsed)
            continue
        person_mask = parsed.class_id == person_id
        ball_mask = parsed.class_id == ball_id if ball_id is not None else person_mask & False
        persons = parsed[person_mask]
        ball_dets = parsed[ball_mask]
        tracked = tracker.update_with_detections(persons)
        tracks.append(tracked)
        if len(ball_dets) == 0 or ball_dets.confidence is None:
            balls.append(None)
        else:
            balls.append(ball_dets.xyxy[int(ball_dets.confidence.argmax())])
    return tracks, balls


def class_id(names: dict, *candidates: str) -> int | None:
    wanted = {name.lower() for name in candidates}
    for index, name in names.items():
        if str(name).lower() in wanted:
            return int(index)
    return None


def detection_map(tracked, offset: int = 0) -> dict:
    frame = {}
    if tracked is None or tracked.tracker_id is None:
        return frame
    for xyxy, track_id in zip(tracked.xyxy, tracked.tracker_id):
        if track_id is None:
            continue
        frame[int(track_id) + offset] = {"bbox": [float(v) for v in xyxy]}
    return frame


def drop_overlapping(players: dict, refs: dict, threshold: float = 0.45) -> dict:
    if not refs:
        return players
    kept = {}
    for track_id, info in players.items():
        box = info["bbox"]
        if any(iou(box, ref["bbox"]) >= threshold for ref in refs.values()):
            continue
        kept[track_id] = info
    return kept


def iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    inter = max(0.0, min(ax2, bx2) - max(ax1, bx1)) * max(0.0, min(ay2, by2) - max(ay1, by1))
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def detect_ebard(frames):
    import supervision as sv
    from ultralytics import YOLO

    weights = VISION / "models" / "BODD_yolov8n_0001.pt"
    if not weights.is_file():
        raise SystemExit(f"还没有 {weights.name}")
    model = YOLO(str(weights))
    player_cls = class_id(model.names, "player")
    ref_cls = class_id(model.names, "referee", "ref")
    ball_cls = class_id(model.names, "basketball", "ball")
    if player_cls is None or ref_cls is None:
        raise SystemExit(f"E-BARD 类名对不上: {model.names}")
    player_tracker = sv.ByteTrack()
    ref_tracker = sv.ByteTrack()
    batch = 8
    detections = []
    for start in range(0, len(frames), batch):
        detections.extend(
            model.predict(
                frames[start : start + batch],
                conf=0.25,
                imgsz=704,
                verbose=False,
            )
        )
        print(f"ebard {min(start + batch, len(frames))}/{len(frames)}", flush=True)
    player_tracks = []
    ref_tracks = []
    balls = []
    for detection in detections:
        parsed = sv.Detections.from_ultralytics(detection)
        if parsed.class_id is None or len(parsed) == 0:
            player_tracks.append({})
            ref_tracks.append({})
            balls.append(None)
            player_tracker.update_with_detections(parsed)
            ref_tracker.update_with_detections(parsed)
            continue
        players = detection_map(player_tracker.update_with_detections(parsed[parsed.class_id == player_cls]))
        refs = detection_map(
            ref_tracker.update_with_detections(parsed[parsed.class_id == ref_cls]),
            offset=10_000,
        )
        player_tracks.append(drop_overlapping(players, refs))
        ref_tracks.append(refs)
        if ball_cls is None:
            balls.append(None)
            continue
        ball_dets = parsed[parsed.class_id == ball_cls]
        if len(ball_dets) == 0 or ball_dets.confidence is None:
            balls.append(None)
        else:
            balls.append(ball_dets.xyxy[int(ball_dets.confidence.argmax())])
    return player_tracks, ref_tracks, balls


def timeline_from_tracks(frames, times, tracks, balls, color_map) -> list[dict]:
    rows = []
    for frame, t, tracked, ball in zip(frames, times, tracks, balls):
        players = []
        if tracked is not None and tracked.tracker_id is not None:
            for xyxy, track_id in zip(tracked.xyxy, tracked.tracker_id):
                if track_id is None:
                    continue
                box = [float(v) for v in xyxy]
                color = jersey_color(frame, box)
                players.append(
                    {
                        "track_id": int(track_id),
                        "bbox": xywh(box),
                        "team": color_map.get(color) if color else None,
                        "jersey_color": color,
                        "role": "player",
                        "jersey": None,
                        "name": None,
                    }
                )
        ball_out = xywh(ball) if ball is not None else None
        rows.append(
            {
                "t": t,
                "players": players,
                "ball": ball_out,
                "ball_handler_track": None,
                "ball_handler": None,
                "court_keypoints": 0,
            }
        )
    return rows


def timeline_from_roles(frames, times, player_tracks, ref_tracks, balls, color_map, possession) -> list[dict]:
    rows = []
    for frame, t, players, refs, ball, holder in zip(
        frames, times, player_tracks, ref_tracks, balls, possession
    ):
        people = []
        for track_id, info in players.items():
            box = info["bbox"]
            color = jersey_color(frame, box)
            people.append(
                {
                    "track_id": int(track_id),
                    "role": "player",
                    "bbox": xywh(box),
                    "team": color_map.get(color) if color else None,
                    "jersey_color": color,
                    "jersey": None,
                    "name": None,
                }
            )
        for track_id, info in refs.items():
            people.append(
                {
                    "track_id": int(track_id),
                    "role": "referee",
                    "bbox": xywh(info["bbox"]),
                    "team": None,
                    "jersey_color": None,
                    "jersey": None,
                    "name": None,
                }
            )
        rows.append(
            {
                "t": t,
                "players": people,
                "ball": xywh(ball) if ball is not None else None,
                "ball_handler_track": None if holder == -1 else int(holder),
                "ball_handler": None,
                "court_keypoints": 0,
            }
        )
    return rows


def possess(player_tracks, balls, effective_fps: float) -> list[int]:
    from ball_aquisition import BallAquisitionDetector

    ball_tracks = []
    for ball in balls:
        if ball is None:
            ball_tracks.append({})
        else:
            ball_tracks.append({1: {"bbox": [float(v) for v in ball]}})
    detector = BallAquisitionDetector()
    detector.min_frames = max(3, round(11 * effective_fps / 24))
    return detector.detect_ball_possession(player_tracks, ball_tracks)


def run_clip(engine: str, clip_id: str, target_fps: float) -> None:
    clip = CLIPS[clip_id]
    video = (ROOT / clip["video"]).resolve()
    frames, times, effective_fps, _stride = read_sampled(video, target_fps)
    print(f"{engine} {clip_id}: {len(frames)} frames", flush=True)
    color_map = clip["jersey_color_to_team"]
    if engine == "yolov8n_coco":
        tracks, balls = detect_yolov8n(frames)
        timeline = timeline_from_tracks(frames, times, tracks, balls, color_map)
    elif engine == "ebard_yolov8n":
        player_tracks, ref_tracks, balls = detect_ebard(frames)
        holders = possess(player_tracks, balls, effective_fps)
        timeline = timeline_from_roles(
            frames, times, player_tracks, ref_tracks, balls, color_map, holders
        )
    else:
        raise SystemExit(f"还没有实现 {engine}")
    doc = {
        "clip": clip_id,
        "game_id": clip["game_id"],
        "video": clip["video"],
        "component": component_block(engine),
        "source_note": f"{engine} local weights; no jersey OCR; no Qwen",
        "sampled_fps": round(effective_fps, 3),
        "frame_count": len(frames),
        "duration_sec": times[-1] if times else 0,
        "players": [],
        "named_players": [],
        "events": [],
        "timeline": timeline,
    }
    out = RESULTS / f"{clip_id}.{engine}.json"
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out.name}", flush=True)


def stamp_basketball() -> None:
    block = component_block("basketball_analysis")
    for clip_id in CLIPS:
        path = RESULTS / f"{clip_id}.json"
        if not path.is_file():
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["component"] = block
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"stamped {path.name}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="跑对照检测器")
    parser.add_argument("--engine", choices=["yolov8n_coco", "ebard_yolov8n"], default="ebard_yolov8n")
    parser.add_argument("--clip", choices=["flagg", "amen", "all"], default="all")
    parser.add_argument("--fps", type=float, default=8.0)
    parser.add_argument("--stamp-only", action="store_true")
    args = parser.parse_args()
    stamp_basketball()
    if args.stamp_only:
        return
    targets = ["flagg", "amen"] if args.clip == "all" else [args.clip]
    for clip_id in targets:
        run_clip(args.engine, clip_id, args.fps)


if __name__ == "__main__":
    main()
