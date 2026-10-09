#!/usr/bin/env python3
"""本地 OCR 读记分牌：比分、节次、比赛钟、进攻钟。不调大模型。

只读画面下方 1/4；第一次读全后锁定记分牌那一块，之后只读这一块，连续读丢就回到整条重新定位。比赛钟是离得最近的 M:SS；进攻钟是钟右侧 ≤24 的数；
两个比分是离钟最近的两个 2–3 位数，按先上后下、先左后右排，客队在前（NBA 转播惯例）。
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
STRIP_TOP = 0.75           # 只读画面下方这一截
CONFIRM_READS = 2
RESET_JUMP = 3.0           # 进攻钟比上一读数多这么多秒才算重置
RESET_LOOKBACK_S = 3.0     # 比分跳变向前找进攻钟重置的窗口
ROI_MARGIN = 60            # 锁定区域四周留白（像素）
ROI_LOST_READS = 3         # 锁定区域里连续这么多次读不到比赛钟，就回到整条

CLOCK = re.compile(r"^(\d{1,2}):(\d{2})$")
PERIOD = re.compile(r"^(1ST|2ND|3RD|4TH|OT\d?)$")


def _center(box) -> tuple[float, float]:
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]
    return sum(xs) / 4, sum(ys) / 4


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text.upper().replace("O", "0"))


def parse(tokens: list[tuple[str, tuple[float, float]]]) -> dict:
    out = {"period": None, "clock": None, "shot_clock": None, "scores": None}
    clocks = [(t, c) for t, c in tokens if CLOCK.match(t.replace(" ", ""))]
    if not clocks:
        return out
    clock_text, (cx, cy) = clocks[0]
    out["clock"] = clock_text.replace(" ", "")
    for t, _ in tokens:
        cleaned = re.sub(r"[^A-Z0-9]", "", t.upper())
        if PERIOD.match(cleaned):
            out["period"] = cleaned
    used = {clock_text}

    shot = []
    for t, (x, y) in tokens:
        if t in used or x <= cx or "TO" in t.upper():
            continue
        if re.fullmatch(r"\d{1,2}(\.\d)?", t.strip()) and float(t) <= 24:
            shot.append((abs(x - cx) + abs(y - cy), t.strip()))
    if shot:
        out["shot_clock"] = float(min(shot)[1])
        used.add(min(shot)[1])

    scores = []
    for t, (x, y) in tokens:
        if t in used or ":" in t or "/" in t or "TO" in t.upper():
            continue
        d = _digits(t)
        if 2 <= len(d) <= 3 and abs(y - cy) < 120 and x < cx:
            scores.append((abs(x - cx) + abs(y - cy), int(d), x, y))
    if len(scores) >= 2:
        two = sorted(scores)[:2]
        two.sort(key=lambda s: (round(s[3] / 40), s[2]))
        out["scores"] = [two[0][1], two[1][1]]
    return out


def _roi(res, parsed: dict, width: int, height: int):
    """记分牌在下方条带里的外框：比赛钟、进攻钟、比分、节次这几块文字的并集。"""
    wanted = {parsed["clock"], parsed["period"]}
    if parsed["shot_clock"] is not None:
        wanted.add(f"{parsed['shot_clock']:g}")
    wanted.update(str(v) for v in parsed["scores"] or [])
    boxes = [r[0] for r in res if r[1].replace(" ", "") in wanted or _digits(r[1]) in wanted]
    if not boxes or parsed["clock"] is None or not parsed["scores"]:
        return None
    xs = [p[0] for b in boxes for p in b]
    ys = [p[1] for b in boxes for p in b]
    return (max(0, int(min(xs)) - ROI_MARGIN), max(0, int(min(ys)) - ROI_MARGIN),
            min(width, int(max(xs)) + ROI_MARGIN), min(height, int(max(ys)) + ROI_MARGIN))


def read_clip(clip_id: str) -> dict:
    from rapidocr_onnxruntime import RapidOCR

    clip = CLIPS[clip_id]
    ocr = RapidOCR()
    cap = cv2.VideoCapture(str((ROOT / clip["video"]).resolve()))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, round(fps / SAMPLE_FPS))
    reads = []
    roi, lost = None, 0
    index = 0
    while cap.grab():
        if index % step == 0:
            _, frame = cap.retrieve()
            h = frame.shape[0]
            strip = frame[int(h * STRIP_TOP):, :]
            x0, y0 = 0, 0
            view = strip
            if roi:
                x0, y0, x1, y1 = roi
                view = strip[y0:y1, x0:x1]
            res, _ = ocr(view)
            res = [([(px + x0, py + y0) for px, py in r[0]], r[1], r[2]) for r in (res or []) if r[2] >= 0.6]
            parsed = parse([(r[1], _center(r[0])) for r in res])
            if roi and parsed["clock"] is None:
                lost += 1
                if lost >= ROI_LOST_READS:
                    roi, lost = None, 0
            elif roi:
                lost = 0
            elif parsed["clock"]:
                roi = _roi(res, parsed, strip.shape[1], strip.shape[0])
            reads.append({"t": round(index / fps, 3), **parsed})
        index += 1
    cap.release()
    return {"clip": clip_id, "sampled_fps": round(fps / step, 3), "away": clip["away"], "home": clip["home"],
            "reads": reads}


def derive(doc: dict) -> dict:
    away, home = doc["away"], doc["home"]
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
        print(f"{clip_id}: {read_ok}/{len(doc['reads'])} 次读全  ticks={doc['ticks']}  "
              f"resets={[x['t_sec'] for x in doc['resets']]}")


if __name__ == "__main__":
    main()
