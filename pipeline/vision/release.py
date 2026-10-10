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
LEAD_S = 5.0            # 领防人：进球那次出手往前看这么久
LEAD_STEP = 2           # 隔一帧检测，约 30 帧/秒
TRACK_GAP = 8
MATCH_BASE = 0.12      # 脚下位移不超过框高的这么多倍（每多断一帧再放宽一点）
MATCH_PER_FRAME = 0.06
STITCH_GAP = 12
GUARD_RADIUS = 0.9      # 脚下距离不超过投篮人框高的这么多倍，算贴着防
MIN_GUARD_S = 1.0       # 至少贴防这么久才叫领防人
OCR_CROPS = 40
CLEAN_IOU = 0.1        # 截号码的帧，和别人的框重叠不超过这么多
MIN_VOTES = 3
MIN_SHARE = 0.6
HOLD_RECENT_S = 1.5    # 出手前这么久里拿球最多的同队轨迹，就是持球进攻人


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


def iter_window(video: Path, t0: float, t1: float, step: int, keep: set[int] | None = None):
    """按 step 隔帧读。keep 给了就只把这些序号的帧交出来，省内存。"""
    cap = cv2.VideoCapture(str(video))
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t0) * 1000)
    raw, out = 0, 0
    while True:
        t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
        ok, frame = cap.read()
        if not ok or t > t1 + 1e-3:
            break
        if raw % step == 0:
            if keep is None or out in keep:
                yield out, round(t, 3), frame
            out += 1
        raw += 1
    cap.release()


def detect_stream(model, video: Path, t0: float, t1: float, step: int) -> tuple[list[dict], list[float]]:
    rows, times, batch = [], [], []
    names = {k: str(v).lower() for k, v in model.names.items()}

    def flush():
        for res in model.predict([f for _i, _t, f in batch], conf=min(BALL_CONF, PLAYER_CONF), verbose=False, device=DEVICE):
            balls, players = [], []
            for box, cls, conf in zip(res.boxes.xyxy.tolist(), res.boxes.cls.tolist(), res.boxes.conf.tolist()):
                kind = names.get(int(cls))
                if kind == "ball" and conf >= BALL_CONF:
                    balls.append([round(v, 1) for v in box])
                elif kind == "player" and conf >= PLAYER_CONF:
                    players.append([round(v, 1) for v in box])
            rows.append({"balls": balls, "players": players})
        batch.clear()

    for i, t, frame in iter_window(video, t0, t1, step):
        times.append(t)
        batch.append((i, t, frame))
        if len(batch) == 8:
            flush()
    if batch:
        flush()
    return rows, times


def _same_color(a: str | None, b: str | None) -> bool:
    return a is None or b is None or a == b


def _track_color(track: dict[int, list[float]], rows: list[dict]) -> str | None:
    cs = [rows[i]["colors"][rows[i]["players"].index(b)] for i, b in track.items() if _clean(rows, i, b)]
    cs = [c for c in cs if c] or [c for i, b in track.items() if (c := rows[i]["colors"][rows[i]["players"].index(b)])]
    if not cs:
        return None
    top = max(set(cs), key=cs.count)
    return top if cs.count(top) / len(cs) >= 0.7 else None


def build_tracks(rows: list[dict]) -> list[dict[int, list[float]]]:
    """按脚下位移贪心接轨迹，只接同色球衣；断开的同一人按位置和颜色拼回去。贴身防守时两人框会重叠，不看颜色会接错人。"""
    tracks: list[dict[int, list[float]]] = []
    tcolor: list[list[str]] = []
    for i, row in enumerate(rows):
        live = [k for k, tr in enumerate(tracks) if max(tr) >= i - TRACK_GAP]
        pairs = []
        for k in live:
            end = max(tracks[k])
            prev = tracks[k][end]
            px, py = _foot(prev)
            height = prev[3] - prev[1]
            limit = height * (MATCH_BASE + MATCH_PER_FRAME * (i - end))
            for m, p in enumerate(row["players"]):
                qx, qy = _foot(p)
                d = ((qx - px) ** 2 + (qy - py) ** 2) ** 0.5
                if d <= limit:
                    pairs.append((d / height, k, m))
        pairs.sort()
        used_t, used_p = set(), set()
        for _cost, k, m in pairs:
            if k in used_t or m in used_p:
                continue
            seen = max(set(tcolor[k]), key=tcolor[k].count) if tcolor[k] else None
            if not _same_color(seen, row["colors"][m]):
                continue
            tracks[k][i] = row["players"][m]
            if row["colors"][m]:
                tcolor[k].append(row["colors"][m])
            used_t.add(k)
            used_p.add(m)
        for m, p in enumerate(row["players"]):
            if m not in used_p:
                tracks.append({i: p})
                tcolor.append([row["colors"][m]] if row["colors"][m] else [])
    merged = True
    while merged:
        merged = False
        tracks.sort(key=min)
        colors = [_track_color(tr, rows) for tr in tracks]
        for a in range(len(tracks)):
            end = max(tracks[a])
            box = tracks[a][end]
            width = box[2] - box[0]
            for b in range(len(tracks)):
                start = min(tracks[b])
                if b == a or not 0 < start - end <= STITCH_GAP or not _same_color(colors[a], colors[b]):
                    continue
                other = tracks[b][start]
                ax, ay = _foot(box)
                bx, by = _foot(other)
                if ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5 <= width * 0.8:
                    tracks[a].update(tracks.pop(b))
                    merged = True
                    break
            if merged:
                break
    return tracks


