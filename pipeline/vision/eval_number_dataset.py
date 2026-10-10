#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""号码识别基线评测：在官方录像帧数据集上跑生产同款 OCR，和官方标签对账。

评测口径（帧级，不含跟踪——跟踪是他们管线的另一环，这里单测 OCR 质量）：
  每帧检测球员（player_detector.pt, conf=0.5, Player 类）→ 躯干裁切 →
  RapidOCR 读 1-2 位数字（jersey_ocr.parse_jersey_text 同款过滤）。
  标签 = 该剪辑投篮人的官方背号（_labels.jsonl）。

帧结局：
  hit              投篮人号码出现在本帧任一识别结果里
  miss_with_reads  有识别结果但没有命中真值（读错/读到别人）
  no_read          检测到球员但一个有效数字都没读出来
  no_player        一个球员都没检测到

产出（results/number_eval/）：
  clips/*.json     每剪辑逐帧明细（断点续跑依据）
  error_cases.csv  所有 miss/no_read 帧
  report.html      汇总 + 混淆对 + 失败案例缩略图
  summary.json     指标

用法：.venv/bin/python eval_number_dataset.py [--workers 6] [--clips 0022500160_163 ...]
"""
from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import sys
from collections import Counter
from pathlib import Path

VISION = Path(__file__).resolve().parent
WORKSPACE = VISION.parents[2]
FRAMES = WORKSPACE / "data" / "2025-26" / "frames"
OUT = VISION / "results" / "number_eval"
MIN_H, MAX_H, MAX_BOXES = 90, 520, 12  # 与 apply_jersey_ocr.choose_boxes 同款过滤


def norm(n: str | None) -> str | None:
    if n is None:
        return None
    n = str(n).strip()
    if n == "00":
        return "00"
    try:
        return str(int(n))
    except ValueError:
        return None


def eval_clip(clip_dir: Path, truth: dict) -> dict:
    import cv2

    from jersey_ocr import RapidJerseyReader
    from team_color import torso_crop
    from ultralytics import YOLO

    model = YOLO(str(VISION / "models" / "player_detector.pt"))
    reader = RapidJerseyReader()
    truth_num = norm(truth["number"])
    frames_out = []
    for img_path in sorted(clip_dir.glob("f_*.jpg")):
        frame = cv2.imread(str(img_path))
        if frame is None:
            continue
        result = model.predict(frame, conf=0.5, classes=[4], verbose=False)[0]
        boxes = []
        if result.boxes is not None:
            for b in result.boxes:
                x1, y1, x2, y2 = b.xyxy[0].tolist()
                boxes.append([x1, y1, x2, y2])
        boxes = [b for b in boxes if MIN_H <= b[3] - b[1] <= MAX_H]
        boxes.sort(key=lambda b: b[3] - b[1], reverse=True)
        boxes = boxes[:MAX_BOXES]
        reads = []
        for b in boxes:
            crop = torso_crop(frame, b)
            if crop is None:
                continue
            number, conf = reader.read_crop(crop)
            number = norm(number)
            if number:
                reads.append({"n": number, "c": round(conf, 3),
                              "h": int(b[3] - b[1])})
        nums = [r["n"] for r in reads]
        if truth_num in nums:
            outcome = "hit"
        elif nums:
            outcome = "miss_with_reads"
        elif boxes:
            outcome = "no_read"
        else:
            outcome = "no_player"
        frames_out.append({
            "frame": img_path.name, "tSec": round(
                (int(img_path.stem.split("_")[1]) - 1) * 0.5, 2),
            "outcome": outcome, "reads": reads, "n_boxes": len(boxes),
            "truth": truth_num, "shooter": truth["shooter"],
            "number_raw": truth["number"],
        })
    return {"clip": clip_dir.name, "truth": truth_num,
            "shooter": truth["shooter"], "team": truth["team"],
            "period": truth["period"], "gameClock": truth["gameClock"],
            "frames": frames_out}


def _run(job):
    d_name, out_path, truth = job
    out_file = Path(out_path)
    try:
        res = eval_clip(FRAMES / d_name, truth)
    except Exception as e:  # 单个剪辑失败不拖垮全局
        res = {"clip": d_name, "error": f"{type(e).__name__}: {e}", "frames": []}
    out_file.write_text(json.dumps(res, ensure_ascii=False))
    return d_name


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--clips", nargs="*", help="只跑指定剪辑")
    args = ap.parse_args()

    labels = [json.loads(l) for l in (FRAMES / "_labels.jsonl").read_text().splitlines()]
    truth_by_clip = {}
    for r in labels:
        truth_by_clip.setdefault(r["clip"], r)
    clip_dirs = sorted(p for p in FRAMES.iterdir() if p.is_dir())
    if args.clips:
        clip_dirs = [p for p in clip_dirs if p.name in set(args.clips)]
    (OUT / "clips").mkdir(parents=True, exist_ok=True)

    todo, done = [], 0
    for d in clip_dirs:
        out_file = OUT / "clips" / f"{d.name}.json"
        if out_file.exists():
            done += 1
        else:
            todo.append((d.name, str(out_file), truth_by_clip[d.name]))
    print(f"剪辑 {len(clip_dirs)}：已完成 {done}，待跑 {len(todo)}", flush=True)

    import multiprocessing as mp

    with mp.Pool(args.workers) as pool:
        for i, name in enumerate(pool.imap_unordered(_run, todo), 1):
            if i % 20 == 0 or i == len(todo):
                print(f"[{i}/{len(todo)}] {name}", flush=True)

    # ---- 汇总 ----
    outcomes = Counter()
    per_number = {}
    confusion = Counter()      # (true, pred) 出现在 miss_with_reads 帧里
    miss_frames, no_read_frames, no_player_frames = [], [], []
    total_frames = 0
    for f in sorted((OUT / "clips").glob("*.json")):
        doc = json.loads(f.read_text())
        for fr in doc.get("frames", []):
            total_frames += 1
            o = fr["outcome"]
            outcomes[o] += 1
            t = fr["truth"]
            per_number.setdefault(t, Counter())[o] += 1
            if o == "hit":
                continue
            row = {"clip": doc["clip"], "frame": fr["frame"], "tSec": fr["tSec"],
                   "truth": t, "shooter": fr["shooter"],
                   "reads": ";".join(f"{r['n']}({r['c']})" for r in fr["reads"]),
                   "n_boxes": fr["n_boxes"]}
            if o == "miss_with_reads":
                for r in fr["reads"]:
                    confusion[(t, r["n"])] += 1
                miss_frames.append(row)
            elif o == "no_read":
                no_read_frames.append(row)
            else:
                no_player_frames.append(row)

    hits = outcomes["hit"]
    summary = {
        "frames_total": total_frames,
        "outcomes": dict(outcomes),
        "hit_rate": round(hits / total_frames, 4) if total_frames else 0,
        "miss_rate": round(outcomes["miss_with_reads"] / total_frames, 4) if total_frames else 0,
        "per_number": {t: dict(c) for t, c in sorted(per_number.items())},
        "confusion_top": [
            {"true": t, "pred": p, "count": n}
            for (t, p), n in confusion.most_common(30)
        ],
    }
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))

    with open(OUT / "error_cases.csv", "w", newline="") as w:
        wr = csv.DictWriter(w, fieldnames=["clip", "frame", "tSec", "truth", "shooter",
                                           "reads", "n_boxes", "failure"])
        wr.writeheader()
        for r in miss_frames:
            r2 = dict(r); r2["failure"] = "wrong_number"; wr.writerow(r2)
        for r in no_read_frames:
            r2 = dict(r); r2["failure"] = "no_read"; wr.writerow(r2)
        for r in no_player_frames:
            r2 = dict(r); r2["failure"] = "no_player"; wr.writerow(r2)

    build_report(summary, miss_frames, no_read_frames)
    print(json.dumps({k: summary[k] for k in ("frames_total", "outcomes", "hit_rate")}, ensure_ascii=False))
    print(f"报告: {OUT / 'report.html'}  错误表: {OUT / 'error_cases.csv'}")


def thumb(path: Path, width: 480) -> str:
    import cv2
    img = cv2.imread(str(path))
    if img is None:
        return ""
    h, w = img.shape[:2]
    img = cv2.resize(img, (width, int(h * width / w)))
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])
    return base64.b64encode(buf).decode() if ok else ""


def build_report(summary, miss_frames, no_read_frames) -> None:
    FR = summary["frames_total"]
    oc = summary["outcomes"]

    def pct(n):
        return f"{100*n/FR:.1f}%" if FR else "-"

    rows = "".join(
        f"<tr><td>{t}</td><td>{c.get('hit',0)}</td><td>{c.get('miss_with_reads',0)}</td>"
        f"<td>{c.get('no_read',0)}</td><td>{c.get('no_player',0)}</td>"
        f"<td>{pct(c.get('hit',0))}</td></tr>"
        for t, c in summary["per_number"].items())
    conf_rows = "".join(
        f"<tr><td>{c['true']}</td><td>{c['pred']}</td><td>{c['count']}</td></tr>"
        for c in summary["confusion_top"][:15])

    # 失败案例图：miss 按次数取前 12，no_read 取前 8
    def gallery(rows_, label):
        items = []
        for r in rows_[:20]:
            p = FRAMES / r["clip"] / r["frame"]
            b = thumb(p)
            if not b:
                continue
            items.append(
                f"<figure><img src='data:image/jpeg;base64,{b}'>"
                f"<figcaption>{r['clip']} {r['frame']} t={r['tSec']}s<br>"
                f"真值 {r['truth']} 读到 [{r['reads'] or '无'}]</figcaption></figure>")
            if len(items) >= (12 if label == "wrong" else 8):
                break
        return "".join(items)

    html = f"""<!doctype html><meta charset="utf-8"><title>号码识别基线评测</title>
<style>
body{{font-family:-apple-system,sans-serif;margin:24px;max-width:1200px}}
table{{border-collapse:collapse;margin:12px 0}}td,th{{border:1px solid #ccc;padding:4px 10px;font-size:13px}}
h2{{margin-top:28px}}.grid{{display:flex;flex-wrap:wrap;gap:10px}}
figure{{margin:0;width:320px}}img{{width:320px}}figcaption{{font-size:12px;color:#444}}
.big{{font-size:20px}}
</style>
<h1>号码识别基线评测（官方录像 249 剪辑 / {FR} 帧）</h1>
<p class="big">帧级命中率 <b>{pct(oc.get('hit',0))}</b>（投篮人号码出现在该帧任一 OCR 结果中）</p>
<table><tr><th>结局</th><th>帧数</th><th>占比</th></tr>
<tr><td>hit 命中</td><td>{oc.get('hit',0)}</td><td>{pct(oc.get('hit',0))}</td></tr>
<tr><td>miss_with_reads 读错/读到别人</td><td>{oc.get('miss_with_reads',0)}</td><td>{pct(oc.get('miss_with_reads',0))}</td></tr>
<tr><td>no_read 有球员没读出数字</td><td>{oc.get('no_read',0)}</td><td>{pct(oc.get('no_read',0))}</td></tr>
<tr><td>no_player 没检测到球员</td><td>{oc.get('no_player',0)}</td><td>{pct(oc.get('no_player',0))}</td></tr></table>
<h2>按真值号码的命中情况</h2>
<table><tr><th>真值</th><th>hit</th><th>miss</th><th>no_read</th><th>no_player</th><th>命中率</th></tr>{rows}</table>
<h2>混淆对（帧里真值缺席时被读成什么，top15）</h2>
<table><tr><th>真值</th><th>被读成</th><th>次数</th></tr>{conf_rows}</table>
<h2>读错案例（前 12）</h2><div class="grid">{gallery(miss_frames,'wrong')}</div>
<h2>无读数案例（前 8）</h2><div class="grid">{gallery(no_read_frames,'noread')}</div>
"""
    (OUT / "report.html").write_text(html, encoding="utf-8")


if __name__ == "__main__":
    main()
