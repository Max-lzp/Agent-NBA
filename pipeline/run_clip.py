#!/usr/bin/env python3
"""一条命令跑一段新视频：登记片段 → 追踪（顺带球掩码）与记分牌并行 → 事件流清单 → 可选上传。

    python3 -m pipeline.run_clip --video "../官方资料/exam.mp4" --away DAL --home HOU --white HOU
    python3 -m pipeline.run_clip --clip exam --job-id <jobId> --upload      # 已登记过，只重跑并上传

产物：pipeline/vision/results/local/{clip}*.json，pipeline/events/out/{clip}.json。
视觉三步用 pipeline/vision/.venv；事件层只用标准库。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VISION = ROOT / "pipeline" / "vision"
RESULTS = VISION / "results" / "local"
OUT = ROOT / "pipeline" / "events" / "out"
CLIPS_FILE = VISION / "clips.json"
VENV_PY = VISION / ".venv" / "bin" / "python"
TEAMS = ("HOU", "DAL")
STACK = "Team04DeepCourt"
PROFILE = os.environ.get("AWS_PROFILE", "team04")
REGION = "us-east-1"


def register(args) -> str:
    clips = json.loads(CLIPS_FILE.read_text(encoding="utf-8"))
    if not args.video:
        if args.clip not in clips:
            sys.exit(f"片段 {args.clip} 没登记过，要给 --video")
        return args.clip
    video = Path(args.video).expanduser().resolve()
    if not video.is_file():
        sys.exit(f"找不到视频：{video}")
    clip_id = args.clip or re.sub(r"[^A-Za-z0-9]+", "_", video.stem).strip("_").lower() or "clip"
    if {args.away, args.home} != set(TEAMS):
        sys.exit("--away / --home 必须是 HOU 和 DAL 各一个（记分牌上客队在左或在上）")
    white = args.white or args.home
    clips[clip_id] = {
        "game_id": args.game_id or clip_id,
        "video": os.path.relpath(video, ROOT),
        "away": args.away,
        "home": args.home,
        "jersey_color_to_team": {"white": white, "dark": args.away if white == args.home else args.home},
    }
    CLIPS_FILE.write_text(json.dumps(clips, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[登记] {clip_id}: {args.away} @ {args.home}，白衣 {white}，{video.name}")
    return clip_id


def _start(cmd: list[str]) -> tuple[subprocess.Popen, float]:
    return subprocess.Popen([str(VENV_PY), *cmd], cwd=VISION, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True), time.time()


def _finish(name: str, proc: subprocess.Popen, start: float) -> bool:
    out, _ = proc.communicate()
    last = [line for line in out.strip().splitlines() if line.strip()][-1:] or [""]
    print(f"[{name}] {time.time() - start:.0f}s  {last[0][:160]}")
    if proc.returncode:
        print(out[-2000:])
    return proc.returncode == 0


def vision(clip_id: str) -> None:
    """追踪（GPU 为主，顺带写球掩码）和记分牌（CPU OCR）并行。"""
    t0 = time.time()
    track = _start(["analyze.py", "--clip", clip_id])
    board = _start(["scoreboard.py", "--clip", clip_id])
    ok = _finish("记分牌", *board)
    ok = _finish("追踪+号码+球掩码", *track) and ok
    print(f"[视觉] 合计 {time.time() - t0:.0f}s")
    if not ok:
        sys.exit("视觉步骤失败，见上面的输出")


def build(clip_id: str, job_id: str) -> Path:
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "lambdas"))
    from pipeline.events.build_stream import run
    import common

    clip = json.loads(CLIPS_FILE.read_text(encoding="utf-8"))[clip_id]
    stream = run(job_id, "full", str(RESULTS / f"{clip_id}.json"), None,
                 str(RESULTS / f"{clip_id}.ball_raw.json"), str(RESULTS / f"{clip_id}.scoreboard.json"))
    stream.video.update({"source": clip["video"], "game_id": clip["game_id"]})
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{clip_id}.json"
    stream.write(str(path))

    board = json.loads((RESULTS / f"{clip_id}.scoreboard.json").read_text(encoding="utf-8"))
    reads = [r for r in board["reads"] if r["clock"]]
    if reads:
        first, last = reads[0], reads[-1]
        full = sum(1 for r in board["reads"] if r["clock"] and r["scores"])
        print(f"[记分牌] {first['period']} {first['clock']} → {last['clock']}，"
              f"比分 {first['scores']} → {last['scores']}（{board['away']} 在前），读全 {full}/{len(board['reads'])}")
    print(f"[记分牌] 得分 {board['ticks']}  进攻钟重置 {[r['t_sec'] for r in board['resets']]}")
    doc = stream.to_dict()
    for poss in doc["possessions"]:
        print(f"[回合] {poss['possession_id']} {poss['offense_team']} {poss['t_start']:.1f}–{poss['t_end']:.1f}s")
    for row in common.timeline_from_stream(doc):
        print(f"   {row['t']:6.2f}s  {row['label']}  ({row['confidence']:.2f})")
    print(f"[清单] {path}")
    return path


def upload(path: Path, job_id: str) -> None:
    bucket = subprocess.run(
        ["aws", "cloudformation", "describe-stacks", "--stack-name", STACK, "--region", REGION,
         "--profile", PROFILE, "--query", "Stacks[0].Outputs[?OutputKey=='MediaBucketName'].OutputValue",
         "--output", "text"], check=True, capture_output=True, text=True).stdout.strip()
    key = f"structured/{job_id}.json"
    subprocess.run(["aws", "s3", "cp", str(path), f"s3://{bucket}/{key}", "--region", REGION,
                    "--profile", PROFILE, "--content-type", "application/json"], check=True)
    print(f"[上传] s3://{bucket}/{key}，回放页 game.html?jobId={job_id}")


def main() -> None:
    ap = argparse.ArgumentParser(description="一条命令跑新视频")
    ap.add_argument("--video", help="视频路径；不给就用 clips.json 里已登记的 --clip")
    ap.add_argument("--clip", help="片段 id，默认取文件名")
    ap.add_argument("--away", choices=TEAMS, help="客队（记分牌左 / 上）")
    ap.add_argument("--home", choices=TEAMS, help="主队")
    ap.add_argument("--white", choices=TEAMS, help="穿白（浅色）球衣的队，默认主队；先看一帧确认")
    ap.add_argument("--game-id", help="官方 gameId，例如 0022500701")
    ap.add_argument("--job-id", help="云上任务 id；默认等于片段 id")
    ap.add_argument("--skip-vision", action="store_true", help="视觉产物已在，只重建清单（调事件规则时用）")
    ap.add_argument("--upload", action="store_true", help="把清单传到 S3 structured/{jobId}.json")
    args = ap.parse_args()

    t0 = time.time()
    clip_id = register(args)
    if not args.skip_vision:
        vision(clip_id)
    path = build(clip_id, args.job_id or clip_id)
    if args.upload:
        upload(path, args.job_id or clip_id)
    print(f"[完成] {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
