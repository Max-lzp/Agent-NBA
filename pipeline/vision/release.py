#!/usr/bin/env python3
"""出手帧：官方出手时刻前后按原帧率逐帧检测，找球离手那一帧。

抽帧时间线是 8.6 帧/秒、球框插值过，轨迹名字也可能挂错人，所以这里不依赖它：
窗口内每帧重跑 player_detector（同一个模型出球、篮筐、球员），
球心落在谁的框里谁就是持球人；之后连续几帧球都不在这个人框里，最后一帧持球就是出手帧。
投篮人姓名仍以官方记录为准，这里只给框。最近防守人看球衣深浅分队，号码用 RapidOCR 投票。

用法：pipeline/vision/.venv/bin/python pipeline/vision/release.py --clip flagg
产出：results/local/{clip}.release.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import torch

VISION = Path(__file__).resolve().parent
ROOT = VISION.parents[1]
MODELS = VISION / "models"
RESULTS = VISION / "results" / "local"
OUT = ROOT / "pipeline" / "events" / "out"
sys.path.insert(0, str(VISION))

from analyze import quiet_predict  # noqa: E402
from jersey_ocr import RapidJerseyReader  # noqa: E402
from roster_map import lookup_number  # noqa: E402
from team_color import jersey_color, torso_crop  # noqa: E402

DEVICE = "mps" if torch.backends.mps.is_available() else "cpu"
CLIPS = json.loads((VISION / "clips.json").read_text(encoding="utf-8"))

BEFORE_S = 1.0
AFTER_S = 0.4
BALL_CONF = 0.35
PLAYER_CONF = 0.4
PAD_W = 0.25            # 持球判定时人框左右放宽，手臂常伸出框外
PAD_TOP = 0.15
FREE_FRAMES = 4         # 出手后至少这么多帧有球且不在持球人框里
SAME_PERSON_IOU = 0.3
MAX_GAP = 6              # 球路里允许连续几帧没检到球
STEP_PX = 45             # 原帧率下相邻两帧球心最多移动多少像素
AFTER_OFFICIAL_S = 0.2   # 出手帧不会晚于官方对时太多
LOOKBACK_S = 0.8        # 防守人颜色和号码只看出手前这么久；出手后贴身的两条轨迹会接成一个人
MAX_CROPS = 8


def _iou(a: list[float], b: list[float]) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _holds(box: list[float], ball: list[float]) -> bool:
    cx, cy = (ball[0] + ball[2]) / 2, (ball[1] + ball[3]) / 2
    w, h = box[2] - box[0], box[3] - box[1]
    return box[0] - w * PAD_W <= cx <= box[2] + w * PAD_W and box[1] - h * PAD_TOP <= cy <= box[3]


def _foot(box: list[float]) -> tuple[float, float]:
    return (box[0] + box[2]) / 2, box[3]


def read_window(video: Path, t0: float, t1: float) -> tuple[list, list[float]]:
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise SystemExit(f"打不开视频 {video}")
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t0) * 1000)
    frames, times = [], []
    while True:
        t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
        ok, frame = cap.read()
        if not ok or t > t1:
            break
        frames.append(frame)
        times.append(round(t, 3))
    cap.release()
    return frames, times


def detect(model, frames: list) -> list[dict]:
    names = {k: str(v).lower() for k, v in model.names.items()}
    rows = []
    for i in range(0, len(frames), 8):
        for res in model.predict(frames[i:i + 8], conf=min(BALL_CONF, PLAYER_CONF), verbose=False, device=DEVICE):
            balls, players = [], []
            for box, cls, conf in zip(res.boxes.xyxy.tolist(), res.boxes.cls.tolist(), res.boxes.conf.tolist()):
                kind = names.get(int(cls))
                if kind == "ball" and conf >= BALL_CONF:
                    balls.append([round(v, 1) for v in box])
                elif kind == "player" and conf >= PLAYER_CONF:
                    players.append([round(v, 1) for v in box])
            rows.append({"balls": balls, "players": players})
    return rows


def _center(box: list[float]) -> tuple[float, float]:
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def ball_path(rows: list[dict]) -> None:
    """每帧可能有几个球的候选，误检会跳到鞋、场边。取位移连续、帧数最多的一条球路，写回 row["ball"]。"""
    nodes = [(i, k) for i, row in enumerate(rows) for k in range(len(row["balls"]))]
    best: dict[tuple[int, int], tuple[int, tuple | None]] = {}
    for i, k in nodes:
        cx, cy = _center(rows[i]["balls"][k])
        score, prev = 1, None
        for j in range(max(0, i - MAX_GAP), i):
            for m in range(len(rows[j]["balls"])):
                px, py = _center(rows[j]["balls"][m])
                if ((cx - px) ** 2 + (cy - py) ** 2) ** 0.5 <= STEP_PX * (i - j) + 20 and best[(j, m)][0] + 1 > score:
                    score, prev = best[(j, m)][0] + 1, (j, m)
        best[(i, k)] = (score, prev)
    for row in rows:
        row["ball"] = None
    node = max(best, key=lambda n: best[n][0], default=None)
    while node is not None:
        rows[node[0]]["ball"] = rows[node[0]]["balls"][node[1]]
        node = best[node][1]


def holder(row: dict) -> list[float] | None:
    ball = row["ball"]
    if not ball:
        return None
    hits = [p for p in row["players"] if _holds(p, ball)]
    if not hits:
        return None
    cx, cy = (ball[0] + ball[2]) / 2, (ball[1] + ball[3]) / 2
    return min(hits, key=lambda p: abs((p[0] + p[2]) / 2 - cx) + abs(p[1] - cy))


def find_release(rows: list[dict], times: list[float], t_official: float) -> int | None:
    """持球的那一帧之后，球连续不在这个人框里的帧数就是飞行段。官方时刻之前飞行段最长的那次脱手是出手。"""
    best, best_free = None, 0
    for i, row in enumerate(rows):
        if times[i] > t_official + AFTER_OFFICIAL_S:
            break
        box = holder(row)
        if box is None:
            continue
        free = 0
        for later in rows[i + 1:]:
            if not later["ball"]:
                continue
            same = [p for p in later["players"] if _iou(p, box) >= SAME_PERSON_IOU]
            if same and _holds(max(same, key=lambda p: _iou(p, box)), later["ball"]):
                break
            box = max(same, key=lambda p: _iou(p, box)) if same else box
            free += 1
        if free >= FREE_FRAMES and free >= best_free:
            best, best_free = i, free
    return best


def _follow(rows: list[dict], times: list[float], index: int, box: list[float]) -> dict[int, list[float]]:
    """从出手帧往前跟 LOOKBACK_S 秒，按 IoU 接同一个人。"""
    seen = {index: box}
    track, j = box, index - 1
    while j >= 0 and times[index] - times[j] <= LOOKBACK_S:
        same = [p for p in rows[j]["players"] if _iou(p, track) >= SAME_PERSON_IOU]
        if same:
            track = max(same, key=lambda q: _iou(q, track))
            seen[j] = track
        j -= 1
    return seen


def _profile(frames, rows, times, index: int, box: list[float], shooter_track: dict[int, list[float]]) -> dict:
    """球衣颜色和号码裁切只取和投篮人不重叠的帧，贴身封盖时颜色会被投篮人的球衣带偏。"""
    colors, crops = [], []
    for j, p in sorted(_follow(rows, times, index, box).items(), key=lambda kv: abs(kv[0] - index)):
        other = shooter_track.get(j)
        if other is not None and _iou(p, other) > 0.02:
            continue
        color = jersey_color(frames[j], p)
        if color:
            colors.append(color)
        crop = torso_crop(frames[j], p)
        if crop is not None:
            crops.append((p[3] - p[1], j, crop))
    color = max(set(colors), key=colors.count) if colors else None
    crops = [c for _h, _j, c in sorted(crops, key=lambda x: (-x[0], x[1]))[:MAX_CROPS]]
    return {"color": color, "color_votes": colors.count(color) if color else 0, "crops": crops}


def nearest_defender(reader, frames, rows, times, index: int, shooter: list[float]) -> dict | None:
    shooter_track = _follow(rows, times, index, shooter)
    mine = _profile(frames, rows, times, index, shooter, {})["color"]
    fx, fy = _foot(shooter)
    ranked = sorted((p for p in rows[index]["players"] if _iou(p, shooter) < 0.5),
                    key=lambda p: ((_foot(p)[0] - fx) ** 2 + (_foot(p)[1] - fy) ** 2) ** 0.5)
    for p in ranked:
        prof = _profile(frames, rows, times, index, p, shooter_track)
        if mine is None or prof["color"] is None or prof["color"] == mine:
            continue
        number, conf, votes = reader.vote(prof["crops"])
        if not number or not (votes >= 2 or (votes == 1 and conf >= 0.95)):
            number = None
        px, py = _foot(p)
        return {"box": p, "color": prof["color"], "color_votes": prof["color_votes"], "shooter_color": mine,
                "foot_gap_px": round(((px - fx) ** 2 + (py - fy) ** 2) ** 0.5),
                "number": number, "ocr_conf": round(conf, 3), "ocr_votes": votes}
    return None


def shots_of(clip_id: str) -> tuple[list[dict], str | None]:
    stream = json.loads((OUT / f"{clip_id}.json").read_text(encoding="utf-8"))
    out = []
    for poss in stream.get("possessions") or []:
        for ev in poss.get("events") or []:
            if ev.get("type") == "shot" and ev.get("official"):
                out.append(ev)
    return out, (stream.get("video") or {}).get("game_id")


def run(clip_id: str, redetect: bool = False) -> Path:
    from ultralytics import YOLO

    clip = CLIPS[clip_id]
    video = (ROOT / clip["video"]).resolve()
    model = YOLO(str(MODELS / "player_detector.pt"))
    quiet_predict(model)
    reader = RapidJerseyReader()
    shots, official_id = shots_of(clip_id)
    color_map = clip.get("jersey_color_to_team") or {}
    found, debug = [], []
    cache_path = RESULTS / f"{clip_id}.release_frames.json"
    cache = {} if redetect or not cache_path.exists() else {
        item["event_id"]: item for item in json.loads(cache_path.read_text(encoding="utf-8"))}
    for ev in shots:
        t = float(ev["t"])
        frames, times = read_window(video, t - BEFORE_S, t + AFTER_S)
        cached = cache.get(ev["event_id"])
        if cached and len(cached["rows"]) == len(frames) and "balls" in cached["rows"][0]:
            rows = [{"balls": r["balls"], "players": r["players"]} for r in cached["rows"]]
        else:
            rows = detect(model, frames)
        ball_path(rows)
        index = find_release(rows, times, t)
        debug.append({"event_id": ev["event_id"],
                      "rows": [{"t": tt, "balls": r["balls"], "players": r["players"]} for tt, r in zip(times, rows)]})
        item = {"event_id": ev["event_id"], "t_official": t, "frames": len(frames),
                "fps": round((len(times) - 1) / (times[-1] - times[0]), 2) if len(times) > 1 else None}
        if index is None:
            item.update({"t": None, "source": "not_found"})
            found.append(item)
            print(f"[出手帧] {ev['event_id']} 没找到球离手的那一帧，{len(frames)} 帧")
            continue
        shooter = holder(rows[index])
        item.update({"t": times[index], "source": "ball_left_hand", "shooter_box": shooter,
                     "ball_box": rows[index]["ball"]})
        defender = nearest_defender(reader, frames, rows, times, index, shooter)
        if defender:
            defender["team"] = color_map.get(defender["color"] or "")
            number = defender["number"]
            if number:
                hit = {}
                for gid in (official_id, clip.get("game_id")):
                    if gid:
                        hit = lookup_number(gid, number, defender["color"], color_map)
                        if hit.get("name"):
                            break
                if hit.get("name") and (not defender["team"] or hit.get("team") == defender["team"]):
                    defender.update({"name": hit["name"], "team": hit["team"]})
            item["defender"] = defender
        found.append(item)
        d = item.get("defender") or {}
        print(f"[出手帧] {ev['event_id']} 官方对时 {t:.2f}s → 出手帧 {item['t']:.3f}s，"
              f"最近防守人 {d.get('team') or '?'} #{d.get('number') or '?'} {d.get('name') or ''}".rstrip())
    dest = RESULTS / f"{clip_id}.release.json"
    dest.write_text(json.dumps({"clip": clip_id, "model": "player_detector.pt", "window_s": [BEFORE_S, AFTER_S],
                                "shots": found}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    cache_path.write_text(json.dumps(debug) + "\n", encoding="utf-8")
    return dest


def main() -> None:
    ap = argparse.ArgumentParser(description="官方出手时刻前后逐帧找出手帧和最近防守人")
    ap.add_argument("--clip", required=True)
    ap.add_argument("--redetect", action="store_true", help="不用 {clip}.release_frames.json 里缓存的逐帧检测")
    args = ap.parse_args()
    print(run(args.clip, args.redetect))


if __name__ == "__main__":
    main()
