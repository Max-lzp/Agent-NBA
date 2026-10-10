#!/usr/bin/env python3
"""回溯：某人在后面才被认出时，沿同色球衣框回到他在这段里出现的第一帧。

检测结果缓存在 results/local/{clip}.detect.json，同一段再跑不再检测。
身份来自两条：轨迹号码票数够的人，以及出手窗口里跟住的投篮人、领防人。
产出 results/local/{clip}.backtrack.json，并写入事件清单的 players_full、ball_path。

    pipeline/vision/.venv/bin/python -m pipeline.vision.backtrack --clip flagg
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

VISION = Path(__file__).resolve().parent
ROOT = VISION.parents[1]
RESULTS = VISION / "results" / "local"
OUT = ROOT / "pipeline" / "events" / "out"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(VISION))

CLIPS = json.loads((VISION / "clips.json").read_text(encoding="utf-8"))

INTRO_S = 3.0
PASS_WINDOW = 1.5
STEP = 2
GAP = 12                 # 大约 0.8 秒没对上就停止，不隔着别人猜
MATCH_BASE = 0.15
MATCH_PER_FRAME = 0.07
AMBIGUOUS = 1.6


def _foot(box: list[float]) -> tuple[float, float]:
    return (box[0] + box[2]) / 2, box[3]


def _gap(a: list[float], b: list[float]) -> float:
    ax, ay = _foot(a)
    bx, by = _foot(b)
    return math.hypot(ax - bx, ay - by)


def _same(a: str | None, b: str | None) -> bool:
    return a is None or b is None or a == b


def _iou(a: list[float], b: list[float]) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union else 0.0


def chain(rows: list[dict], start: int, box: list[float], stop: int, color: str | None,
          anchors: dict[int, list[float]]) -> dict[int, list[float]]:
    """从 start 往 stop 逐帧接同色、脚下最近的框。两个框差不多近就空过这一帧。"""
    out: dict[int, list[float]] = {}
    step = 1 if stop >= start else -1
    last_i, last = start, box
    end = stop + step
    i = start + step
    while i != end:
        if i in anchors:
            out[i] = last = anchors[i]
            last_i = i
            i += step
            continue
        gap_n = abs(i - last_i)
        if gap_n > GAP:
            break
        height = max(last[3] - last[1], 40)
        limit = height * (MATCH_BASE + MATCH_PER_FRAME * gap_n)
        cands = sorted(
            (_gap(p, last), p) for p, c in zip(rows[i]["players"], rows[i]["colors"]) if _same(c, color)
        )
        if not cands or cands[0][0] > limit:
            i += step
            continue
        if len(cands) > 1 and cands[1][0] <= limit and cands[1][0] < cands[0][0] * AMBIGUOUS:
            i += step
            continue
        out[i] = last = cands[0][1]
        last_i = i
        i += step
    return out


def extend(rows: list[dict], anchors: dict[int, list[float]], color: str | None) -> dict[int, list[float]]:
    if not anchors or not rows:
        return dict(anchors)
    keys = sorted(anchors)
    full = dict(anchors)
    full.update(chain(rows, keys[0], anchors[keys[0]], 0, color, anchors))
    full.update(chain(rows, keys[-1], anchors[keys[-1]], len(rows) - 1, color, anchors))
    for left, right in zip(keys, keys[1:]):
        if right > left + 1:
            full.update(chain(rows, left, anchors[left], right, color, anchors))
    return full


def keep_unique(people: list[dict]) -> list[dict]:
    """优先级高的人先占框。后来的人在同一帧上和已占的框重叠，就丢掉这一帧。"""
    taken: dict[int, list[list[float]]] = {}
    kept = []
    for person in sorted(people, key=lambda p: -p["priority"]):
        path = {}
        for i, box in person["frames"].items():
            if any(_iou(box, old) > 0.45 for old in taken.get(i, [])):
                continue
            path[i] = box
            taken.setdefault(i, []).append(box)
        if path:
            kept.append({**person, "frames": path})
    return kept


def box_at(path: list, t: float, slack: float = 0.1) -> list[float] | None:
    best = None
    for row in path or []:
        gap = abs(row[0] - t)
        if gap <= slack and (best is None or gap < best[0]):
            best = (gap, row[1:])
    return list(best[1]) if best else None


def _holder(players: list[dict], ball: list[float] | None, team: str | None) -> dict | None:
    if not ball:
        return None
    cx, cy = (ball[0] + ball[2]) / 2, (ball[1] + ball[3]) / 2
    pool = [p for p in players if p.get("team") == team] or list(players)

    def dist(p):
        return math.hypot(_foot(p["box"])[0] - cx, _foot(p["box"])[1] - cy)

    if not pool:
        return None
    who = min(pool, key=dist)
    height = max(who["box"][3] - who["box"][1], 40)
    return who if dist(who) <= height * 1.8 else None


def visible_at(players: list[dict], ball_path: list, passes: list[dict], possessions: list[dict],
               t: float, shot_t: float | None = None) -> list[dict]:
    """前 INTRO_S 秒：这一帧上已回溯到的人全标。之后只留持球人、这一下传球的双方、贴着持球人的防守人，
    以及出手前仍在附近的领防人。"""
    here = []
    for person in players:
        box = box_at(person.get("path"), t)
        if box:
            here.append({**person, "box": box})
    if t < INTRO_S:
        return [{**p, "role": "all"} for p in here]
    offense = ""
    for poss in possessions:
        if poss.get("t0", 0) - 0.05 <= t <= poss.get("t1", 0) + 0.05:
            offense = poss.get("team") or ""
    ball = box_at(ball_path, t, 0.2)
    show: list[dict] = []

    def add(person: dict | None, role: str) -> None:
        if not person or any(p["team"] == person["team"] and p["number"] == person["number"] for p in show):
            return
        show.append({**person, "role": role})

    holder = _holder(here, ball, offense or None)
    add(holder, "handler")
    for pas in passes:
        if abs(float(pas["t"]) - t) > PASS_WINDOW:
            continue
        before = box_at(ball_path, float(pas["t"]) - 0.35, 0.25)
        after = box_at(ball_path, float(pas["t"]) + 0.35, 0.25)
        add(_holder(here, before, offense or None), "passer")
        add(_holder(here, after, offense or None), "receiver")
    if holder:
        height = max(holder["box"][3] - holder["box"][1], 40)
        opps = [p for p in here if p.get("team") and p["team"] != holder.get("team")]
        if opps:
            near = min(opps, key=lambda p: _gap(p["box"], holder["box"]))
            if _gap(near["box"], holder["box"]) <= height * 1.5:
                add(near, "defender")
        if shot_t is not None and shot_t - 5 <= t <= shot_t + 0.4:
            for person in here:
                if person.get("role_seed") == "lead" and _gap(person["box"], holder["box"]) <= height * 2.4:
                    add(person, "lead")
    return show


def _index(times: list[float], t: float) -> int | None:
    if not times:
        return None
    i = min(range(len(times)), key=lambda k: abs(times[k] - t))
    return i if abs(times[i] - t) <= 0.25 else None


def _snap(rows: list[dict], times: list[float], t: float, box: list[float], color: str | None):
    i = _index(times, t)
    if i is None:
        return None
    height = max(box[3] - box[1], 40)
    cands = []
    for p, c in zip(rows[i]["players"], rows[i]["colors"]):
        if not _same(c, color):
            continue
        cands.append((_iou(p, box), _gap(p, box), p))
    if not cands:
        return i, box
    cands.sort(key=lambda r: (-r[0], r[1]))
    iou, gap, picked = cands[0]
    if iou >= 0.25 or gap <= height * 0.7:
        return i, picked
    return i, box


def _anchors(rows, times, samples: list[tuple[float, list[float]]], color: str | None) -> dict[int, list[float]]:
    out = {}
    for t, box in samples:
        snapped = _snap(rows, times, t, box, color)
        if snapped:
            out[snapped[0]] = snapped[1]
    return out


def _vision_people(doc: dict) -> list[dict]:
    from pipeline.events.identity import resolve

    spans: dict[int, list[tuple[float, list[float]]]] = {}
    for row in doc.get("timeline") or []:
        for p in row.get("players") or []:
            box = p.get("bbox")
            if p.get("role") == "referee" or not box:
                continue
            x, y, w, h = box
            spans.setdefault(int(p["track_id"]), []).append((float(row["t"]), [x, y, x + w, y + h]))
    found = resolve(doc)
    groups: dict[tuple, dict] = {}
    summary = {p["track_id"]: p for p in doc.get("players") or []}
    for tid, (ref, conf) in found.items():
        if not ref.name or not ref.number or tid not in spans:
            continue
        votes = int(summary.get(tid, {}).get("jersey_votes") or 0)
        key = (ref.team, str(ref.number))
        person = groups.setdefault(key, {
            "team": ref.team, "number": str(ref.number), "name": ref.name,
            "color": summary.get(tid, {}).get("jersey_color"), "priority": 0, "samples": [], "role_seed": "",
        })
        person["priority"] = max(person["priority"], votes * 10 + conf)
        person["samples"].extend(spans[tid])
        if not person["color"]:
            person["color"] = summary.get(tid, {}).get("jersey_color")
    return list(groups.values())


def _release_people(doc: dict, game_id: str) -> list[dict]:
    from pipeline.vision.roster_map import identify

    people = []
    for shot in doc.get("shots") or []:
        lead = shot.get("lead") or {}
        shooter = lead.get("shooter") or {}
        number = shooter.get("number") or shooter.get("official_number")
        if number and shooter.get("path"):
            hit = identify(str(number), team=shooter.get("team"), game_id=game_id)
            if hit.get("name"):
                people.append({
                    "team": hit["team"], "number": str(hit["jersey"]), "name": hit["name"],
                    "color": shooter.get("color"), "priority": 100, "role_seed": "shooter",
                    "samples": [(row[0], row[1:]) for row in shooter["path"]],
                })
        defender = lead.get("defender") or {}
        if defender.get("name") and defender.get("number") and defender.get("path"):
            people.append({
                "team": defender.get("team"), "number": str(defender["number"]), "name": defender["name"],
                "color": defender.get("color"), "priority": 90, "role_seed": "lead",
                "samples": [(row[0], row[1:]) for row in defender["path"]],
            })
    return people


def merge_people(groups: list[dict]) -> list[dict]:
    merged: dict[tuple, dict] = {}
    for person in groups:
        key = (person["team"], str(person["number"]))
        cur = merged.get(key)
        if cur is None:
            merged[key] = {**person, "samples": list(person["samples"])}
            continue
        cur["samples"].extend(person["samples"])
        cur["priority"] = max(cur["priority"], person["priority"])
        cur["color"] = cur["color"] or person.get("color")
        if person.get("role_seed") == "lead":
            cur["role_seed"] = "lead"
    return list(merged.values())


def paths_from(rows: list[dict], times: list[float], people: list[dict]) -> list[dict]:
    built = []
    for person in people:
        anchors = _anchors(rows, times, person["samples"], person.get("color"))
        frames = extend(rows, anchors, person.get("color"))
        built.append({**person, "frames": frames})
    unique = keep_unique(built)
    out = []
    for person in unique:
        path = [[times[i], *[round(v, 1) for v in person["frames"][i]]] for i in sorted(person["frames"])]
        if not path:
            continue
        out.append({
            "team": person["team"], "number": person["number"], "name": person["name"],
            "role_seed": person.get("role_seed") or "", "priority": person["priority"],
            "t0": path[0][0], "t1": path[-1][0], "path": path,
        })
    return out


def ball_path(rows: list[dict], times: list[float]) -> list[list[float]]:
    path = []
    for t, row in zip(times, rows):
        balls = row.get("balls") or []
        if balls:
            path.append([t, *balls[0]])
    return path


def load_detect(clip_id: str, video: Path, duration: float) -> tuple[list[dict], list[float]]:
    cache = RESULTS / f"{clip_id}.detect.json"
    if cache.exists():
        doc = json.loads(cache.read_text(encoding="utf-8"))
        if doc.get("rows") and doc.get("times") and doc["times"][-1] >= duration - 0.4:
            return doc["rows"], doc["times"]
    from ultralytics import YOLO

    from analyze import quiet_predict
    from release import MODELS, detect_stream, iter_window
    from team_color import jersey_color

    model = YOLO(str(MODELS / "player_detector.pt"))
    quiet_predict(model)
    rows, times = detect_stream(model, video, 0.0, duration, STEP)
    colored = iter_window(video, 0.0, duration, STEP)
    for i, _t, frame in colored:
        if i < len(rows):
            rows[i]["colors"] = [jersey_color(frame, p) for p in rows[i]["players"]]
    for row in rows:
        row.setdefault("colors", [None] * len(row["players"]))
    cache.write_text(json.dumps({"times": times, "rows": rows}, ensure_ascii=False), encoding="utf-8")
    return rows, times


def attach_stream(clip_id: str, players: list[dict], balls: list) -> None:
    path = OUT / f"{clip_id}.json"
    if not path.exists():
        return
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["players_full"] = [{k: p[k] for k in ("team", "number", "name", "t0", "t1", "path")} for p in players]
    doc["ball_path"] = balls
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(clip_id: str) -> dict:
    clip = CLIPS[clip_id]
    video = (ROOT / clip["video"]).resolve()
    vision_path = RESULTS / f"{clip_id}.json"
    vision = json.loads(vision_path.read_text(encoding="utf-8")) if vision_path.exists() else {}
    duration = float(vision.get("duration_sec") or 0)
    if duration <= 0:
        import cv2
        cap = cv2.VideoCapture(str(video))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        duration = cap.get(cv2.CAP_PROP_FRAME_COUNT) / fps
        cap.release()
    rows, times = load_detect(clip_id, video, duration)
    release_path = RESULTS / f"{clip_id}.release.json"
    release = json.loads(release_path.read_text(encoding="utf-8")) if release_path.exists() else {}
    game_id = clip.get("official_game_id") or clip.get("game_id") or ""
    people = merge_people(_vision_people(vision) + _release_people(release, game_id))
    players = paths_from(rows, times, people)
    balls = ball_path(rows, times)
    dest = RESULTS / f"{clip_id}.backtrack.json"
    dest.write_text(json.dumps({"clip": clip_id, "players": players, "ball": balls}, ensure_ascii=False), encoding="utf-8")
    attach_stream(clip_id, players, balls)
    for person in players:
        print(f"[回溯] {person['name']} #{person['number']} {person['t0']:.2f}–{person['t1']:.2f}s {len(person['path'])} 帧", flush=True)
    return {"players": players, "ball": balls}


def main() -> None:
    parser = argparse.ArgumentParser(description="把认出的球员回溯到他出现的第一帧")
    parser.add_argument("--clip", required=True)
    args = parser.parse_args()
    run(args.clip)


if __name__ == "__main__":
    main()
