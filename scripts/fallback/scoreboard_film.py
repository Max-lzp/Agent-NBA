#!/usr/bin/env python3
"""只把记分牌烧进成片，不写人名。

给考试应急用。认人、解说或官方数据不能用时，先交这一版。
读 run_clip 已经写出的 pipeline/vision/results/local/{clip}.scoreboard.json。
成片写到 web/assets/{clip}-broadcast.mp4，并把记分牌放进 web/replay/clips.js。

    pipeline/vision/.venv/bin/python scripts/fallback/scoreboard_film.py --clip exam
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAMES = {"DAL": "独行侠", "HOU": "火箭"}
PERIODS = {"1ST": "第一节", "2ND": "第二节", "3RD": "第三节", "4TH": "第四节"}
FONT = ROOT / "web" / "fonts" / "MiSans-Regular.ttf"


def period_label(value: str | None) -> str:
    if not value:
        return ""
    if str(value).startswith("OT"):
        return "加时"
    return PERIODS.get(str(value), str(value))


def label_at(reads: list, t: float, away_default: str, home_default: str) -> str:
    current = None
    for row in reads:
        if float(row.get("t") or 0) <= t + 0.05:
            current = row
        else:
            break
    if not current:
        return ""
    teams = current.get("teams") or (None, None)
    scores = current.get("scores") or [None, None]
    if scores[0] is None or scores[1] is None:
        return ""
    away = (teams[0] if teams else None) or away_default
    home = (teams[1] if len(teams) > 1 else None) or home_default
    period = period_label(current.get("period"))
    clock = current.get("clock") or ""
    left = f"{NAMES.get(away, away)} {scores[0]}"
    right = f"{NAMES.get(home, home)} {scores[1]}"
    return "  ".join(part for part in (period, clock, left, right) if part)


def scorebug(reads: list, away: str, home: str) -> list[dict]:
    rows = []
    for row in reads:
        scores = row.get("scores")
        if not row.get("clock") or not scores or len(scores) < 2 or scores[0] is None:
            continue
        teams = row.get("teams") or (away, home)
        item = {
            "t": row["t"],
            "period": period_label(row.get("period")),
            "clock": row["clock"],
            "away": teams[0] or away,
            "home": (teams[1] if len(teams) > 1 else None) or home,
            "awayScore": scores[0],
            "homeScore": scores[1],
            "awayName": NAMES.get(teams[0] or away, teams[0] or away),
            "homeName": NAMES.get((teams[1] if len(teams) > 1 else None) or home, home),
        }
        if rows and rows[-1]["clock"] == item["clock"] and rows[-1]["awayScore"] == item["awayScore"] and rows[-1]["homeScore"] == item["homeScore"]:
            continue
        rows.append(item)
    return rows


def _font(size: int):
    from PIL import ImageFont

    if FONT.is_file():
        return ImageFont.truetype(str(FONT), size)
    return ImageFont.load_default()


def render(video: Path, reads: list, dest: Path, away: str, home: str) -> None:
    try:
        import cv2
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise SystemExit(f"缺少 {exc.name}。用 pipeline/vision/.venv/bin/python 跑这个脚本。") from exc

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise SystemExit(f"打不开视频：{video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    width, height = 1280, 720
    font = _font(36)
    dest.parent.mkdir(parents=True, exist_ok=True)
    log_file = tempfile.NamedTemporaryFile("w", encoding="utf-8", prefix="deepcourt-ffmpeg-", delete=False)
    log_path = Path(log_file.name)
    err = log_file
    proc = subprocess.Popen(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}", "-r", f"{fps}",
            "-i", "pipe:0",
            "-i", str(video),
            "-map", "0:v:0", "-map", "1:a:0?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", "-movflags", "+faststart",
            str(dest),
        ],
        stdin=subprocess.PIPE,
        stderr=err,
    )
    timed = [row for row in reads if row.get("clock") and row.get("scores")]
    index = 0
    assert proc.stdin is not None
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frame = cv2.resize(frame, (width, height))
        label = label_at(timed, index / fps, away, home)
        if label and font is not None:
            image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            draw = ImageDraw.Draw(image)
            length = int(getattr(font, "getlength", lambda _text: 28 * len(label))(label)) + 28
            draw.rectangle((24, 18, 36 + length, 70), fill=(0, 0, 0))
            draw.text((36, 24), label, fill=(255, 255, 255), font=font)
            frame = cv2.cvtColor(__import__("numpy").asarray(image), cv2.COLOR_RGB2BGR)
        proc.stdin.write(frame.tobytes())
        index += 1
    cap.release()
    proc.stdin.close()
    code = proc.wait()
    err.close()
    log = log_path.read_text(encoding="utf-8", errors="replace")[-800:]
    log_path.unlink(missing_ok=True)
    if code != 0 or not dest.is_file():
        raise SystemExit(f"ffmpeg 退出 {code}：{log}")


def _load_clips(path: Path) -> dict:
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    marker = "window.DEEPCOURT_CLIPS="
    start = text.find(marker)
    if start < 0:
        raise SystemExit(f"回放数据不是预期格式：{path}")
    raw = text[start + len(marker):].strip()
    if raw.endswith(";"):
        raw = raw[:-1]
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise SystemExit("回放数据不是对象")
    return data


def write_pack(clip: str, board: dict, duration: float, game_id: str) -> None:
    away = board.get("away") or "DAL"
    home = board.get("home") or "HOU"
    rows = scorebug(board.get("reads") or [], away, home)
    title = clip
    if rows:
        names = f"{NAMES.get(away, away)}对{NAMES.get(home, home)}"
        title = " ".join(part for part in (rows[0].get("period"), rows[0].get("clock"), names) if part)
    pack = {
        "clip": clip,
        "title": title,
        "dataset": "考试应急",
        "video": f"assets/{clip}-broadcast.mp4",
        "width": 1280,
        "height": 720,
        "duration": round(duration, 3),
        "gameId": game_id,
        "away": away,
        "home": home,
        "scorebug": rows,
        "tags": [],
        "events": [],
        "shots": [],
        "anchor": 0,
        "voice": "这一版只烧了记分牌。人名和解说随后补。",
    }
    path = ROOT / "web" / "replay" / "clips.js"
    data = _load_clips(path)
    data[clip] = pack
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"window.DEEPCOURT_CLIPS={body};\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="只烧记分牌的考试成片")
    parser.add_argument("--clip", required=True)
    parser.add_argument("--video", default="", help="不填就用 clips.json 里这个片名的视频")
    parser.add_argument("--game-id", default="")
    args = parser.parse_args(argv)
    board_path = ROOT / "pipeline" / "vision" / "results" / "local" / f"{args.clip}.scoreboard.json"
    if not board_path.is_file():
        raise SystemExit(f"没有记分牌读数：{board_path}。先跑 python3 -m pipeline.run_clip")
    board = json.loads(board_path.read_text(encoding="utf-8"))
    video = Path(args.video).expanduser() if args.video else None
    if video is None:
        clips = json.loads((ROOT / "pipeline" / "vision" / "clips.json").read_text(encoding="utf-8"))
        if args.clip not in clips:
            raise SystemExit(f"clips.json 里没有 {args.clip}")
        video = Path(clips[args.clip]["video"])
        if not video.is_absolute():
            video = (ROOT / video).resolve()
    if not video.is_file():
        raise SystemExit(f"找不到视频：{video}")
    dest = ROOT / "web" / "assets" / f"{args.clip}-broadcast.mp4"
    away = board.get("away") or "DAL"
    home = board.get("home") or "HOU"
    render(video, board.get("reads") or [], dest, away, home)
    import cv2

    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    cap.release()
    duration = frames / fps if fps else 0
    game_id = args.game_id or board.get("teams_from_game") or ""
    write_pack(args.clip, board, duration, game_id)
    print(f"只烧记分牌：{dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