def _clean(rows: list[dict], i: int, box: list[float]) -> bool:
    return all(_iou(box, p) < CLEAN_IOU for p in rows[i]["players"] if p != box)


def _read_number(reader, crops: list, allowed: set[str]) -> dict:
    """allowed 是这一队当时在场的号码。只认在场号码，票数和占比都够才给。"""
    from collections import Counter

    votes: Counter[str] = Counter()
    confs: dict[str, list[float]] = {}
    raw = 0
    for crop in crops:
        number, conf = reader.read_crop(crop)
        if not number:
            continue
        raw += 1
        if allowed and number not in allowed:
            continue
        votes[number] += 1
        confs.setdefault(number, []).append(conf)
    if not votes:
        return {"number": None, "votes": 0, "reads": raw, "share": 0.0, "conf": 0.0}
    number, count = votes.most_common(1)[0]
    share = count / sum(votes.values())
    conf = sum(confs[number]) / len(confs[number])
    ok = count >= MIN_VOTES and share >= MIN_SHARE
    return {"number": number if ok else None, "candidate": number, "votes": count, "reads": raw,
            "share": round(share, 2), "conf": round(conf, 3)}


def _crops(video: Path, t0: float, t1: float, step: int, picks: dict[int, list[float]]) -> list:
    out = []
    for i, _t, frame in iter_window(video, t0, t1, step, keep=set(picks)):
        crop = torso_crop(frame, picks[i])
        if crop is not None:
            out.append(crop)
    return out


def _foot_gap(a: list[float], b: list[float]) -> float:
    ax, ay = _foot(a)
    bx, by = _foot(b)
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def _chain(rows: list[dict], start: int, box: list[float], stop: int, mine: str | None,
           anchors: dict[int, list[float]]) -> dict[int, list[float]]:
    """从 start 往 stop 一帧帧找离上一帧最近的同队框。有拿球框的帧直接用拿球框；
    两个同队框离得差不多近就跳过这一帧，不猜。"""
    out: dict[int, list[float]] = {}
    step = 1 if stop >= start else -1
    last_i, last = start, box
    for i in range(start + step, stop + step, step):
        if i in anchors:
            out[i] = last = anchors[i]
            last_i = i
            continue
        gap = abs(i - last_i)
        if gap > TRACK_GAP:
            break
        height = last[3] - last[1]
        limit = height * (MATCH_BASE + MATCH_PER_FRAME * gap)
        cands = sorted((_foot_gap(p, last), p) for p, c in zip(rows[i]["players"], rows[i]["colors"])
                       if _same_color(c, mine))
        if not cands or cands[0][0] > limit:
            continue
        if len(cands) > 1 and cands[1][0] <= limit and cands[1][0] < cands[0][0] * 1.6:
            continue
        out[i] = last = cands[0][1]
        last_i = i
    return out


