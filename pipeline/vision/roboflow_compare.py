#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Roboflow Universe 号码模型 vs 本地 RapidOCR 基线 的同款对账。

在「与 eval_number_dataset 完全相同的帧、相同的标签、相同的帧级口径」下，
调 Roboflow hosted inference（Universe 公开模型，默认 basketball-jersey-numbers-ocr/3），
输出命中指标 + 与本地基线的逐帧胜负对照。

密钥：只从 pipeline/vision/.env.local 读 ROBOFLOW_API_KEY（该文件已 gitignore），
脚本不打印 key；不要把 key 发进聊天。

用法：
  .venv/bin/python roboflow_compare.py                # 跑所有本地已评测剪辑的同批帧
  .venv/bin/python roboflow_compare.py --max-frames 8 # 每剪辑只取前 8 帧（省额度）
  .venv/bin/python roboflow_compare.py --model basketball-jersey-numbers-ocr/3
产出：results/roboflow_compare/  clips/*.json、compare_summary.json、compare.html
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

VISION = Path(__file__).resolve().parent
WORKSPACE = VISION.parents[2]
FRAMES = WORKSPACE / "data" / "2025-26" / "frames"
LOCAL = VISION / "results" / "number_eval" / "clips"
OUT = VISION / "results" / "roboflow_compare"

# serverless 接口形态：POST https://serverless.roboflow.com/{project}/{version}
# body 为裸 base64（form-urlencoded），Bearer 头鉴权（官方 2026-09 文档格式）
ENDPOINTS = [
    "https://serverless.roboflow.com/{model}",
]
NUM_RE = re.compile(r"\d{1,2}")


def load_key() -> str:
    env = VISION / ".env.local"
    if not env.exists():
        raise SystemExit("缺少 pipeline/vision/.env.local，请先写入 ROBOFLOW_API_KEY=...")
    for line in env.read_text().splitlines():
        if line.startswith("ROBOFLOW_API_KEY="):
            key = line.split("=", 1)[1].strip().strip('"').strip("'")
            if key:
                return key
    raise SystemExit(".env.local 里没有 ROBOFLOW_API_KEY= 行")


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


def extract_numbers(payload: dict) -> list[dict]:
    """从 Roboflow 各形态响应里抽出号码读数 [{'n': '28', 'c': 0.9}]。"""
    out = []
    preds = payload.get("predictions") or []
    for p in preds:
        label = str(p.get("class") or p.get("label") or "")
        m = NUM_RE.search(label.replace("O", "0"))
        if m:
            n = norm(m.group())
            if n:
                out.append({"n": n, "c": round(float(p.get("confidence") or 0), 3)})
    # VLM 形态（SmolVLM2 LoRA 可能返回纯文本）
    if not out:
        text = str(payload.get("output") or payload.get("text") or payload.get("result") or "")
        for tok in NUM_RE.findall(text):
            n = norm(tok)
            if n:
                out.append({"n": n, "c": 0.5})
    return out


def infer_one(url_tmpl: str, model: str, key: str, image_path: Path, timeout=90) -> dict:
    b64 = base64.b64encode(image_path.read_bytes()).decode()
    url = url_tmpl.format(model=model) + "?confidence=0.4"
    req = urllib.request.Request(
        url, data=b64.encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Authorization": f"Bearer {key}",
                 "User-Agent": "nba-hackathon-eval/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def run_frame(url_tmpl, model, key, frame_path: Path) -> list[dict]:
    last = None
    for attempt in range(3):
        try:
            return extract_numbers(infer_one(url_tmpl, model, key, frame_path))
        except Exception as e:
            last = e
            time.sleep(2 * (attempt + 1))
    print(f"  {frame_path.name} 失败: {last}", file=sys.stderr)
    return []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="jersey-numbers-i1wn5-xkuck/1",
                    help="Universe 模型 ID；ocr/3 未部署不能 hosted，SwingAI 这个可用")
    ap.add_argument("--endpoint", default=ENDPOINTS[0],
                    help="serverless / detect 接口模板，{model} 占位")
    ap.add_argument("--max-frames", type=int, default=0, help="每剪辑最多评多少帧（0=全量）")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    key = load_key()

    local_clips = sorted(LOCAL.glob("*.json"))
    if not local_clips:
        raise SystemExit("本地基线结果为空，先跑 eval_number_dataset.py")
    (OUT / "clips").mkdir(parents=True, exist_ok=True)

    def run_clip(lc: Path):
        doc = json.loads(lc.read_text())
        clip = doc["clip"]
        out_file = OUT / "clips" / f"{clip}.json"
        if out_file.exists():
            return clip, "exists"
        truth = norm(doc["truth"])
        frames = doc["frames"][: args.max_frames] if args.max_frames else doc["frames"]
        results = {}
        with ThreadPoolExecutor(args.workers) as ex:
            futs = {ex.submit(run_frame, args.endpoint, args.model, key,
                              FRAMES / clip / fr["frame"]): fr["frame"] for fr in frames}
            for fut in as_completed(futs):
                results[futs[fut]] = fut.result()
        rows = []
        for fr in doc["frames"][: len(frames)]:
            reads = results.get(fr["frame"], [])
            nums = [r["n"] for r in reads]
            rows.append({
                "frame": fr["frame"],
                "rf_outcome": "hit" if truth in nums else ("miss" if nums else "no_read"),
                "rf_reads": reads,
                "local_outcome": fr["outcome"],
            })
        out_file.write_text(json.dumps(
            {"clip": clip, "truth": truth, "model": args.model, "frames": rows},
            ensure_ascii=False))
        return clip, "ok"

    for i, lc in enumerate(local_clips, 1):
        name, st = run_clip(lc)
        print(f"[{i}/{len(local_clips)}] {st} {name}", flush=True)

    # ---- 汇总对照 ----
    total = hit = 0
    wins, losses, both_hit, both_miss = 0, 0, 0, 0
    per_clip = []
    for f in sorted((OUT / "clips").glob("*.json")):
        d = json.loads(f.read_text())
        c_hit = sum(1 for r in d["frames"] if r["rf_outcome"] == "hit")
        c_n = len(d["frames"])
        total += c_n
        hit += c_hit
        for r in d["frames"]:
            rf_hit = r["rf_outcome"] == "hit"
            lc_hit = r["local_outcome"] == "hit"
            if rf_hit and lc_hit: both_hit += 1
            elif rf_hit: wins += 1
            elif lc_hit: losses += 1
            else: both_miss += 1
        per_clip.append({"clip": d["clip"], "truth": d["truth"],
                         "rf_hit": c_hit, "frames": c_n,
                         "rf_clip_hit": c_hit > 0})
    summary = {
        "model": args.model,
        "frames": total,
        "rf_hit_rate": round(hit / total, 4) if total else 0,
        "frame_duel": {"both_hit": both_hit, "rf_only": wins, "local_only": losses,
                       "both_miss": both_miss},
        "rf_clip_level_hit": sum(1 for p in per_clip if p["rf_clip_hit"]),
        "clips": len(per_clip),
        "per_clip": per_clip,
    }
    (OUT / "compare_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    print(json.dumps({k: summary[k] for k in ("frames", "rf_hit_rate", "frame_duel",
                                              "rf_clip_level_hit", "clips")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
