#!/usr/bin/env python3
"""用已经检测好的框，重判球衣颜色，并给稳定轨迹读一次号码。

不重跑 YOLO。颜色看每一帧的下摆和短裤。
号码只对颜色稳定、跟踪够长的轨迹各问一次 Qwen，不逐帧调用。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

import cv2

VISION = Path(__file__).resolve().parent
ROOT = VISION.parents[1]
RESULTS = VISION / "results" / "local"
sys.path.insert(0, str(VISION))
sys.path.insert(0, str(VISION / "vendor" / "basketball_analysis"))

from qwen_roles import fallback_number  # noqa: E402
from roster_map import lookup_number, normalize_jersey  # noqa: E402
from team_color import jersey_color, stable_color  # noqa: E402

ROLE_ARN = "arn:aws:iam::278477606068:role/NBAHackathonParticipantRole"


def xywh_to_xyxy(bbox: list[float]) -> list[float]:
    x, y, w, h = bbox
    return [x, y, x + w, y + h]


def load_clip(clip_id: str) -> tuple[dict, dict]:
    clips = json.loads((VISION / "clips.json").read_text(encoding="utf-8"))
    doc = json.loads((RESULTS / f"{clip_id}.json").read_text(encoding="utf-8"))
    return clips[clip_id], doc


def recolor(clip_id: str, clip: dict, doc: dict) -> dict:
    video = (ROOT / clip["video"]).resolve()
    color_map = clip["jersey_color_to_team"]
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise SystemExit(f"打不开视频 {video}")
    votes: dict[int, list[str]] = defaultdict(list)
    for row in doc["timeline"]:
        cap.set(cv2.CAP_PROP_POS_MSEC, float(row["t"]) * 1000)
        ok, frame = cap.read()
        if not ok:
            continue
        for player in row["players"]:
            color = jersey_color(frame, xywh_to_xyxy(player["bbox"]))
            player["jersey_color"] = color
            player["team"] = color_map.get(color) if color else None
            if color:
                votes[player["track_id"]].append(color)
    cap.release()

    by_id = {player["track_id"]: player for player in doc["players"]}
    for track_id, player in by_id.items():
        color = stable_color(votes.get(track_id, []))
        player["jersey_color"] = color
        player["team"] = color_map.get(color) if color else None
        player["color_votes"] = dict(Counter(votes.get(track_id, [])))
        if player.get("name") and player.get("identity_source") not in {"qwen_unique", "qwen_color"}:
            player["name"] = None
            player["jersey"] = None
            player["identity_source"] = "recolored"
    doc["source_note"] = (
        "local basketball_analysis weights; jersey color from shorts/hem per frame; "
        "jersey OCR weights absent"
    )
    return doc


def best_crop(clip: dict, doc: dict, track_id: int, color: str) -> tuple[float, list[float]] | None:
    best = None
    for row in doc["timeline"]:
        for player in row["players"]:
            if player["track_id"] != track_id or player.get("jersey_color") != color:
                continue
            x, y, w, h = player["bbox"]
            if h < 110 or h > 520 or w > 320:
                continue
            area = w * h
            if best is None or area > best[0]:
                best = (area, row["t"], player["bbox"])
    if best is None:
        return None
    return best[1], best[2]


def save_crop(video: Path, t: float, bbox: list[float], dest: Path) -> None:
    cap = cv2.VideoCapture(str(video))
    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise SystemExit(f"读不到 {video.name} t={t}")
    height, width = frame.shape[:2]
    x, y, w, h = [int(round(v)) for v in bbox]
    pad = 6
    x1, y1 = max(0, x - pad), max(0, y - pad)
    x2, y2 = min(width, x + w + pad), min(height, y + h + pad)
    crop = frame[y1:y2, x1:x2]
    scale = 640 / max(crop.shape[0], 1)
    if scale > 1:
        crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(dest), crop, [int(cv2.IMWRITE_JPEG_QUALITY), 90])


def ensure_participant_credentials() -> None:
    if os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SESSION_TOKEN"):
        return
    proc = subprocess.run(
        [
            "aws", "sts", "assume-role",
            "--profile", "team04",
            "--role-arn", ROLE_ARN,
            "--role-session-name", "vision-identity",
            "--region", "us-east-1",
            "--output", "json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(proc.stderr.strip() or "假定 ParticipantRole 失败")
    creds = json.loads(proc.stdout)["Credentials"]
    os.environ["AWS_ACCESS_KEY_ID"] = creds["AccessKeyId"]
    os.environ["AWS_SECRET_ACCESS_KEY"] = creds["SecretAccessKey"]
    os.environ["AWS_SESSION_TOKEN"] = creds["SessionToken"]
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"


def apply_number(doc: dict, game_id: str, color_map: dict, track_id: int, found: dict) -> None:
    by_id = {player["track_id"]: player for player in doc["players"]}
    summary = by_id[track_id]
    summary.update(
        {
            "jersey": found.get("jersey"),
            "name": found.get("name"),
            "team": found.get("team") or summary.get("team"),
            "identity_source": found.get("source"),
            "dnp": found.get("dnp", False),
        }
    )
    for row in doc["timeline"]:
        for player in row["players"]:
            if player["track_id"] != track_id:
                continue
            if found.get("name") and player.get("jersey_color") == summary.get("jersey_color"):
                player["jersey"] = found.get("jersey")
                player["name"] = found.get("name")
                player["team"] = found.get("team")
            else:
                player["jersey"] = None
                player["name"] = None


def read_numbers(clip_id: str, clip: dict, doc: dict, only: set[int] | None) -> list[dict]:
    ensure_participant_credentials()
    color_map = clip["jersey_color_to_team"]
    video = (ROOT / clip["video"]).resolve()
    calls = []
    targets = []
    for player in doc["players"]:
        if only is not None and player["track_id"] not in only:
            continue
        if player.get("name") and player.get("identity_source") in {"qwen_unique", "qwen_color"}:
            continue
        if not player.get("jersey_color") or player.get("samples", 0) < 20:
            continue
        targets.append(player)
    crop_dir = VISION / "work" / "identity" / clip_id
    for player in targets:
        track_id = player["track_id"]
        picked = best_crop(clip, doc, track_id, player["jersey_color"])
        if picked is None:
            calls.append({"clip": clip_id, "track_id": track_id, "skipped": "no_crop"})
            continue
        t, bbox = picked
        image = crop_dir / f"track_{track_id}.jpg"
        save_crop(video, t, bbox, image)
        result = fallback_number(image)
        parsed = result.get("parsed") or {}
        jersey = normalize_jersey(parsed.get("jersey"))
        found = lookup_number(doc["game_id"], jersey, player["jersey_color"], color_map)
        if found.get("name") and not found.get("dnp"):
            apply_number(doc, doc["game_id"], color_map, track_id, found)
        else:
            player["identity_source"] = found.get("source") or "qwen_unreadable"
            player["jersey_raw"] = jersey
        calls.append(
            {
                "clip": clip_id,
                "track_id": track_id,
                "t": t,
                "jersey_color": player["jersey_color"],
                "qwen_jersey": jersey,
                "qwen_color": parsed.get("color"),
                "name": player.get("name"),
                "team": player.get("team"),
                "source": player.get("identity_source"),
                "cost": result.get("cost"),
            }
        )
        print(
            f"{clip_id} track {track_id}: qwen={jersey} color={player['jersey_color']} "
            f"-> {player.get('name') or found.get('source')} ${result['cost']['total_usd']:.6f}",
            flush=True,
        )
    named = sorted({player["name"] for player in doc["players"] if player.get("name")})
    doc["named_players"] = named
    doc["source_note"] = (
        "local basketball_analysis weights; jersey color from shorts/hem per frame; "
        "one Qwen crop per stable track; no per-frame Qwen"
    )
    return calls


def recompute_events(doc: dict) -> None:
    from pass_and_interception_detector import PassAndInterceptionDetector

    possession = []
    assignment = []
    times = []
    for row in doc["timeline"]:
        holder = row.get("ball_handler_track")
        possession.append(-1 if holder is None else int(holder))
        times.append(row["t"])
        teams = {}
        for player in row["players"]:
            code = {"HOU": 1, "DAL": 2}.get(player.get("team"))
            if code:
                teams[player["track_id"]] = code
        assignment.append(teams)
    detector = PassAndInterceptionDetector()
    passes = detector.detect_passes(possession, assignment)
    interceptions = detector.detect_interceptions(possession, assignment)
    names = {player["track_id"]: player.get("name") for player in doc["players"]}
    events = []
    for index, (passed, stolen) in enumerate(zip(passes, interceptions)):
        holder = possession[index]
        name = names.get(holder) if holder != -1 else None
        if passed != -1:
            events.append({"type": "pass", "t": times[index], "actor": name, "track_id": holder})
        if stolen != -1:
            events.append(
                {"type": "interception", "t": times[index], "actor": name, "track_id": holder}
            )
    doc["events"] = events
    for row in doc["timeline"]:
        holder = row.get("ball_handler_track")
        row["ball_handler"] = names.get(holder) if holder is not None else None


def parse_only(text: str | None) -> set[int] | None:
    if not text:
        return None
    return {int(part) for part in text.split(",") if part.strip()}


def main() -> None:
    parser = argparse.ArgumentParser(description="重判球衣颜色，并给稳定轨迹读号码")
    parser.add_argument("--clip", choices=["flagg", "amen", "all"], default="all")
    parser.add_argument("--numbers", action="store_true", help="对稳定轨迹各调用一次 Qwen 读号码")
    parser.add_argument("--only", help="只读这些轨迹，逗号分隔，例如 14,27")
    args = parser.parse_args()
    targets = ["flagg", "amen"] if args.clip == "all" else [args.clip]
    only = parse_only(args.only)
    all_calls = []
    for clip_id in targets:
        clip, doc = load_clip(clip_id)
        print(f"{clip_id}: recolor", flush=True)
        doc = recolor(clip_id, clip, doc)
        changed = [
            f"{player['track_id']}:{player.get('team') or '-'}"
            for player in doc["players"]
            if player.get("samples", 0) >= 30
        ]
        print(f"{clip_id}: long tracks {', '.join(changed)}", flush=True)
        if args.numbers:
            all_calls.extend(read_numbers(clip_id, clip, doc, only))
        recompute_events(doc)
        out = RESULTS / f"{clip_id}.json"
        out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        named = [player for player in doc["players"] if player.get("name")]
        print(
            f"{clip_id}: events {len(doc['events'])} named {len(named)}",
            flush=True,
        )
    if all_calls:
        log_path = RESULTS / "identity_calls.json"
        previous = []
        if log_path.is_file() and only is not None:
            previous = json.loads(log_path.read_text(encoding="utf-8"))
            seen = {(row.get("clip"), row.get("track_id")) for row in all_calls}
            previous = [row for row in previous if (row.get("clip"), row.get("track_id")) not in seen]
        log_path.write_text(
            json.dumps(previous + all_calls, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        spent = sum((row.get("cost") or {}).get("total_usd") or 0 for row in all_calls)
        print(f"qwen calls {len(all_calls)} ${spent:.6f}", flush=True)


if __name__ == "__main__":
    main()
