#!/usr/bin/env python3
"""按固定 fps 抽帧，文件名用时间戳，并写出帧号 → 秒映射表。

示例：
  python3 pipeline/vision/extract_frames.py --clip flagg
  python3 pipeline/vision/extract_frames.py --clip amen --fps 3
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VISION = Path(__file__).resolve().parent
CLIPS_PATH = VISION / "clips.json"
WORK = VISION / "work"
RESULTS = VISION / "results"

SHOWINFO_RE = re.compile(
    r"n:\s*(?P<n>\d+)\s+pts:\s*\d+\s+pts_time:(?P<pts>[0-9]+(?:\.[0-9]+)?)"
)


def load_clips() -> dict:
    return json.loads(CLIPS_PATH.read_text(encoding="utf-8"))


def resolve_video(rel: str) -> Path:
    path = (ROOT / rel).resolve()
    if not path.is_file():
        raise SystemExit(f"找不到视频: {path}")
    return path


def require_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("未找到 ffmpeg。macOS 可用 brew install ffmpeg。")
    return ffmpeg


def probe_duration(ffprobe: str, video: Path) -> float | None:
    if not ffprobe:
        return None
    proc = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(video),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    try:
        return float(json.loads(proc.stdout)["format"]["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def timestamp_name(t_sec: float) -> str:
    return f"t_{t_sec:09.3f}.png"


def extract(clip_id: str, fps: float) -> Path:
    clips = load_clips()
    if clip_id not in clips:
        raise SystemExit(f"未知 clip: {clip_id}，可选 {', '.join(clips)}")
    clip = clips[clip_id]
    video = resolve_video(clip["video"])
    ffmpeg = require_ffmpeg()
    ffprobe = shutil.which("ffprobe")

    out_dir = WORK / "frames" / clip_id
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    pattern = str(out_dir / "seq_%06d.png")
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-y",
        "-i",
        str(video),
        "-vf",
        f"fps={fps},showinfo",
        "-fps_mode",
        "vfr",
        "-start_number",
        "0",
        pattern,
    ]
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"ffmpeg 抽帧失败，退出码 {proc.returncode}")

    pts = [float(m.group("pts")) for m in SHOWINFO_RE.finditer(proc.stderr)]
    files = sorted(out_dir.glob("seq_*.png"))
    if not files:
        sys.stderr.write(proc.stderr[-4000:])
        raise SystemExit("ffmpeg 没有产出 PNG")

    if len(pts) != len(files):
        pts = [index / fps for index in range(len(files))]
        pts_source = "index/fps"
    else:
        pts_source = "showinfo.pts_time"

    frames = []
    for index, (src, t_sec) in enumerate(zip(files, pts)):
        t_sec = round(t_sec, 3)
        dest = out_dir / timestamp_name(t_sec)
        if dest.exists():
            dest = out_dir / f"t_{t_sec:09.3f}_{index:04d}.png"
        src.rename(dest)
        frames.append(
            {
                "index": index,
                "t_sec": t_sec,
                "filename": dest.name,
            }
        )

    mapping = {
        "clip": clip_id,
        "game_id": clip["game_id"],
        "video": clip["video"],
        "fps": fps,
        "filter": f"fps={fps}",
        "pts_source": pts_source,
        "duration_sec": probe_duration(ffprobe or "", video),
        "frame_count": len(frames),
        "width_hint": 1920,
        "height_hint": 1080,
        "frames": frames,
    }
    map_path = out_dir / "frame_map.json"
    map_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    RESULTS.mkdir(parents=True, exist_ok=True)
    published = RESULTS / f"frame_map_{clip_id}.json"
    published.write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{clip_id}: {len(frames)} frames -> {out_dir}")
    print(f"mapping: {published}")
    return published


def main() -> None:
    parser = argparse.ArgumentParser(description="按时间戳抽帧")
    parser.add_argument("--clip", choices=["flagg", "amen", "all"], default="all")
    parser.add_argument("--fps", type=float, default=3.0)
    args = parser.parse_args()
    if args.fps < 2 or args.fps > 4:
        raise SystemExit("本次基准抽帧限定 2–4 fps")
    targets = ["flagg", "amen"] if args.clip == "all" else [args.clip]
    for clip_id in targets:
        extract(clip_id, args.fps)


if __name__ == "__main__":
    main()
