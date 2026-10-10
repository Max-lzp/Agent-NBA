#!/usr/bin/env python3
"""把整段视频按回放台的采样交给 Bedrock 视觉模型，单独出一版检测。

不跟本地框混用。模型只回 JSON。坐标是相对画面的 0–1000，这里换算成像素。
号码能对上出场名单才写姓名。中断后按抽样时间续跑，不重复扣已成功的帧。
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import cv2

VISION = Path(__file__).resolve().parent
ROOT = VISION.parents[1]
RESULTS = VISION / "results" / "local"
WORK = VISION / "work" / "vlm"
sys.path.insert(0, str(VISION))

from analyze import read_sampled  # noqa: E402
from converse_frame import bedrock_api_key, converse  # noqa: E402
from roster_map import lookup_number  # noqa: E402

CLIPS = json.loads((VISION / "clips.json").read_text(encoding="utf-8"))
COMPONENTS = {
    item["id"]: item
    for item in json.loads((VISION / "components.json").read_text(encoding="utf-8"))
}

MODELS = {
    "qwen3_vl": "qwen.qwen3-vl-235b-a22b",
    "claude_sonnet": "global.anthropic.claude-sonnet-4-6",
    "kimi_k3": "us.moonshotai.kimi-k3",
}

PROMPT = """这是一帧 NBA 转播画面。只输出一个 JSON 对象，不要解释，不要用 markdown。
bbox 是 [左, 上, 右, 下]，四个整数都在 0 到 1000，相对整幅画面。右要大于左，下要大于上。不要写宽和高。
{"players":[{"role":"player","bbox":[100,200,180,560],"team":null,"jersey":null}],"ball":null,"event":null}
role 只能是 player 或 referee。team 只能是 HOU、DAL 或 null。
jersey 是号码字符串，看不清就 null，不要猜姓名。
event 只能是 pass、interception、shot、rebound 或 null。
裁判是灰衬衫，标成 referee，不要标成球员。看不清的人可以不框。球看不清就 ball 为 null。"""

EVENTS = {"pass", "interception", "shot", "rebound"}
write_lock = threading.Lock()


def component_block(engine: str, extra: str) -> dict:
    item = COMPONENTS[engine]
    note = item["note"]
    if extra:
        note = note + extra
    return {
        "id": item["id"],
        "label": item["label"],
        "repo": item["repo"],
        "license": item["license"],
        "role": item["role"],
        "note": note,
    }


def parse_json(text: str) -> dict | None:
    start = text.find("{")
    if start < 0:
        return None
    chunk = text[start:]
    end = chunk.rfind("}")
    candidates = [chunk[: end + 1]] if end >= 0 else []
    # 输出被长度截断时，留下已经写完的球员对象。
    last_player = chunk.rfind("}")
    if last_player > 0:
        candidates.append(chunk[: last_player + 1] + '],"ball":null,"event":null}')
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and isinstance(data.get("players"), list):
            return data
    return None


def norm_box(value, width: int, height: int) -> list[int] | None:
    """bbox 是 [左, 上, 右, 下]，0 到 1000。"""
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        left, top, right, bottom = [float(v) for v in value]
    except (TypeError, ValueError):
        return None
    left = min(1000.0, max(0.0, left))
    top = min(1000.0, max(0.0, top))
    right = min(1000.0, max(0.0, right))
    bottom = min(1000.0, max(0.0, bottom))
    if right < left:
        left, right = right, left
    if bottom < top:
        top, bottom = bottom, top
    box_w = right - left
    box_h = bottom - top
    if box_w < 8 or box_h < 8:
        return None
    return [
        round(left / 1000 * width),
        round(top / 1000 * height),
        max(2, round(box_w / 1000 * width)),
        max(2, round(box_h / 1000 * height)),
    ]


def jersey_text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in {"", "null", "none", "未知"}:
        return None
    if text.isdigit():
        if text in {"0", "00"}:
            return text
        return str(int(text))
    return None


def people_from_model(data: dict, width: int, height: int, game_id: str, color_map: dict) -> tuple[list[dict], list[int] | None, str | None]:
    players = []
    for item in (data.get("players") or [])[:16]:
        if not isinstance(item, dict):
            continue
        box = norm_box(item.get("bbox"), width, height)
        if box is None:
            continue
        role = item.get("role")
        role = "referee" if role == "referee" else "player"
        team = item.get("team")
        team = team if team in {"HOU", "DAL"} else None
        jersey = jersey_text(item.get("jersey")) if role == "player" else None
        name = None
        if jersey:
            found = lookup_number(game_id, jersey, None, color_map)
            if found.get("name") and not found.get("dnp"):
                name = found["name"]
                team = found.get("team") or team
            elif found.get("source") == "qwen_collision" and team:
                found = lookup_number(game_id, jersey, "white" if color_map.get("white") == team else "dark", color_map)
                if found.get("name") and not found.get("dnp"):
                    name = found["name"]
        players.append(
            {
                "track_id": len(players) + 1,
                "role": role,
                "bbox": box,
                "team": team,
                "jersey": jersey,
                "name": name,
            }
        )
    ball = norm_box(data.get("ball"), width, height)
    event = data.get("event")
    event = event if event in EVENTS else None
    return players, ball, event


def nearest_handler(players: list[dict], ball: list[int] | None) -> int | None:
    if not ball:
        return None
    cx = ball[0] + ball[2] / 2
    cy = ball[1] + ball[3] / 2
    best_id = None
    best_distance = 90 ** 2
    for player in players:
        if player["role"] == "referee":
            continue
        x, y, w, h = player["bbox"]
        if x <= cx <= x + w and y <= cy <= y + h:
            return player["track_id"]
        dx = max(x - cx, 0, cx - (x + w))
        dy = max(y - cy, 0, cy - (y + h))
        distance = dx * dx + dy * dy
        if distance < best_distance:
            best_distance = distance
            best_id = player["track_id"]
    return best_id


def save_jpeg(frame, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    height, width = frame.shape[:2]
    image = frame
    if width > 960:
        scale = 960 / width
        image = cv2.resize(frame, (960, int(height * scale)), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(dest), image, [int(cv2.IMWRITE_JPEG_QUALITY), 80])


def load_done(path: Path) -> dict[float, dict]:
    done = {}
    if not path.is_file():
        return done
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        done[float(row["t"])] = row
    return done


def call_frame(engine: str, image: Path, t: float, width: int, height: int, game_id: str, color_map: dict) -> dict:
    import time

    result = None
    error = None
    for attempt in range(3):
        try:
            result = converse(image, PROMPT, "只输出 JSON。", 1400, MODELS[engine])
            error = None
            break
        except (SystemExit, TimeoutError) as exc:
            error = str(exc)
            if attempt < 2 and any(word in error.lower() for word in ("throttl", "too many", "timeout", "service", "429", "rate")):
                time.sleep(2 ** attempt)
                continue
            return {"t": t, "error": error, "players": [], "ball": None, "event": None, "cost": None}
    if result is None:
        return {"t": t, "error": error or "empty", "players": [], "ball": None, "event": None, "cost": None}
    parsed = parse_json(result["text"])
    if parsed is None:
        players, ball, event = [], None, None
    else:
        players, ball, event = people_from_model(parsed, width, height, game_id, color_map)
    return {
        "t": t,
        "error": None if parsed is not None else "json",
        "raw": None if parsed is not None else result["text"][:500],
        "players": players,
        "ball": ball,
        "event": event,
        "elapsed_ms": result["elapsed_ms"],
        "cost": result["cost"],
    }


def checkpoint(path: Path, row: dict) -> None:
    with write_lock:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def assemble(engine: str, clip_id: str, clip: dict, rows: list[dict], elapsed_sec: float) -> dict:
    rows = sorted(rows, key=lambda item: item["t"])
    events = []
    last_type = None
    last_t = -10.0
    timeline = []
    named = set()
    spent = 0.0
    errors = 0
    for row in rows:
        players = row.get("players") or []
        ball = row.get("ball")
        holder = nearest_handler(players, ball)
        holder_name = next((player.get("name") for player in players if player["track_id"] == holder), None)
        for player in players:
            if player.get("name"):
                named.add(player["name"])
        event = row.get("event")
        if event and (event != last_type or row["t"] - last_t > 1.2):
            actor = holder_name
            events.append(
                {
                    "type": event,
                    "t": row["t"],
                    "actor": actor,
                    "track_id": holder,
                }
            )
            last_type = event
            last_t = row["t"]
        elif not event:
            last_type = None
        cost = row.get("cost") or {}
        if cost.get("total_usd"):
            spent += float(cost["total_usd"])
        if row.get("error"):
            errors += 1
        timeline.append(
            {
                "t": row["t"],
                "players": players,
                "ball": ball,
                "ball_handler_track": holder,
                "ball_handler": holder_name,
                "court_keypoints": 0,
            }
        )
    money = f" 已用 ${spent:.3f}。" if spent else " 费用按 token 另计。"
    note = f" 这次 {len(rows)} 帧，失败 {errors}，墙钟 {elapsed_sec:.0f} 秒。{money}"
    return {
        "clip": clip_id,
        "game_id": clip["game_id"],
        "video": clip["video"],
        "component": component_block(engine, note),
        "source_note": f"{engine} full-clip frames; no local detector",
        "sampled_fps": None,
        "frame_count": len(rows),
        "duration_sec": rows[-1]["t"] if rows else 0,
        "players": [],
        "named_players": sorted(named),
        "events": events,
        "timeline": timeline,
        "vlm": {
            "model_id": MODELS[engine],
            "frames": len(rows),
            "errors": errors,
            "elapsed_sec": round(elapsed_sec, 1),
            "total_usd": round(spent, 4) if spent else None,
        },
    }


def run_clip(engine: str, clip_id: str, target_fps: float, workers: int, max_seconds: float | None) -> None:
    import time

    if engine == "kimi_k3":
        bedrock_api_key()
    clip = CLIPS[clip_id]
    video = (ROOT / clip["video"]).resolve()
    frames, times, effective_fps, _stride = read_sampled(video, target_fps)
    if max_seconds is not None:
        keep = [i for i, t in enumerate(times) if t <= max_seconds]
        frames = [frames[i] for i in keep]
        times = [times[i] for i in keep]
    height, width = frames[0].shape[:2]
    log_path = WORK / f"{clip_id}.{engine}.jsonl"
    done = {
        t: row
        for t, row in load_done(log_path).items()
        if not row.get("error")
    }
    pending = [(frame, t) for frame, t in zip(frames, times) if float(t) not in done]
    print(
        f"{engine} {clip_id}: {len(frames)} frames, {len(pending)} to call, {len(done)} resumed",
        flush=True,
    )
    started = time.perf_counter()

    def task(item):
        frame, t = item
        image = WORK / engine / clip_id / f"t_{t:07.3f}.jpg"
        if not image.is_file():
            save_jpeg(frame, image)
        row = call_frame(engine, image, float(t), width, height, clip["game_id"], clip["jersey_color_to_team"])
        checkpoint(log_path, row)
        cost = (row.get("cost") or {}).get("total_usd")
        money = f" ${cost:.4f}" if cost else ""
        print(
            f"  {engine} {clip_id} t={t:.3f} players={len(row['players'])} "
            f"ball={bool(row['ball'])} event={row.get('event')}{money} err={row.get('error')}",
            flush=True,
        )
        return row

    if pending:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(task, item) for item in pending]
            for future in as_completed(futures):
                future.result()
    done = load_done(log_path)
    rows = [done[float(t)] for t in times if float(t) in done]
    document = assemble(engine, clip_id, clip, rows, time.perf_counter() - started)
    document["sampled_fps"] = round(effective_fps, 3)
    out = RESULTS / f"{clip_id}.{engine}.json"
    out.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    vlm = document["vlm"]
    print(
        f"{out.name}: named={document['named_players']} events={len(document['events'])} "
        f"errors={vlm['errors']} ${vlm['total_usd']} {vlm['elapsed_sec']}s",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="整段视频交给 Bedrock 视觉模型")
    parser.add_argument("--model", choices=["qwen3_vl", "claude_sonnet", "kimi_k3", "both"], default="both")
    parser.add_argument("--clip", choices=["flagg", "amen", "all"], default="all")
    parser.add_argument("--fps", type=float, default=8.0)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--max-seconds", type=float, default=None)
    args = parser.parse_args()
    engines = ["qwen3_vl", "claude_sonnet"] if args.model == "both" else [args.model]
    clips = ["flagg", "amen"] if args.clip == "all" else [args.clip]
    for engine in engines:
        for clip_id in clips:
            run_clip(engine, clip_id, args.fps, args.workers, args.max_seconds)


if __name__ == "__main__":
    main()
