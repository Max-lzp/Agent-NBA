#!/usr/bin/env python3
"""步骤 2/3 的小样本调用：每段视频取 3 帧。

  python3 pipeline/vision/run_sample.py probe
  python3 pipeline/vision/run_sample.py json
  python3 pipeline/vision/run_sample.py probe --clip flagg --limit 1
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from converse_frame import converse
from roster_map import confirm_document

VISION = Path(__file__).resolve().parent
RESULTS = VISION / "results"
FRAMES = VISION / "work" / "frames"
PROBE_PROMPT = (VISION / "prompts" / "probe_open.txt").read_text(encoding="utf-8")
JSON_SYSTEM = (VISION / "prompts" / "json_system.txt").read_text(encoding="utf-8")
ROSTER = json.loads((VISION / "roster.json").read_text(encoding="utf-8"))


def load_map(clip_id: str) -> dict:
    path = RESULTS / f"frame_map_{clip_id}.json"
    if not path.is_file():
        raise SystemExit(f"缺少 {path}，先跑 extract_frames.py")
    return json.loads(path.read_text(encoding="utf-8"))


def pick_frames(mapping: dict, count: int = 3) -> list[dict]:
    frames = mapping["frames"]
    if len(frames) <= count:
        return frames
    # 避开片头片尾 1 秒，在 20% / 50% / 80% 处取最近帧。
    duration = frames[-1]["t_sec"] or 1
    lo, hi = 1.0, max(1.0, duration - 1.0)
    targets = [lo + (hi - lo) * ratio for ratio in (0.2, 0.5, 0.8)]
    chosen = []
    used = set()
    for target in targets:
        best = min(frames, key=lambda frame: (abs(frame["t_sec"] - target), frame["index"]))
        if best["index"] in used:
            continue
        used.add(best["index"])
        chosen.append(best)
    return chosen[:count]


def roster_brief(game_id: str) -> str:
    game = ROSTER["games"][game_id]
    lines = [
        f"本场 {game['date']} {game['matchup']}，主队 {game['home']}，客队 {game['away']}。",
        "出场名单（号码=短名）：",
    ]
    for team, players in game["active"].items():
        pairs = ", ".join(f"#{num} {name}" for num, name in players.items())
        lines.append(f"{team}: {pairs}")
    lines.append("同号码冲突，必须看球衣颜色：" + "；".join(ROSTER["number_collisions"]))
    return "\n".join(lines)


def json_user_text(mapping: dict, frame: dict) -> str:
    game = ROSTER["games"][mapping["game_id"]]
    return (
        f"frame_t={frame['t_sec']}\n"
        f"home_team={game['home']} away_team={game['away']}\n\n"
        f"{roster_brief(mapping['game_id'])}\n\n"
        "只输出 JSON。"
    )


def extract_json(text: str) -> dict:
    stripped = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", stripped, re.DOTALL)
    if fence:
        stripped = fence.group(1).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end < start:
        raise ValueError("响应里没有 JSON 对象")
    return json.loads(stripped[start : end + 1])


def run(mode: str, clip_id: str, limit: int | None, resume: bool, only: list[str]) -> dict:
    mapping = load_map(clip_id)
    if only:
        by_name = {frame["filename"]: frame for frame in mapping["frames"]}
        missing = [name for name in only if name not in by_name]
        if missing:
            raise SystemExit(f"{clip_id} 没有这些帧: {', '.join(missing)}")
        selected = [by_name[name] for name in only]
    else:
        selected = pick_frames(mapping)
        if limit is not None:
            selected = selected[:limit]
    out_dir = RESULTS / mode
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{clip_id}.json"
    calls = []
    if resume and out_path.is_file():
        previous = json.loads(out_path.read_text(encoding="utf-8"))
        calls = list(previous.get("calls") or [])
        done = {call.get("frame", {}).get("filename") for call in calls}
        selected = [frame for frame in selected if frame["filename"] not in done]
    for frame in selected:
        image = FRAMES / clip_id / frame["filename"]
        if not image.is_file():
            raise SystemExit(f"缺少帧文件 {image}")
        if mode == "probe":
            user_text = (
                f"这帧对应视频时间 {frame['t_sec']:.3f} 秒。\n\n{PROBE_PROMPT}"
            )
            result = converse(image, user_text, system_text=None, max_tokens=700)
        elif mode == "probe-roster":
            user_text = (
                f"这帧对应视频时间 {frame['t_sec']:.3f} 秒。\n\n{PROBE_PROMPT}\n\n"
                "下面是本场名单，只在看清号码和球衣颜色后对照。"
                "看不清号码不要用名单猜人。\n"
                f"{roster_brief(mapping['game_id'])}"
            )
            result = converse(image, user_text, system_text=None, max_tokens=700)
        elif mode == "json":
            result = converse(
                image,
                json_user_text(mapping, frame),
                system_text=JSON_SYSTEM,
                max_tokens=1200,
            )
            raw = result["text"]
            try:
                parsed = extract_json(raw)
                confirmed = confirm_document(mapping["game_id"], parsed)
                result["json_ok"] = True
                result["parsed"] = parsed
                result["confirmed"] = confirmed
            except (ValueError, json.JSONDecodeError) as exc:
                result["json_ok"] = False
                result["json_error"] = str(exc)
        else:
            raise SystemExit(f"未知 mode: {mode}")
        result["clip"] = clip_id
        result["frame"] = frame
        result["mode"] = mode
        calls.append(result)
        payload = {"clip": clip_id, "mode": mode, "calls": calls}
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        cost = result["cost"]
        print(
            f"{mode} {clip_id} {frame['filename']}: "
            f"in={cost['input_tokens']} out={cost['output_tokens']} "
            f"${cost['total_usd']:.4f} {result['elapsed_ms']}ms",
            flush=True,
        )
    payload = {"clip": clip_id, "mode": mode, "calls": calls}
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="小样本单帧调用")
    parser.add_argument("mode", choices=["probe", "probe-roster", "json"])
    parser.add_argument("--clip", choices=["flagg", "amen", "all"], default="all")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--only", action="append", default=[], help="只跑这些文件名，可重复")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    clips = ["flagg", "amen"] if args.clip == "all" else [args.clip]
    for clip_id in clips:
        run(args.mode, clip_id, args.limit, args.resume, args.only)


if __name__ == "__main__":
    sys.path.insert(0, str(VISION))
    main()
