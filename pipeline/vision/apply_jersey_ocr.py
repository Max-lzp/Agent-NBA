#!/usr/bin/env python3
"""给已经写好的检测结果补球衣号码。不重跑检测。

每条球员轨迹取最多 8 张够大的躯干裁切，RapidOCR 只认 1 到 2 位数字。
至少两票，或者一票且置信度不低于 0.95，才收下号码。
号码能唯一对上出场名单时才写姓名。裁判不读。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import cv2

VISION = Path(__file__).resolve().parent
ROOT = VISION.parents[1]
RESULTS = VISION / "results" / "local"
sys.path.insert(0, str(VISION))

from jersey_ocr import RapidJerseyReader  # noqa: E402
from roster_map import lookup_number  # noqa: E402
from team_color import torso_crop  # noqa: E402

CLIPS = json.loads((VISION / "clips.json").read_text(encoding="utf-8"))
COMPONENTS = {
    item["id"]: item for item in json.loads((VISION / "components.json").read_text(encoding="utf-8"))
}


def xyxy(bbox: list[float]) -> list[float]:
    x, y, width, height = bbox
    return [x, y, x + width, y + height]


def component_block(doc: dict) -> dict:
    item = COMPONENTS.get((doc.get("component") or {}).get("id") or "basketball_analysis")
    if item is None:
        return doc.get("component") or {}
    return {
        "id": item["id"],
        "label": item["label"],
        "repo": item["repo"],
        "license": item["license"],
        "role": item["role"],
        "note": item["note"],
    }


def choose_boxes(doc: dict) -> dict[int, list[tuple[float, float, list[float]]]]:
    chosen: dict[int, list[tuple[float, float, list[float]]]] = defaultdict(list)
    for row in doc["timeline"]:
        for player in row["players"]:
            if player.get("role") == "referee":
                continue
            _x, _y, _width, height = player["bbox"]
            if height < 90 or height > 520:
                continue
            chosen[int(player["track_id"])].append((height, float(row["t"]), xyxy(player["bbox"])))
    kept = {}
    for track_id, items in chosen.items():
        items.sort(reverse=True)
        kept[track_id] = items[:8]
    return kept


def read_frames(video: Path, needed: dict[float, list[tuple[int, list[float]]]]) -> dict[int, list]:
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise SystemExit(f"打不开视频 {video}")
    crops: dict[int, list] = defaultdict(list)
    for t in sorted(needed):
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok:
            continue
        for track_id, box in needed[t]:
            crop = torso_crop(frame, box)
            if crop is not None:
                crops[track_id].append(crop)
    cap.release()
    return crops


def majority_color(doc: dict, track_id: int) -> str | None:
    votes = []
    for row in doc["timeline"]:
        for player in row["players"]:
            if int(player["track_id"]) != track_id:
                continue
            color = player.get("jersey_color")
            if color in {"white", "dark"}:
                votes.append(color)
    if not votes:
        return None
    return Counter(votes).most_common(1)[0][0]


def accept(number: str | None, conf: float, votes: int) -> str | None:
    if not number:
        return None
    if votes >= 2 or (votes == 1 and conf >= 0.95):
        return number
    return None


def apply_doc(path: Path, reader: RapidJerseyReader) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    clip_id = doc["clip"]
    video = (ROOT / CLIPS[clip_id]["video"]).resolve()
    color_map = CLIPS[clip_id]["jersey_color_to_team"]
    chosen = choose_boxes(doc)
    needed: dict[float, list[tuple[int, list[float]]]] = defaultdict(list)
    for track_id, items in chosen.items():
        for _height, t, box in items:
            needed[t].append((track_id, box))
    print(f"{path.name}: {len(chosen)} tracks, {sum(len(v) for v in chosen.values())} crops", flush=True)
    crops = read_frames(video, needed)
    by_id = {int(player["track_id"]): player for player in doc.get("players") or []}
    records = {}
    named = {}
    for track_id in sorted(chosen):
        number, conf, votes = reader.vote(crops.get(track_id, []))
        number = accept(number, conf, votes)
        summary = by_id.get(track_id)
        color = (summary or {}).get("jersey_color") or majority_color(doc, track_id)
        found = lookup_number(doc["game_id"], number, color, color_map) if number else {}
        matched = bool(found.get("name")) and not found.get("dnp")
        record = {
            "jersey": number,
            "jersey_conf": round(conf, 3),
            "jersey_votes": votes,
            "name": found.get("name") if matched else None,
            "team": found.get("team") if matched else (summary or {}).get("team"),
            "identity_source": "ocr" if matched else ("ocr_unmatched" if number else "missing_key"),
            "dnp": False,
        }
        records[track_id] = record
        if summary is not None:
            summary.update(record)
        if matched:
            named[track_id] = record
        print(
            f"  track {track_id}: {number or '-'} votes={votes} conf={conf:.2f} "
            f"-> {record['name'] or record['identity_source']}",
            flush=True,
        )
    for row in doc["timeline"]:
        holder = row.get("ball_handler_track")
        for player in row["players"]:
            if player.get("role") == "referee":
                continue
            track_id = int(player["track_id"])
            record = records.get(track_id) or {}
            jersey = record.get("jersey")
            if not jersey:
                continue
            player["jersey"] = jersey
            if track_id in named:
                player["name"] = named[track_id]["name"]
                player["team"] = named[track_id]["team"]
        if holder in named:
            row["ball_handler"] = named[holder]["name"]
    for event in doc.get("events") or []:
        track_id = event.get("track_id")
        if track_id in named:
            event["actor"] = named[track_id]["name"]
    doc["named_players"] = sorted({info["name"] for info in named.values()})
    doc["component"] = component_block(doc)
    note = doc.get("source_note") or ""
    if "rapidocr" not in note:
        doc["source_note"] = (note + "; " if note else "") + "rapidocr jersey vote"
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{path.name}: named={doc['named_players']}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="给现有检测结果补号码")
    parser.add_argument("--only", help="只处理文件名里包含这段文字的结果")
    args = parser.parse_args()
    reader = RapidJerseyReader()
    targets = [
        RESULTS / "flagg.json",
        RESULTS / "amen.json",
        RESULTS / "flagg.ebard_yolov8n.json",
        RESULTS / "amen.ebard_yolov8n.json",
    ]
    for path in targets:
        if not path.is_file():
            continue
        if args.only and args.only not in path.name:
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        if "rapidocr" in (doc.get("source_note") or ""):
            print(f"{path.name}: 已经有号码，跳过", flush=True)
            continue
        apply_doc(path, reader)


if __name__ == "__main__":
    main()
