"""同一台机器上跑完追踪、记分牌和成片，再把新文件名的 mp4 和回放数据写进网页桶。

出手、领防和官方预期命中率仍由本机清单补。这里写进页面的是记分牌读数和比分跳动。
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

import track

REGION = os.environ.get("AWS_REGION", "us-east-1")
NAMES = {"DAL": "独行侠", "HOU": "火箭"}
PERIODS = {"1ST": "第一节", "2ND": "第二节", "3RD": "第三节", "4TH": "第四节"}
FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def main() -> None:
    job_id = os.environ.get("JOB_ID", "")
    video = os.environ.get("VIDEO_S3", "")
    output = os.environ.get("OUTPUT_S3", "")
    bucket_name = os.environ.get("MEDIA_BUCKET", "")
    web_bucket = os.environ.get("WEB_BUCKET", "")
    distribution = os.environ.get("DISTRIBUTION_ID", "")
    threads = int(os.environ.get("OMP_NUM_THREADS", "8"))
    if not job_id or not web_bucket:
        raise RuntimeError("缺少 JOB_ID 或 WEB_BUCKET")

    frames = track._track(bucket_name, video, threads)
    body = {
        "jobId": job_id,
        "source": video,
        "device": "cpu",
        "threads": threads,
        "sampled_fps": track.SAMPLE_FPS,
        "tracks": frames,
        "frame_count": len(frames),
        "degraded": False,
    }
    track._put(output, body)
    local_video = Path("/tmp/video.mp4")
    board = _scoreboard(local_video)
    film = Path("/tmp/broadcast.mp4")
    _render(
        local_video,
        board.get("reads") or [],
        film,
        board.get("away") or os.environ.get("AWAY", "DAL"),
        board.get("home") or os.environ.get("HOME_TEAM", "HOU"),
    )
    if not bucket_name:
        raise RuntimeError("缺少 MEDIA_BUCKET，解说无法把原声送去转写")
    spoken = _dub(job_id, local_video, film, bucket_name)
    _publish(job_id, film, board, web_bucket, distribution, spoken)
    print(json.dumps({
        "jobId": job_id,
        "frames": len(frames),
        "film": f"assets/{job_id}-broadcast.mp4",
        "tracks": spoken.get("tracks") if spoken else {},
    }))


def _scoreboard(video: Path) -> dict:
    import sys

    sys.path.insert(0, "/opt/track/vision")
    import scoreboard

    scoreboard.CLIPS["job"] = {
        "video": str(video),
        "away": os.environ.get("AWAY", "DAL"),
        "home": os.environ.get("HOME_TEAM", "HOU"),
    }
    try:
        return scoreboard.derive(scoreboard.read_clip("job"))
    except (SystemExit, Exception) as exc:  # noqa: BLE001 — 记分牌失败仍要交出成片
        print(f"记分牌没读完：{exc}")
        return {"reads": [], "ticks": [], "away": os.environ.get("AWAY", ""), "home": os.environ.get("HOME_TEAM", "")}


def _render(video: Path, reads: list, dest: Path, away: str, home: str) -> None:
    import cv2
    from PIL import Image, ImageDraw, ImageFont

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError("打不开视频")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    width, height = 1280, 720
    font = _font(36)
    err = open("/tmp/ffmpeg.log", "w", encoding="utf-8")
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
        label = _label_at(timed, index / fps, away, home)
        if label and font is not None:
            image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            draw = ImageDraw.Draw(image)
            box = int(getattr(font, "getlength", lambda _text: 28 * len(label))(label)) + 28
            draw.rectangle((24, 18, 36 + box, 70), fill=(0, 0, 0))
            draw.text((36, 24), label, fill=(255, 255, 255), font=font)
            frame = cv2.cvtColor(__import__("numpy").asarray(image), cv2.COLOR_RGB2BGR)
        proc.stdin.write(frame.tobytes())
        index += 1
    cap.release()
    proc.stdin.close()
    code = proc.wait()
    err.close()
    if code != 0 or not dest.is_file():
        log = Path("/tmp/ffmpeg.log").read_text(encoding="utf-8", errors="replace")[-800:]
        raise RuntimeError(f"ffmpeg 退出 {code}：{log}")


def _font(size: int):
    from PIL import ImageFont

    for path in FONT_CANDIDATES:
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _label_at(reads: list, t: float, away_default: str, home_default: str) -> str:
    current = None
    for row in reads:
        if row["t"] <= t + 0.05:
            current = row
        else:
            break
    if not current:
        return ""
    teams = current.get("teams") or (None, None)
    scores = current.get("scores") or [None, None]
    away = (teams[0] if teams else None) or away_default
    home = (teams[1] if teams and len(teams) > 1 else None) or home_default
    period = _period(current.get("period"))
    clock = current.get("clock") or ""
    left = f"{NAMES.get(away, away)} {scores[0]}"
    right = f"{NAMES.get(home, home)} {scores[1]}"
    return "  ".join(part for part in (period, clock, left, right) if part)


def _period(value: str | None) -> str:
    if not value:
        return ""
    if value.startswith("OT"):
        return "加时"
    return PERIODS.get(value, value)


def _dub(job_id: str, source: Path, film: Path, media_bucket: str) -> dict:
    import dub

    print(f"{job_id} 开始做英文原声和中文解说")
    return dub.attach(source, film, Path("/tmp/dub"), media_bucket, job_id)


def _publish(job_id: str, film: Path, board: dict, web_bucket: str, distribution: str, spoken: dict | None = None) -> None:
    s3 = boto3.client("s3", region_name=REGION)
    video_key = f"assets/{job_id}-broadcast.mp4"
    s3.upload_file(
        str(film),
        web_bucket,
        video_key,
        ExtraArgs={"ContentType": "video/mp4"},
    )
    track_keys = []
    for lang, path in ((spoken or {}).get("files") or {}).items():
        key = f"assets/{job_id}-{lang}.mp4"
        s3.upload_file(str(path), web_bucket, key, ExtraArgs={"ContentType": "video/mp4"})
        track_keys.append(key)
    clips = _merge_clips(s3, web_bucket, job_id, video_key, board, spoken)
    body = _clips_js(job_id, clips)
    s3.put_object(
        Bucket=web_bucket,
        Key="replay/clips.js",
        Body=body.encode("utf-8"),
        ContentType="application/javascript; charset=utf-8",
    )
    pages = {
        "game.html": "text/html; charset=utf-8",
        "game.js": "application/javascript; charset=utf-8",
        "styles.css": "text/css; charset=utf-8",
    }
    for name, kind in pages.items():
        path = Path("/opt/track/web") / name
        if path.is_file():
            s3.upload_file(str(path), web_bucket, name, ExtraArgs={"ContentType": kind})
    if distribution:
        items = [f"/{video_key}", "/replay/clips.js", "/game.html", "/game.js", "/styles.css"]
        items.extend(f"/{key}" for key in track_keys)
        boto3.client("cloudfront", region_name=REGION).create_invalidation(
            DistributionId=distribution,
            InvalidationBatch={
                "Paths": {"Quantity": len(items), "Items": items},
                "CallerReference": f"{job_id}-{int(film.stat().st_mtime)}",
            },
        )


def _merge_clips(s3, web_bucket: str, job_id: str, video_key: str, board: dict, spoken: dict | None = None) -> dict:
    text = ""
    try:
        text = s3.get_object(Bucket=web_bucket, Key="replay/clips.js")["Body"].read().decode("utf-8")
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code not in {"NoSuchKey", "404", "NotFound"}:
            raise
    data = _load_clips(text) if text else {}
    for key in ("flagg", "amen"):
        if key in text and key not in data:
            raise RuntimeError(f"现有回放数据里的 {key} 没读出来，停止覆盖")
    data[job_id] = _pack(job_id, video_key, board, spoken)
    return data


def _load_clips(text: str) -> dict:
    marker = "window.DEEPCOURT_CLIPS="
    start = text.find(marker)
    if start < 0:
        raise RuntimeError("回放数据不是预期格式")
    raw = text[start + len(marker):].strip()
    if raw.endswith(";"):
        raw = raw[:-1]
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise RuntimeError("回放数据不是对象")
    return data


def _clips_js(job_id: str, data: dict) -> str:
    lead = ""
    if os.environ.get("HOMEPAGE_LEAD", "1") == "1":
        lead = f'window.DEEPCOURT_LEAD="{job_id}";\n'
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"{lead}window.DEEPCOURT_CLIPS={payload};\n"


def _pack(job_id: str, video_key: str, board: dict, spoken: dict | None = None) -> dict:
    import cv2

    cap = cv2.VideoCapture("/tmp/video.mp4")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    cap.release()
    duration = round(frames / fps, 3) if fps else 0
    away = board.get("away") or os.environ.get("AWAY", "")
    home = board.get("home") or os.environ.get("HOME_TEAM", "")
    scorebug = _scorebug(board.get("reads") or [], away, home)
    events = _events(board.get("ticks") or [])
    title = os.environ.get("TITLE") or _title(scorebug, away, home, job_id)
    anchor = events[0]["t"] if events else 0
    pack = {
        "clip": job_id,
        "title": title,
        "dataset": os.environ.get("DATASET", "云上作业"),
        "video": video_key,
        "width": 1280,
        "height": 720,
        "duration": duration,
        "gameId": os.environ.get("GAME_ID", ""),
        "away": away,
        "home": home,
        "scorebug": scorebug,
        "tags": [],
        "events": events,
        "shots": [],
        "anchor": anchor,
        "voice": (spoken or {}).get("voice") or "比分来自云上记分牌。出手、领防和官方预期命中率仍由本机清单补。",
    }
    if spoken and spoken.get("tracks"):
        pack["tracks"] = spoken["tracks"]
        pack["scriptEn"] = spoken.get("scriptEn") or ""
        pack["scriptZh"] = spoken.get("scriptZh") or ""
    return pack


def _scorebug(reads: list, away: str, home: str) -> list[dict]:
    rows = []
    for row in reads:
        scores = row.get("scores")
        if not row.get("clock") or not scores or len(scores) < 2:
            continue
        teams = row.get("teams") or (away, home)
        item = {
            "t": row["t"],
            "period": _period(row.get("period")),
            "clock": row["clock"],
            "away": teams[0] or away,
            "home": teams[1] or home,
            "awayScore": scores[0],
            "homeScore": scores[1],
            "awayName": NAMES.get(teams[0] or away, teams[0] or away),
            "homeName": NAMES.get(teams[1] or home, teams[1] or home),
        }
        if rows and rows[-1]["clock"] == item["clock"] and rows[-1]["awayScore"] == item["awayScore"] and rows[-1]["homeScore"] == item["homeScore"]:
            continue
        rows.append(item)
    return rows


def _events(ticks: list) -> list[dict]:
    events = []
    for tick in ticks:
        team = tick.get("team") or ""
        events.append({
            "t": tick.get("t_sec") or 0,
            "timecode": "",
            "label": f"{NAMES.get(team, team)} +{tick.get('delta')}",
            "eventType": "score",
            "detail": "记分牌读数",
            "source": "scoreboard",
            "shot": None,
        })
    return events


def _title(scorebug: list, away: str, home: str, job_id: str) -> str:
    if not scorebug:
        return job_id
    first = scorebug[0]
    names = f"{NAMES.get(away, away)}对{NAMES.get(home, home)}".strip("对")
    return " ".join(part for part in (first.get("period"), first.get("clock"), names) if part)


if __name__ == "__main__":
    main()