def shooter_frames(rows: list[dict], times: list[float], catch_t: float, mine: str | None,
                   release_box: list[float] | None = None) -> tuple[dict[int, list[float]], int]:
    """接球到出手每帧拿球的同队框是投篮人；中间球没检到的帧按位置接上。接球之前沿他往回接。"""
    anchors: dict[int, list[float]] = {}
    for i, row in enumerate(rows):
        if times[i] < catch_t - 0.1:
            continue
        h = holder(row)
        if h is not None and _same_color(row["colors"][row["players"].index(h)], mine):
            anchors[i] = h
    last = len(rows) - 1
    if release_box is not None:
        # 起跳以后脚离地，按脚下位置会接到底下的队友身上；出手帧以找到的投篮人框为准
        anchors[last] = max(rows[last]["players"], key=lambda p: _iou(p, release_box), default=release_box)
    if not anchors:
        return {}, 0
    first = min(anchors)
    boxes = {first: anchors[first]}
    boxes.update(_chain(rows, first, anchors[first], len(rows) - 1, mine, anchors))
    boxes.update(_chain(rows, first, anchors[first], 0, mine, {}))
    return boxes, len(anchors)


def lead_pair(model, reader, video: Path, release_t: float, shooter_box: list[float], on_court: dict,
              color_map: dict, shooter_team: str, catch_t: float | None, cached: list | None) -> tuple[dict, list]:
    """进球那次出手往前 LEAD_S 秒：先认清持球进攻人，再找这段时间最常贴着他的对方球员，就是领防人。"""
    t0 = max(0.0, release_t - LEAD_S)
    if cached:
        rows, times = [{"balls": r["balls"], "players": r["players"]} for r in cached], [r["t"] for r in cached]
    else:
        rows, times = detect_stream(model, video, t0, release_t, LEAD_STEP)
    if cached and all("colors" in r for r in cached):
        for r, c in zip(rows, cached):
            r["colors"] = c["colors"]
    else:
        for i, _t, frame in iter_window(video, t0, release_t, LEAD_STEP):
            if i < len(rows):
                rows[i]["colors"] = [jersey_color(frame, p) for p in rows[i]["players"]]
    for r in rows:
        r.setdefault("colors", [None] * len(r["players"]))
    debug = [{"t": t, "balls": r["balls"], "players": r["players"], "colors": r["colors"]} for t, r in zip(times, rows)]
    ball_path(rows)
    fps = len(rows) / max(1e-6, times[-1] - times[0])
    tracks = build_tracks(rows)
    tone = {id(tr): _track_color(tr, rows) for tr in tracks}
    mine = {v: k for k, v in color_map.items()}.get(shooter_team)
    rival = next((c for c in color_map if c != mine), None)
    catch = catch_t if catch_t is not None and catch_t < release_t else release_t - 2.0
    shooter, held = shooter_frames(rows, times, catch, mine, shooter_box)
    if not held:
        return {"status": "shooter_lost", "window_s": [round(times[0], 3), round(times[-1], 3)]}, debug

    near: dict[int, list[int]] = {}
    for i, box in shooter.items():
        height = box[3] - box[1]
        best, gap = None, None
        for tr in tracks:
            if i not in tr or tone[id(tr)] != rival:
                continue
            d = _foot_gap(tr[i], box)
            if d <= height * GUARD_RADIUS and (gap is None or d < gap):
                best, gap = tr, d
        if best is not None:
            near.setdefault(id(best), []).append(i)
    by_id = {id(tr): tr for tr in tracks}
    defender_team = color_map.get(rival or "")
    court = lambda team: {p["jersey"].lstrip("0") or "0" for p in on_court.get(team or "", [])}  # noqa: E731

    clean_shooter = {i: b for i, b in shooter.items() if _clean(rows, i, b)}
    shooter_read = _read_number(reader, _crops(video, t0, release_t, LEAD_STEP, _thin(clean_shooter, OCR_CROPS)),
                                court(shooter_team))
    span = sorted(shooter)
    result = {
        "status": "ok", "window_s": [round(times[0], 3), round(times[-1], 3)], "fps": round(fps, 1),
        "catch_t": round(catch, 3),
        "shooter": {"seconds": round(len(shooter) / fps, 2), "from_t": times[span[0]],
                    "hold_seconds": round(held / fps, 2), "color": mine, "team": shooter_team, **shooter_read,
                    "path": _path(shooter, times)},
    }
    if not near:
        result["defender"] = {"status": "none_close", "team": defender_team}
        return result, debug
    lead_id = max(near, key=lambda k: len(near[k]))
    lead = by_id[lead_id]
    guard_s = len(near[lead_id]) / fps
    picks = {i: lead[i] for i in near[lead_id] if _clean(rows, i, lead[i])}
    read = _read_number(reader, _crops(video, t0, release_t, LEAD_STEP, _thin(picks, OCR_CROPS)), court(defender_team))
    others = sorted((len(v) / fps for k, v in near.items() if k != lead_id), reverse=True)
    last = len(rows) - 1
    end = max(lead)
    lead = {**lead, **_chain(rows, end, lead[end], last, rival, {})} if end < last else lead
    result["defender"] = {
        "status": "ok" if guard_s >= MIN_GUARD_S else "short",
        "team": defender_team, "color": rival,
        "guard_seconds": round(guard_s, 2), "next_guard_seconds": round(others[0], 2) if others else 0.0,
        "guard_from_t": times[min(near[lead_id])], "guard_to_t": times[max(near[lead_id])],
        "box_at_release": lead.get(last) or next((lead[k] for k in range(last, last - int(0.3 * fps) - 1, -1) if k in lead), None),
        **read,
        "path": _path(lead, times),
    }
    return result, debug


