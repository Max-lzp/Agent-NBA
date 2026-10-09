#!/usr/bin/env python3
"""球的原始检测掩码：哪些抽帧是模型真看到球，哪些是 analyze.py 插值补的。

analyze.py 输出的 timeline 每帧都有球（interpolate + bfill），事件层分不清真假。
这里按同样的抽帧重跑球检测，只做跳点过滤、不插值，结果写 results/local/{clip}.ball_raw.json，
事件层据此给插值帧降权。不改 analyze.py 的产物。

用法：pipeline/vision/.venv/bin/python pipeline/vision/ball_raw.py --clip all
"""
from __future__ import annotations

import argparse
import json

from analyze import MODELS, RESULTS, ROOT, VISION, quiet_predict, read_sampled

MAX_JUMP_PX = 25          # 与 analyze.py 一致，按 stride 放大


def detect(clip_id: str, clip: dict, target_fps: float) -> dict:
    from ultralytics import YOLO

    frames, times, effective_fps, stride = read_sampled((ROOT / clip["video"]).resolve(), target_fps)
    model = YOLO(str(MODELS / "ball_detector_model.pt"))
    quiet_predict(model)
    ball_cls = {str(v).lower(): k for k, v in model.names.items()}["ball"]

    raw: list[dict | None] = []
    for i in range(0, len(frames), 8):
        for res in model.predict(frames[i:i + 8], conf=0.5):
            best = None
            for box, cls, conf in zip(res.boxes.xyxy.tolist(), res.boxes.cls.tolist(), res.boxes.conf.tolist()):
                if int(cls) == ball_cls and (best is None or conf > best["conf"]):
                    best = {"bbox": [round(v) for v in box], "conf": round(float(conf), 3)}
            raw.append(best)

    last = -1
    for i, det in enumerate(raw):
        if det is None:
            continue
        if last >= 0:
            (ax, ay), (bx, by) = raw[last]["bbox"][:2], det["bbox"][:2]
            if ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5 > MAX_JUMP_PX * stride * (i - last):
                raw[i] = None
                continue
        last = i

    hits = sum(1 for d in raw if d)
    print(f"{clip_id}: {hits}/{len(raw)} 帧真检测到球 ({hits / max(len(raw), 1):.0%}) @ {effective_fps:.2f}fps")
    return {"clip": clip_id, "sampled_fps": round(effective_fps, 3),
            "frames": [{"t": t, "ball": d} for t, d in zip(times, raw)]}


def main() -> None:
    ap = argparse.ArgumentParser(description="球原始检测掩码（不插值）")
    clips = json.loads((VISION / "clips.json").read_text(encoding="utf-8"))
    ap.add_argument("--clip", choices=[*clips, "all"], default="all")
    ap.add_argument("--fps", type=float, default=8.0)
    args = ap.parse_args()
    for clip_id in (clips if args.clip == "all" else [args.clip]):
        out = RESULTS / f"{clip_id}.ball_raw.json"
        out.write_text(json.dumps(detect(clip_id, clips[clip_id], args.fps), ensure_ascii=False),
                       encoding="utf-8")


if __name__ == "__main__":
    main()
