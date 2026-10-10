#!/usr/bin/env python3
"""本地视觉主路径。

检测跟踪：abdullahtarek/basketball_analysis 的预训练 YOLO + ByteTrack。
球衣号码：Roboflow basketball-jersey-numbers-ocr 的本地权重。
身份：号码 + 球衣颜色 → roster.json。
Qwen3 VL 不在这里逐帧调用。记分牌、战术和难帧走 qwen_roles.py。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import torch

VISION = Path(__file__).resolve().parent
VENDOR = VISION / "vendor" / "basketball_analysis"
ROOT = VISION.parents[1]
MODELS = VISION / "models"
RESULTS = VISION / "results" / "local"
sys.path.insert(0, str(VISION))
sys.path.insert(0, str(VENDOR))

from jersey_ocr import JerseyNumberReader, RapidJerseyReader, number_crops  # noqa: E402
from roster_map import lookup_number  # noqa: E402
from team_color import jersey_color, stable_color  # noqa: E402


def quiet_predict(model):
    original = model.predict

    def predict(*args, **kwargs):
        kwargs.setdefault("verbose", False)
        return original(*args, **kwargs)

    model.predict = predict


def read_sampled(video: Path, target_fps: float) -> tuple[list, list[float], float, int]:
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise SystemExit(f"打不开视频 {video}")
    source_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, round(source_fps / target_fps))
    frames = []
    times = []
    index = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if index % step == 0:
            frames.append(frame)
            times.append(round(index / source_fps, 3))
        index += 1
    cap.release()
    effective_fps = source_fps / step
    return frames, times, effective_fps, step


def load_detectors():
    from trackers import BallTracker, PlayerTracker
    from court_keypoint_detector import CourtKeypointDetector

    player = PlayerTracker(str(MODELS / "player_detector.pt"))
    ball = BallTracker(str(MODELS / "ball_detector_model.pt"))
    court = CourtKeypointDetector(str(MODELS / "court_keypoint_detector.pt"))
    for model in (player.model, ball.model, court.model):
        quiet_predict(model)
    print(
        f"classes player={player.model.names} ball={ball.model.names}",
        flush=True,
    )
    return player, ball, court


def keypoint_counts(court_keypoints) -> list[int]:
    counts = []
    for item in court_keypoints:
        try:
            points = item.xy.tolist()[0]
        except Exception:
            counts.append(0)
            continue
        counts.append(sum(1 for x, y in points if x > 0 and y > 0))
    return counts


def identify_tracks(frames, tracks, game_id: str, color_map: dict, reader: JerseyNumberReader | None):
    samples: dict[int, list] = defaultdict(list)
    colors: dict[int, list[str]] = defaultdict(list)
    for frame, track in zip(frames, tracks):
        for track_id, info in track.items():
            bbox = info["bbox"]
            samples[track_id].append((frame, bbox))
            color = jersey_color(frame, bbox)
            if color:
                colors[track_id].append(color)
    identified = {}
    for track_id, pairs in samples.items():
        color = stable_color(colors.get(track_id, []))
        team = color_map.get(color) if color else None
        jersey, conf, votes = (None, 0.0, 0)
        if reader is not None:
            crops = number_crops([frame for frame, _ in pairs], [bbox for _, bbox in pairs])
            jersey, conf, votes = reader.vote(crops)
        jersey = jersey if votes >= 2 or (votes == 1 and conf >= 0.95) else None
        found = lookup_number(game_id, jersey, color, color_map) if jersey else {}
        if found.get("name") and not found.get("dnp"):
            team = found.get("team") or team
            source = "ocr"
            name = found["name"]
        else:
            source = "ocr_unmatched" if jersey else "missing_key"
            name = None
        identified[track_id] = {
            "track_id": int(track_id),
            "role": "player",
            "team": team,
            "jersey_color": color,
            "jersey": jersey,
            "jersey_conf": round(conf, 3),
            "jersey_votes": votes,
            "name": name,
            "identity_source": source,
            "dnp": False,
            "samples": len(pairs),
        }
    return identified


def possession_events(times, possession, identified, passes, interceptions):
    events = []
    for index, (passed, stolen) in enumerate(zip(passes, interceptions)):
        holder = possession[index]
        name = identified.get(holder, {}).get("name") if holder != -1 else None
        if passed != -1:
            events.append({"type": "pass", "t": times[index], "actor": name, "track_id": holder})
        if stolen != -1:
            events.append(
                {"type": "interception", "t": times[index], "actor": name, "track_id": holder}
            )
    return events


def summarize_refs(ref_tracks) -> list[dict]:
    counts: dict[int, int] = defaultdict(int)
    for frame in ref_tracks:
        for track_id in frame:
            counts[int(track_id)] += 1
    rows = []
    for track_id, samples in counts.items():
        rows.append(
            {
                "track_id": track_id,
                "role": "referee",
                "team": None,
                "jersey_color": None,
                "jersey": None,
                "jersey_conf": 0.0,
                "jersey_votes": 0,
                "name": None,
                "identity_source": "referee",
                "dnp": False,
                "samples": samples,
            }
        )
    return rows


def component_meta() -> dict:
    items = json.loads((VISION / "components.json").read_text(encoding="utf-8"))
    item = next(entry for entry in items if entry["id"] == "basketball_analysis")
    return {
        "id": item["id"],
        "label": item["label"],
        "repo": item["repo"],
        "license": item["license"],
        "role": item["role"],
        "note": item["note"],
    }


def hard_tracks(identified) -> list[dict]:
    """号码没读出来、但又被跟踪到的人。留给 Qwen 兜底，不在这里调用。"""
    return [
        info
        for info in identified.values()
        if info["name"] is None and info["samples"] >= 5
    ]


def analyze_clip(clip_id: str, clip: dict, target_fps: float, reader, max_seconds: float | None) -> dict:
    from ball_aquisition import BallAquisitionDetector
    from pass_and_interception_detector import PassAndInterceptionDetector

    video = (ROOT / clip["video"]).resolve()
    frames, times, effective_fps, stride = read_sampled(video, target_fps)
    if max_seconds is not None:
        keep = [i for i, t in enumerate(times) if t <= max_seconds]
        frames = [frames[i] for i in keep]
        times = [times[i] for i in keep]
    print(f"{clip_id}: {len(frames)} frames @ {effective_fps:.2f} fps", flush=True)

    player_tracker, ball_tracker, court_detector = load_detectors()
    print(f"{clip_id}: player detect", flush=True)
    player_tracks = player_tracker.get_object_tracks(frames, read_from_stub=False, stub_path=None)
    ref_tracks = player_tracker.ref_tracks
    print(
        f"{clip_id}: refs={sum(len(frame) for frame in ref_tracks)} "
        f"suppressed_player_boxes={player_tracker.suppressed_players}",
        flush=True,
    )
    print(f"{clip_id}: ball detect", flush=True)
    ball_tracks = ball_tracker.get_object_tracks(frames, read_from_stub=False, stub_path=None)
    # 上游 25px 按约 24fps 的相邻帧。抽帧后一帧跨过 stride 张原帧，阈值按同样倍数放宽。
    ball_tracks = ball_tracker.remove_wrong_detections(
        ball_tracks, maximum_allowed_distance=25 * stride
    )
    ball_tracks = ball_tracker.interpolate_ball_positions(ball_tracks)
    print(f"{clip_id}: court keypoints", flush=True)
    court_keypoints = court_detector.get_court_keypoints(frames, read_from_stub=False, stub_path=None)
    counts = keypoint_counts(court_keypoints)

    identified = identify_tracks(
        frames,
        player_tracks,
        clip["game_id"],
        clip["jersey_color_to_team"],
        reader,
    )
    # 上游按约 24fps 设了 11 帧确认。采样更稀时按比例缩短。
    ball_detector = BallAquisitionDetector()
    ball_detector.min_frames = max(3, round(11 * effective_fps / 24))
    possession = ball_detector.detect_ball_possession(player_tracks, ball_tracks)
    assignment = []
    for track in player_tracks:
        assignment.append(
            {
                track_id: {"HOU": 1, "DAL": 2}[identified[track_id]["team"]]
                for track_id in track
                if identified.get(track_id, {}).get("team") in {"HOU", "DAL"}
            }
        )
    pass_detector = PassAndInterceptionDetector()
    passes = pass_detector.detect_passes(possession, assignment)
    interceptions = pass_detector.detect_interceptions(possession, assignment)
    events = possession_events(times, possession, identified, passes, interceptions)

    referees = summarize_refs(ref_tracks)
    timeline = []
    for index, (t, track, refs, ball, holder) in enumerate(
        zip(times, player_tracks, ref_tracks, ball_tracks, possession)
    ):
        players = []
        for track_id, info in track.items():
            identity = identified.get(track_id, {})
            x1, y1, x2, y2 = info["bbox"]
            players.append(
                {
                    "track_id": int(track_id),
                    "role": "player",
                    "bbox": [round(x1), round(y1), round(x2 - x1), round(y2 - y1)],
                    "team": identity.get("team"),
                    "jersey": identity.get("jersey"),
                    "name": identity.get("name"),
                }
            )
        for track_id, info in refs.items():
            x1, y1, x2, y2 = info["bbox"]
            players.append(
                {
                    "track_id": int(track_id),
                    "role": "referee",
                    "bbox": [round(x1), round(y1), round(x2 - x1), round(y2 - y1)],
                    "team": None,
                    "jersey": None,
                    "name": None,
                }
            )
        ball_bbox = ball.get(1, {}).get("bbox")
        ball_ok = bool(ball_bbox) and all(v == v for v in ball_bbox)
        if ball_ok:
            bx1, by1, bx2, by2 = ball_bbox
            ball_out = [round(bx1), round(by1), round(bx2 - bx1), round(by2 - by1)]
        else:
            ball_out = None
        timeline.append(
            {
                "t": t,
                "players": players,
                "ball": ball_out,
                "ball_handler_track": None if holder == -1 else int(holder),
                "ball_handler": None if holder == -1 else identified.get(holder, {}).get("name"),
                "court_keypoints": counts[index] if index < len(counts) else 0,
            }
        )

    named = [info for info in identified.values() if info["name"]]
    return {
        "clip": clip_id,
        "game_id": clip["game_id"],
        "video": clip["video"],
        "component": component_meta(),
        "source_note": (
            "local basketball_analysis weights; Ref kept; rapidocr jersey vote; no per-frame Qwen"
            if reader is not None
            else "local basketball_analysis weights; Ref kept; jersey OCR absent; no per-frame Qwen"
        ),
        "device": "mps" if torch.backends.mps.is_available() else "cpu",
        "sampled_fps": round(effective_fps, 3),
        "frame_count": len(frames),
        "duration_sec": times[-1] if times else 0,
        "players": sorted(list(identified.values()) + referees, key=lambda item: -item["samples"]),
        "named_players": sorted({info["name"] for info in named}),
        "hard_tracks": hard_tracks(identified),
        "events": events,
        "court_keypoint_frames": sum(1 for count in counts if count >= 4),
        "timeline": timeline,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="本地检测、跟踪、号码识别")
    parser.add_argument("--clip", choices=["flagg", "amen", "all"], default="all")
    parser.add_argument("--fps", type=float, default=8.0)
    parser.add_argument("--max-seconds", type=float, default=None)
    args = parser.parse_args()
    clips = json.loads((VISION / "clips.json").read_text(encoding="utf-8"))
    jersey_path = MODELS / "jersey_numbers.pt"
    if jersey_path.is_file():
        reader = JerseyNumberReader(jersey_path)
        print(f"号码读取器 YOLO {jersey_path.name}", flush=True)
    else:
        reader = RapidJerseyReader()
        print("号码读取器 RapidOCR", flush=True)
    targets = ["flagg", "amen"] if args.clip == "all" else [args.clip]
    RESULTS.mkdir(parents=True, exist_ok=True)
    for clip_id in targets:
        document = analyze_clip(clip_id, clips[clip_id], args.fps, reader, args.max_seconds)
        suffix = "" if args.max_seconds is None else f"_{args.max_seconds:.0f}s"
        out = RESULTS / f"{clip_id}{suffix}.json"
        out.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(
            f"{clip_id}: named={document['named_players']} events={len(document['events'])} "
            f"hard={len(document['hard_tracks'])} -> {out}",
            flush=True,
        )


if __name__ == "__main__":
    main()