def _path(boxes: dict[int, list[float]], times: list[float]) -> list[list]:
    """[[t, x1, y1, x2, y2], ...]，给成片和网页沿路标人名。"""
    return [[times[i], *(round(v, 1) for v in boxes[i])] for i in sorted(boxes)]


def _thin(picks: dict[int, list[float]], limit: int) -> dict[int, list[float]]:
    keys = sorted(picks)
    if len(keys) > limit:
        stride = len(keys) / limit
        keys = [keys[int(k * stride)] for k in range(limit)]
    return {i: picks[i] for i in keys}


def catch_time(stream: dict, shot: dict) -> float | None:
    """清单里出手前最后一次传给投篮人的传球。"""
    actor = shot.get("actor") or {}
    best = None
    for poss in stream.get("possessions") or []:
        for ev in poss.get("events") or []:
            target = ev.get("target") or {}
            if ev.get("type") != "pass" or ev["t"] >= shot["t"]:
                continue
            if (target.get("track_id") is not None and target.get("track_id") == actor.get("track_id")) or (
                    target.get("number") and target.get("number") == actor.get("number")):
                best = ev["t"] if best is None else max(best, ev["t"])
    return best


def _name_lead(lead: dict, game_id: str | None, on_court: dict, ev: dict) -> None:
    """号码只在当时在场名单里对名字。持球人号码和官方记录对不上就标出来。"""
    def name_of(team: str, number: str | None) -> dict:
        for p in on_court.get(team) or []:
            if number and (p["jersey"].lstrip("0") or "0") == number:
                return {"name": p["name"], "full_name": p.get("full_name"), "jersey": p["jersey"]}
        return {}

    sh = lead.get("shooter")
    if sh:
        official = str((ev.get("actor") or {}).get("number") or "").lstrip("0") or None
        sh["official_number"] = official
        if sh.get("number"):
            sh.update(name_of(sh["team"], sh["number"]))
            sh["check"] = "，和官方记录一致" if sh["number"] == official else f"，和官方记录 #{official} 不一致"
        else:
            sh["check"] = "，画面没确认，用官方记录"
    de = lead.get("defender")
    if de and de.get("number") and de.get("status") == "ok":
        de.update(name_of(de.get("team") or "", de["number"]))


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
        if (ev.get("official") or {}).get("made"):
            stream = json.loads((OUT / f"{clip_id}.json").read_text(encoding="utf-8"))
            on_court = (stream.get("official") or {}).get("on_court") or {}
            lead, lead_rows = lead_pair(model, reader, video, times[index], shooter, on_court, color_map,
                                        (ev.get("actor") or {}).get("team") or "", catch_time(stream, ev),
                                        None if redetect else (cached or {}).get("lead_rows"))
            debug[-1]["lead_rows"] = lead_rows
            _name_lead(lead, official_id or clip.get("game_id"), on_court, ev)
            item["lead"] = lead
            sh, de = lead.get("shooter") or {}, lead.get("defender") or {}
            print(f"[持球人] 接球 {lead.get('catch_t')}s，跟住 {sh.get('seconds')}s、持球 {sh.get('hold_seconds')}s，号码 {sh.get('number') or '没读出'}"
                  f"（{sh.get('votes')} 票 / 读到 {sh.get('reads')}，候选 {sh.get('candidate')}）{sh.get('check', '')}")
            print(f"[领防人] {de.get('team') or '?'} 贴防 {de.get('guard_seconds')}s（次多 {de.get('next_guard_seconds')}s），"
                  f"号码 {de.get('number') or '没读出'}（{de.get('votes')} 票 / 读到 {de.get('reads')}，候选 {de.get('candidate')}）"
                  f" {de.get('name') or ''}")
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
