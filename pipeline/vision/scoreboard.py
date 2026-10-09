#!/usr/bin/env python3
"""本地 OCR 读记分牌：比分、节次、比赛钟、进攻钟。不调大模型。

先读画面下方 1/4，开头几次都读不到比赛钟就改读全画面；第一次读全后锁定记分牌那一块，之后只读这一块，连续读丢就回到整条重新定位。
两个比分必须同行或同列，和比赛钟同一行的一对优先（挡掉画面里的球衣号码）。比赛钟优先认 M:SS；每节最后一分钟转播改成 SS.s，此时在 SS.s 候选里取上一次钟位置附近的，没有锚点就取最靠上、再最靠左的
（两种实测布局里比赛钟都在进攻钟的左边或上方）。比赛钟不能倒走，也不能比流逝的时间跳得更多，不合理的读数作废。
进攻钟是钟右侧 ≤24 的数；两个比分是钟左侧、竖直距离不大的两个 2–3 位数，按先上后下、先左后右排，客队在前（NBA 转播惯例）。
画面上读到 HOU / DAL 缩写时按同样顺序认出客主队，写进 detected_away / detected_home。
像素阈值按 1080p 标定，按画面高度缩放。
比分要连续 CONFIRM_READS 次读到同一个新值才算跳变，挡掉 OCR 偶发错字。

输出 results/local/{clip}.scoreboard.json：
  reads  每次读数
  ticks  [{t_sec, team, delta, t_reset}]  得分；t_reset 是得分前最近一次进攻钟重置（球进的那一刻）
  resets [{t_sec, value}]                 进攻钟往上跳（24 / 14）

用法：pipeline/vision/.venv/bin/python pipeline/vision/scoreboard.py --clip all
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import cv2

VISION = Path(__file__).resolve().parent
ROOT = VISION.parents[1]
RESULTS = VISION / "results" / "local"
CLIPS = json.loads((VISION / "clips.json").read_text(encoding="utf-8"))

SAMPLE_FPS = 3.0
STRIP_TOP = 0.75           # 先读画面下方这一截
FULL_FRAME_AFTER = 6       # 开头连续这么多次在下方读不到比赛钟，改读全画面
CONFIRM_READS = 2
RESET_JUMP = 3.0           # 进攻钟比上一读数多这么多秒才算重置
RESET_LOOKBACK_S = 3.0     # 比分跳变向前找进攻钟重置的窗口
ROI_MARGIN = 60            # 锁定区域四周留白（像素）
ROI_LOST_READS = 3         # 锁定区域里连续这么多次读不到比赛钟，就回到整条
SCORE_DY = 70              # 比分与比赛钟的最大竖直距离（1080p 像素）
ROW_PX = 40                # 同一行的竖直容差（1080p 像素）
ALIGN_PX = 20              # 两个比分同一行的竖直容差（1080p 像素），挡掉画面里的球衣号码
COLUMN_PX = 12             # 两个比分同一列的水平容差（1080p 像素）；实测竖排布局差 1 像素
ANCHOR_PX = 80             # SS.s 候选离上一次比赛钟位置的最大距离（1080p 像素）
CLOCK_SLACK_S = 2.0        # 比赛钟相对流逝时间允许多跳的秒数
TEAMS = ("HOU", "DAL")
MIN_TEAM_READS = 5         # 至少这么多次读到队名缩写，才用它覆盖配置里的客主队

CLOCK = re.compile(r"^(\d{1,2}):(\d{2})$")
TENTHS = re.compile(r"^(\d{1,2})\.(\d)$")
PERIOD = re.compile(r"^(1ST|2ND|3RD|4TH|OT\d?)$")


def _center(box) -> tuple[float, float]:
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]
    return sum(xs) / 4, sum(ys) / 4


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text.upper().replace("O", "0"))


def clock_value(text: str | None) -> float | None:
    """'7:29' → 449；'0:45.3' → 45.3。"""
    if not text:
        return None
    mm, ss = text.split(":", 1)
    return int(mm) * 60 + float(ss)


def _pick_clock(tokens, anchor, scale):
    clocks = [(t.replace(" ", ""), c) for t, c in tokens if CLOCK.match(t.replace(" ", ""))]
    if clocks:
        if anchor:
            clocks.sort(key=lambda tc: abs(tc[1][0] - anchor[0]) + abs(tc[1][1] - anchor[1]))
        return clocks[0][0], clocks[0][1], clocks[0][0]
    tenths = [(t.strip(), c) for t, c in tokens if TENTHS.match(t.strip()) and float(t) < 60]
    if not tenths:
        return None
    if anchor:
        near = [(abs(c[0] - anchor[0]) + abs(c[1] - anchor[1]), t, c) for t, c in tenths]
        d, t, c = min(near)
        return (f"0:{float(t):04.1f}", c, t) if d <= ANCHOR_PX * scale else None
    t, c = min(tenths, key=lambda tc: (round(tc[1][1] / (ROW_PX * scale)), tc[1][0]))
    return f"0:{float(t):04.1f}", c, t


def plausible(value: float, period: str | None, t: float, last: tuple | None) -> bool:
    """比赛钟只能往下走，且不能比流逝的视频时间跳得更多；换节不检查。last = (t, period, clock_s)。"""
    if last is None or period not in (None, last[1]):
        return True
    return last[2] - (t - last[0]) - CLOCK_SLACK_S <= value <= last[2] + 1.0


def parse(tokens: list[tuple[str, tuple[float, float]]], anchor=None, scale: float = 1.0) -> dict:
    out = {"period": None, "clock": None, "shot_clock": None, "scores": None, "clock_xy": None, "teams": None}
    picked = _pick_clock(tokens, anchor, scale)
    if not picked:
        return out
    out["clock"], (cx, cy), clock_text = picked
    out["clock_xy"] = [round(cx), round(cy)]
    for t, _ in tokens:
        cleaned = re.sub(r"[^A-Z0-9]", "", t.upper())
        if PERIOD.match(cleaned):
            out["period"] = cleaned
    used = {clock_text}

    shot = []
    for t, (x, y) in tokens:
        if t.strip() in used or x <= cx or "TO" in t.upper():
            continue
        if re.fullmatch(r"\d{1,2}(\.\d)?", t.strip()) and float(t) <= 24:
            shot.append((abs(x - cx) + abs(y - cy), t.strip()))
    if shot:
        out["shot_clock"] = float(min(shot)[1])
        used.add(min(shot)[1])

    scores = []
    for t, (x, y) in tokens:
        if t.strip() in used or ":" in t or "/" in t or "." in t or "TO" in t.upper():
            continue
        d = _digits(t)
        if 2 <= len(d) <= 3 and abs(y - cy) < SCORE_DY * scale and x < cx:
            scores.append((abs(x - cx) + abs(y - cy), int(d), x, y))
    pairs = [(a[0] + b[0], a, b) for i, a in enumerate(scores) for b in scores[i + 1:]
             if abs(a[3] - b[3]) <= ALIGN_PX * scale or abs(a[2] - b[2]) <= COLUMN_PX * scale]
    on_clock_row = [pr for pr in pairs if all(abs(v[3] - cy) <= ALIGN_PX * scale for v in pr[1:])]
    if pairs:
        _, a, b = min(on_clock_row or pairs, key=lambda p: p[0])
        two = sorted([a, b], key=lambda s: (round(s[3] / (ROW_PX * scale)), s[2]))
        out["scores"] = [two[0][1], two[1][1]]

    teams = sorted(((round(y / (ROW_PX * scale)), x, t.strip().upper()) for t, (x, y) in tokens
                    if t.strip().upper() in TEAMS), key=lambda r: (r[0], r[1]))
    names = [r[2] for r in teams]
    if len(set(names)) == 2:
        out["teams"] = list(dict.fromkeys(names))
    return out


def _roi(res, parsed: dict, width: int, height: int, scale: float = 1.0):
    """记分牌在下方条带里的外框：比赛钟、进攻钟、比分、节次这几块文字的并集。"""
    tail = parsed["clock"].split(":", 1)[1] if parsed["clock"] else ""
    wanted = {parsed["clock"], parsed["period"], tail, tail.lstrip("0")}
    if parsed["shot_clock"] is not None:
        wanted.add(f"{parsed['shot_clock']:g}")
    wanted.update(str(v) for v in parsed["scores"] or [])
    wanted.update(TEAMS)
    boxes = [r[0] for r in res if r[1].replace(" ", "").upper() in wanted or _digits(r[1]) in wanted]
    if not boxes or parsed["clock"] is None or not parsed["scores"]:
        return None
    xs = [p[0] for b in boxes for p in b]
    ys = [p[1] for b in boxes for p in b]
    m = int(ROI_MARGIN * scale)
    return (max(0, int(min(xs)) - m), max(0, int(min(ys)) - m),
            min(width, int(max(xs)) + m), min(height, int(max(ys)) + m))


def read_clip(clip_id: str) -> dict:
    from rapidocr_onnxruntime import RapidOCR

    clip = CLIPS[clip_id]
    ocr = RapidOCR()
    cap = cv2.VideoCapture(str((ROOT / clip["video"]).resolve()))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, round(fps / SAMPLE_FPS))
    reads = []
    top_frac, misses = STRIP_TOP, 0
    roi, lost = None, 0
    anchor, last = None, None          # last = (t, period, clock_s)
    index = 0
    while cap.grab():
        if index % step == 0:
            _, frame = cap.retrieve()
            h = frame.shape[0]
            scale = h / 1080
            strip = frame[int(h * top_frac):, :]
            x0, y0 = 0, 0
            view = strip
            if roi:
                x0, y0, x1, y1 = roi
                view = strip[y0:y1, x0:x1]
            res, _ = ocr(view)
            res = [([(px + x0, py + y0) for px, py in r[0]], r[1], r[2]) for r in (res or []) if r[2] >= 0.6]
            parsed = parse([(r[1], _center(r[0])) for r in res], anchor, scale)
            t = round(index / fps, 3)
            value = clock_value(parsed["clock"])
            if value is not None and not plausible(value, parsed["period"], t, last):
                parsed.update(clock=None, clock_xy=None, shot_clock=None, scores=None)
                value = None
            if value is not None:
                last = (t, parsed["period"] or (last[1] if last else None), value)
                anchor = parsed["clock_xy"]
            if roi and value is None:
                lost += 1
                if lost >= ROI_LOST_READS:
                    roi, lost = None, 0
            elif roi:
                lost = 0
            elif value is not None:
                roi = _roi(res, parsed, strip.shape[1], strip.shape[0], scale)
            if value is None and roi is None and anchor is None and top_frac > 0:
                misses += 1
                if misses >= FULL_FRAME_AFTER:
                    top_frac = 0.0
            parsed["region"] = "full" if top_frac == 0 else "bottom"
            reads.append({"t": t, **parsed})
        index += 1
    cap.release()
    orders = [tuple(r["teams"]) for r in reads if r.get("teams")]
    detected = max(set(orders), key=orders.count) if orders else (None, None)
    return {"clip": clip_id, "sampled_fps": round(fps / step, 3), "away": clip.get("away"), "home": clip.get("home"),
            "detected_away": detected[0], "detected_home": detected[1], "detected_reads": len(orders),
            "reads": reads}


def derive(doc: dict) -> dict:
    away, home = doc["away"], doc["home"]
    if doc.get("detected_reads", 0) >= MIN_TEAM_READS and doc.get("detected_away"):
        away, home = doc["detected_away"], doc["detected_home"]
        doc["away"], doc["home"] = away, home
    if not away or not home:
        raise SystemExit(f"{doc['clip']}：记分牌上认不出客主队，请用 --away / --home 指定")
    resets = []
    prev_shot = None
    for r in doc["reads"]:
        s = r.get("shot_clock")
        if s is None:
            continue
        if prev_shot is not None and s - prev_shot >= RESET_JUMP:
            resets.append({"t_sec": r["t"], "value": s})
        prev_shot = s

    ticks = []
    confirmed = None
    candidate, seen = None, 0
    for r in doc["reads"]:
        sc = r.get("scores")
        if not sc:
            continue
        if confirmed is None:
            if sc == candidate:
                seen += 1
            else:
                candidate, seen = sc, 1
            if seen >= CONFIRM_READS:
                confirmed = sc
            continue
        deltas = [sc[0] - confirmed[0], sc[1] - confirmed[1]]
        if deltas == [0, 0] or min(deltas) < 0 or max(deltas) > 3 or sum(1 for d in deltas if d) != 1:
            candidate, seen = None, 0
            continue
        if sc == candidate:
            seen += 1
        else:
            candidate, seen, first_t = sc, 1, r["t"]
        if seen >= CONFIRM_READS:
            side = 0 if deltas[0] else 1
            t_reset = max((x["t_sec"] for x in resets if 0 <= first_t - x["t_sec"] <= RESET_LOOKBACK_S),
                          default=None)
            ticks.append({"t_sec": first_t, "team": (away, home)[side], "delta": deltas[side],
                          "t_reset": t_reset})
            confirmed = sc
    doc["ticks"] = ticks
    doc["resets"] = resets
    return doc


def main() -> None:
    ap = argparse.ArgumentParser(description="本地 OCR 读记分牌")
    ap.add_argument("--clip", choices=[*CLIPS, "all"], default="all")
    args = ap.parse_args()
    for clip_id in (CLIPS if args.clip == "all" else [args.clip]):
        doc = derive(read_clip(clip_id))
        out = RESULTS / f"{clip_id}.scoreboard.json"
        out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        read_ok = sum(1 for r in doc["reads"] if r["scores"] and r["clock"])
        print(f"{clip_id}: 认出 {doc['detected_away']} @ {doc['detected_home']}（{doc['detected_reads']} 次）")
        print(f"{clip_id}: {read_ok}/{len(doc['reads'])} 次读全  ticks={doc['ticks']}  "
              f"resets={[x['t_sec'] for x in doc['resets']]}")


if __name__ == "__main__":
    main()
