"""同一台机器上跑完追踪、记分牌、认人、事件清单和成片，再把新文件名的 mp4 和回放数据写进网页桶。"""

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
TEAM_RGB = {"HOU": (206, 17, 65), "DAL": (0, 100, 177)}
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

    clock = _Clock()
    _fetch_extras(bucket_name)
    local_video = Path("/tmp/video.mp4")
    frames, board = None, None
    if os.environ.get("REUSE_TRACKS") == "1" or os.environ.get("RESUME_FROM") == "identity":
        frames, board = _reuse(bucket_name, video, output, job_id)
    if frames is None:
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
        _cache_put(bucket_name, job_id, "tracks.json", body)
    clock.lap("追踪")
    if board is None:
        board = _scoreboard(local_video)
        _save_board(output, board)
        _cache_put(bucket_name, job_id, "board.json", {k: board.get(k) for k in ("reads", "ticks", "resets", "away", "home")})
    clock.lap("记分牌")
    linked = _reuse_linked(bucket_name, job_id) if os.environ.get("REUSE_LINKED") == "1" else None
    if not linked:
        linked = _link(job_id, local_video, frames, board, bucket_name)
    clock.lap("认人和事件")
    if os.environ.get("SKIP_PUBLISH") == "1":
        still = Path("/tmp/t1.jpg")
        _still(local_video, linked.get("tags") or [], still, 1.0)
        still_key = ""
        if bucket_name and still.is_file():
            still_key = f"identity/{job_id}-t1.jpg"
            try:
                boto3.client("s3", region_name=REGION).upload_file(
                    str(still), bucket_name, still_key, ExtraArgs={"ContentType": "image/jpeg"},
                )
            except ClientError as exc:
                print(f"第 1 秒截图没写上：{exc}")
                still_key = ""
        print(json.dumps({
            "jobId": job_id,
            "frames": len(frames),
            "publish": "skipped",
            "gameId": linked.get("gameId"),
            "bedrockCalls": linked.get("bedrockCalls"),
            "identitySeconds": linked.get("identitySeconds"),
            "firstSecond": linked.get("firstSecond"),
            "firstSecondDetecting": linked.get("firstSecondDetecting"),
            "still": still_key,
        }, ensure_ascii=False))
        return
    film = Path("/tmp/broadcast.mp4")
    render_args = (
        local_video,
        board.get("reads") or [],
        film,
        board.get("away") or os.environ.get("AWAY", "DAL"),
        board.get("home") or os.environ.get("HOME_TEAM", "HOU"),
        _live_tags(linked.get("tags") or [], board),
        [] if _has_highlights() else linked.get("cards") or [],
    )
    if not bucket_name:
        raise RuntimeError("缺少 MEDIA_BUCKET，解说无法把原声送去转写")
    script = linked.get("script") or []
    spoken = _render_and_voice(job_id, render_args, script, board, linked) if script else None
    if spoken is None:
        if not film.is_file():
            _render(*render_args)
            linked["events"] = (linked.get("events") or []) + _highlights(job_id, film, board, linked)
        spoken = _dub(job_id, local_video, film, bucket_name, script)
    clock.lap("压片和音轨")
    if os.environ.get("PREVIEW_ONLY") == "1":
        _preview(job_id, film, spoken, linked, bucket_name)
        clock.lap("预览上传")
        print(json.dumps({"jobId": job_id, "seconds": clock.steps, "total": clock.total()}, ensure_ascii=False))
        return
    _publish(job_id, film, board, web_bucket, distribution, spoken, linked)
    clock.lap("发布")
    print(json.dumps({
        "jobId": job_id,
        "frames": len(frames),
        "film": f"assets/{job_id}-broadcast.mp4",
        "tracks": spoken.get("tracks") if spoken else {},
        "seconds": clock.steps,
        "total": clock.total(),
    }, ensure_ascii=False))


class _Clock:
    def __init__(self) -> None:
        import time

        self._time = time.time
        self.start = self.last = self._time()
        self.steps: dict[str, float] = {}

    def lap(self, name: str) -> None:
        now = self._time()
        self.steps[name] = round(now - self.last, 1)
        self.last = now
        print(f"[耗时] {name} {self.steps[name]} 秒，累计 {round(now - self.start, 1)} 秒")

    def total(self) -> float:
        return round(self._time() - self.start, 1)


def _board_key(output: str) -> tuple[str, str]:
    bucket, key = output.removeprefix("s3://").split("/", 1)
    return bucket, key.rsplit("/", 1)[0] + "/board.json"


def _save_board(output: str, board: dict) -> None:
    if not output.startswith("s3://"):
        return
    bucket, key = _board_key(output)
    try:
        boto3.client("s3", region_name=REGION).put_object(
            Bucket=bucket, Key=key, ContentType="application/json",
            Body=json.dumps({k: board.get(k) for k in ("reads", "ticks", "resets", "away", "home")}, ensure_ascii=False).encode(),
        )
    except ClientError as exc:
        print(f"记分牌读数没存上：{exc}")


EXTRA_FILES = {  # 作业定义的引导脚本只拉固定几份文件，精彩回放模块和它要的赛季数据在这里补上
    "bootstrap/track/highlights.py": "track/highlights.py",
    "bootstrap/track/card_layout.py": "track/card_layout.py",
    "bootstrap/track/data/season/2025-26/player_shot_zones.csv": "data/season/2025-26/player_shot_zones.csv",
    "bootstrap/track/data/season/2025-26/player_defensive_shooting.csv": "data/season/2025-26/player_defensive_shooting.csv",
    "bootstrap/track/data/season/2025-26/player_shooting_three_bands.csv": "data/season/2025-26/player_shooting_three_bands.csv",
    "bootstrap/track/data/names/zh-names.json": "data/names/zh-names.json",
}


def _fetch_extras(bucket: str) -> None:
    if not bucket:
        return
    s3 = boto3.client("s3", region_name=REGION)
    base = Path(__file__).resolve().parents[1]
    for key, rel in EXTRA_FILES.items():
        dest = base / rel
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(bucket, key, str(dest))
        except (ClientError, OSError) as exc:
            print(f"没拉到 {key}：{exc}")


def _live_tags(tags: list, board: dict) -> list:
    """比赛钟停住、一直到片尾都不再走，就是死球；停表 2 秒以后转播切坐地、特写和慢镜头，不再画名签。"""
    reads = [read for read in board.get("reads") or [] if read.get("clock")]
    if not reads:
        return tags
    final, t_first = reads[-1]["clock"], float(reads[-1]["t"])
    for read in reversed(reads):
        if read["clock"] != final:
            break
        t_first = float(read["t"])
    if float(reads[-1]["t"]) - t_first < 3.0:
        return tags
    cut = t_first + 1.0 + 2.0
    print(f"比赛钟在 {t_first + 1.0:.2f} 秒前停住，{cut:.2f} 秒以后不画名签")
    return [frame for frame in tags if float(frame.get("t") or 0) <= cut]


def _has_highlights() -> bool:
    """回放从成片里截，卡片由回放定格时贴；有回放模块时成片本身不再烧卡，免得定格里出现两层卡。"""
    if os.environ.get("SKIP_HIGHLIGHTS") == "1":
        return False
    return (Path(__file__).resolve().parent / "highlights.py").is_file()


def _highlights(job_id: str, film: Path, board: dict, linked: dict, replay_video: Path | None = None) -> list:
    """成片后面接精彩回放：每段 5 秒，慢放到球离手那一帧再定格，卡片左右分列。失败就只发原片。"""
    if not _has_highlights():
        return []
    try:
        import highlights
    except Exception as exc:  # noqa: BLE001
        print(f"精彩回放模块没加载：{exc}")
        return []
    game = linked.get("gameId") or ""
    games = Path("/tmp/official/2025-26/games")
    pbp = games / f"{game}_playbyplay.json"
    if not game or not pbp.is_file():
        print("没有这场的逐回合，跳过精彩回放")
        return []
    out = Path("/tmp/broadcast-highlights.mp4")

    def opt(name: str):
        path = games / f"{game}_{name}.json"
        return str(path) if path.is_file() else None

    try:
        doc = highlights.render_highlights(
            film, board, str(pbp),
            replay_video=replay_video if replay_video and replay_video.is_file() else None,
            shotquality=opt("shotqualityshotlog"),
            matchups=opt("boxscorematchupsv3"),
            boxscore=opt("boxscore"),
            roster_stats=[str(path) for path in sorted(Path("/tmp/official/2025-26/rosters").glob("leaguedash*.json"))],
            out_video=out,
            out_json=Path(f"/tmp/{job_id}-highlights.json"),
        )
    except Exception as exc:  # noqa: BLE001 — 回放失败只发原片
        print(f"精彩回放没做出来：{exc}")
        return []
    if not doc.get("highlights") or not out.is_file():
        print("片里没有可回放的瞬间")
        return []
    expected = float(doc.get("prefixDuration") or 0) + highlights.MIN_REPLAY_S * len(doc["highlights"])
    got = float(highlights._probe(out)[0] or 0)
    if abs(got - expected) > 1.5:
        print(f"回放成片时长 {got} 秒，应为约 {expected} 秒，照发原片")
        return []
    os.replace(out, film)
    print("精彩回放：", [(item.get("type"), (item.get("segment") or {}).get("renderedDuration")) for item in doc["highlights"]])
    return list(doc.get("events") or [])


REUSE_PREFIX = "commentary/asr/reuse"  # 任务角色在这个前缀上能写也能读


def _reuse_linked(bucket: str, job_id: str) -> dict | None:
    """复用上一次的认人和事件结果（identity、structured），不再调模型。读不到就返回空，照常认人。"""
    source_job = os.environ.get("REUSE_FROM_JOB") or job_id
    s3 = boto3.client("s3", region_name=REGION)
    try:
        stream = json.loads(s3.get_object(Bucket=bucket, Key=f"{REUSE_PREFIX}/{source_job}/structured.json")["Body"].read())
        identity = json.loads(s3.get_object(Bucket=bucket, Key=f"{REUSE_PREFIX}/{source_job}/identity.json")["Body"].read())
        import chain

        chain._download_official(s3, bucket)
        linked = {
            "script": stream.get("script") or [],
            "cards": stream.get("cards") or [],
            "events": chain._page_events(stream),
            "gameId": stream.get("gameId") or "",
            "tags": identity.get("tags") or [],
            "firstSecond": identity.get("firstSecond"),
            "bedrockCalls": 0,
            "identitySeconds": 0,
        }
    except Exception as exc:  # noqa: BLE001 — 读不到就照常认人
        print(f"没复用上认人和事件结果，照常认人：{exc}")
        return None
    print(f"复用了作业 {source_job} 的认人和事件结果：{len(linked['events'])} 个事件，{len(linked['script'])} 句解说")
    return linked


def _cache_put(bucket: str, job_id: str, name: str, body: dict) -> None:
    if not bucket:
        return
    try:
        boto3.client("s3", region_name=REGION).put_object(
            Bucket=bucket, Key=f"{REUSE_PREFIX}/{job_id}/{name}", ContentType="application/json",
            Body=json.dumps(body, ensure_ascii=False).encode(),
        )
    except ClientError as exc:
        print(f"复用缓存没写上 {name}：{exc}")


def _reuse(bucket_name: str, video: str, output: str, job_id: str):
    """复用上一次作业留在 S3 的轨迹和记分牌读数。找不到轨迹就退回完整重跑。"""
    if not video.startswith("s3://") or not bucket_name:
        return None, None
    s3 = boto3.client("s3", region_name=REGION)
    source_job = os.environ.get("REUSE_FROM_JOB") or job_id
    places = [(bucket_name, f"{REUSE_PREFIX}/{source_job}/tracks.json", f"{REUSE_PREFIX}/{source_job}/board.json")]
    explicit = os.environ.get("REUSE_FROM_OUTPUT") or output
    if explicit.startswith("s3://"):
        bucket, key = explicit.removeprefix("s3://").split("/", 1)
        places.append((bucket, key, _board_key(explicit)[1]))
    body, where, board_key = None, "", ""
    for bucket, key, b_key in places:
        try:
            body = json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
            where, board_key = f"s3://{bucket}/{key}", b_key
            break
        except ClientError as exc:
            print(f"读不到 s3://{bucket}/{key}：{exc.response.get('Error', {}).get('Code')}")
    frames = (body or {}).get("tracks") or []
    if not frames:
        print("没找到可复用的追踪文件，改为完整重跑")
        return None, None
    video_bucket, video_key = video.removeprefix("s3://").split("/", 1)
    s3.download_file(video_bucket, video_key, "/tmp/video.mp4")
    board = None
    try:
        board = json.loads(s3.get_object(Bucket=bucket_name if board_key.startswith(REUSE_PREFIX) else where.removeprefix("s3://").split("/", 1)[0],
                                         Key=board_key)["Body"].read())
    except ClientError:
        print("没找到上一次的记分牌读数，记分牌重读一遍")
    print(f"复用了作业 {body.get('jobId') or source_job} 的追踪文件 {where}（{len(frames)} 帧），记分牌{'也复用' if board else '重读'}；本次作业 {job_id}")
    _cache_put(bucket_name, job_id, "tracks.json", body)
    return frames, board


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


def _render(video: Path, reads: list, dest: Path, away: str, home: str, tags: list | None = None,
            cards: list | None = None) -> None:
    import cv2
    from PIL import Image, ImageDraw

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError("打不开视频")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    width, height = 1280, 720
    font = _font(36)
    tag_font = _font(22)
    err = open("/tmp/ffmpeg.log", "w", encoding="utf-8")
    proc = subprocess.Popen(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}", "-r", f"{fps}",
            "-i", "pipe:0",
            "-i", str(video),
            "-map", "0:v:0", "-map", "1:a:0?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", os.environ.get("RENDER_CRF", "16"), "-pix_fmt", "yuv420p",
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
        frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_LANCZOS4 if frame.shape[1] > width else cv2.INTER_LINEAR)
        label = _label_at(timed, index / fps, away, home)
        players = _players_at(tags or [], index / fps)
        showing = [card for card in cards or [] if card["t0"] <= index / fps <= card["t1"]]
        if (label or players or showing) and (font is not None or tag_font is not None):
            image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            draw = ImageDraw.Draw(image)
            if label and font is not None:
                box = int(getattr(font, "getlength", lambda _text: 28 * len(label))(label)) + 28
                draw.rectangle((24, 18, 36 + box, 70), fill=(0, 0, 0))
                draw.text((36, 24), label, fill=(255, 255, 255), font=font)
            _draw_player_tags(draw, tag_font or font, players)
            for card in showing:
                try:
                    _draw_cards(draw, tag_font or font, card, players)
                except Exception as exc:  # noqa: BLE001 — 卡片画不出不影响成片
                    print(f"定格卡没画上：{exc}")
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


def _draw_cards(draw, font, card: dict, players: list) -> None:
    """进攻卡放远离篮筐的一侧，防守卡放靠近篮筐的一侧；从上角往下找不压球员、不压记分牌的位置。"""
    hoop = card.get("hoop") or "right"
    sides = {"offense": "left" if hoop == "right" else "right", "defense": hoop}
    boxes = [(p["x"], p["y"], p["x"] + p["w"], p["y"] + p["h"]) for p in players]
    placed = [(24, 18, 640, 74)]
    for role in ("offense", "defense"):
        body = card.get(role)
        if not body:
            continue
        rows = [body["name"]] + list(body.get("lines") or []) + ([body["rank"]] if body.get("rank") else [])
        width = max(draw.textbbox((0, 0), row, font=font)[2] for row in rows) + 30
        height = 30 * len(rows) + (26 if body.get("stars") else 0) + 14
        x = 24 if sides[role] == "left" else 1280 - width - 24
        best, best_cost = None, None
        for y in range(84, 720 - height - 20, 34):
            rect = (x, y, x + width, y + height)
            cost = sum(_area(rect, other) for other in boxes) + 10 * sum(_area(rect, other) for other in placed)
            if best_cost is None or cost < best_cost:
                best, best_cost = rect, cost
            if cost == 0:
                break
        if best is None:
            continue
        placed.append(best)
        _paint_card(draw, font, best, rows, body)


def _area(a: tuple, b: tuple) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def _paint_card(draw, font, rect: tuple, rows: list, body: dict) -> None:
    left, top, right, bottom = rect
    edge = TEAM_RGB.get(body.get("team") or "", (241, 104, 30))
    draw.rectangle(rect, fill=(20, 17, 14))
    draw.rectangle((left, top, left + 6, bottom), fill=edge)
    y = top + 8
    for index, row in enumerate(rows):
        draw.text((left + 16, y), row, font=font, fill=(255, 255, 255) if index == 0 else (225, 218, 205))
        y += 30
    if body.get("stars"):
        _paint_stars(draw, left + 16, y + 2, 18, float(body["stars"]))


def _paint_stars(draw, x: float, y: float, size: float, rating: float) -> None:
    import math

    for k in range(5):
        cx, cy, r = x + k * (size + 4) + size / 2, y + size / 2, size / 2
        points = [
            (cx + (r if i % 2 == 0 else r * 0.45) * math.sin(math.pi * i / 5),
             cy - (r if i % 2 == 0 else r * 0.45) * math.cos(math.pi * i / 5))
            for i in range(10)
        ]
        fill = min(max(rating - k, 0.0), 1.0)
        draw.polygon(points, fill=(241, 196, 15) if fill >= 1 else ((150, 120, 40) if fill > 0 else (88, 80, 70)))


def _still(video: Path, tags: list, dest: Path, second: float) -> None:
    import cv2
    from PIL import Image, ImageDraw

    cap = cv2.VideoCapture(str(video))
    cap.set(cv2.CAP_PROP_POS_MSEC, second * 1000)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        return
    frame = cv2.resize(frame, (1280, 720))
    image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(image)
    font = _font(22)
    _draw_player_tags(draw, font, _players_at(tags, second))
    image.save(dest, quality=90)


def _players_at(tags: list, t: float) -> list:
    found = None
    for frame in tags:
        if float(frame.get("t") or 0) <= t + 0.08:
            found = frame
        else:
            break
    players = [player for player in ((found or {}).get("players") or []) if float(player.get("h") or 0) >= 48]
    players.sort(key=lambda player: player.get("label") == "Agent detecting")
    return players


def _draw_player_tags(draw, font, players: list) -> None:
    """名签贴在框顶上方 12 像素，最多上挪一行，不高过场上最高那个人的头顶。Agent detecting 放不下就不画。"""
    if font is None or not players:
        return
    ceiling = min(float(player.get("y") or 0) for player in players)
    placed = []
    for player in players:
        rect = _tag_slot(draw, font, player, placed, ceiling)
        if rect is None:
            if player.get("label") == "Agent detecting":
                continue
            rect = _tag_slot(draw, font, player, [], ceiling)
            if rect is None:
                continue
        placed.append(rect)
        _paint_tag(draw, font, player, rect)


def _tag_slot(draw, font, player: dict, placed: list, ceiling: float):
    text = player.get("label") or player.get("name") or ""
    if not text:
        return None
    box = draw.textbbox((0, 0), text, font=font)
    width = box[2] - box[0] + 16
    height = box[3] - box[1] + 8
    x = float(player.get("x") or 0)
    y = float(player.get("y") or 0)
    w = float(player.get("w") or 0)
    center = x + (w - width) / 2
    floor = max(4.0, ceiling - 2 * height - 16)
    for row in range(2):
        top = y - height - 12 - row * (height + 4)
        if top < floor:
            break
        for left in (center, center - width / 2, center + width / 2):
            if left < 0 or left + width > 1280:
                continue
            rect = (left, top, left + width, top + height)
            if not any(_overlap(rect, other) for other in placed):
                return rect
    return None


def _overlap(a: tuple, b: tuple) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _paint_tag(draw, font, player: dict, rect: tuple) -> None:
    text = player.get("label") or player.get("name") or ""
    edge = (241, 196, 15) if player.get("role") == "defender" else TEAM_RGB.get(player.get("team") or "", (241, 104, 30))
    left, top, right, bottom = rect
    box = draw.textbbox((0, 0), text, font=font)
    draw.rectangle(rect, fill=(20, 17, 14))
    draw.rectangle((left, top, left + 4, bottom), fill=edge)
    draw.text((left + 8, top + 3 - box[1]), text, font=font, fill=(243, 237, 226))


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


def _link(job_id: str, video: Path, frames: list, board: dict, bucket: str) -> dict:
    if not bucket:
        return {}
    try:
        import chain

        return chain.link(job_id, video, frames, board, bucket)
    except Exception as exc:  # noqa: BLE001 — 认人或官方数据失败仍要交出成片
        print(f"认人、事件或官方数据没写完：{exc}")
        import traceback
        traceback.print_exc()
        return {}


def _dub(job_id: str, source: Path, film: Path, media_bucket: str, script: list | None = None) -> dict:
    import dub

    if script:
        print(f"{job_id} 按事件解说稿配中英文音轨，共 {len(script)} 句")
        try:
            return dub.attach_script(source, film, Path("/tmp/dub"), job_id, script)
        except Exception as exc:  # noqa: BLE001 — 解说稿配音失败就退回转写原解说
            print(f"解说稿配音失败，改用原解说转写：{exc}")
    print(f"{job_id} 开始做英文原声和中文解说")
    return dub.attach(source, film, Path("/tmp/dub"), media_bucket, job_id)


def _render_and_voice(job_id: str, render_args: tuple, script: list, board: dict, linked: dict) -> dict | None:
    """成片和中英文两条配音同时做：配音只要原声和解说稿，不用等成片。成片出来后两支带配音的 mp4 同时封装。"""
    from concurrent.futures import ThreadPoolExecutor

    import dub

    mod = dub._tracks()
    source, film = render_args[0], render_args[2]
    work = Path("/tmp/dub")
    work.mkdir(parents=True, exist_ok=True)
    duration = mod.media_duration(source)
    print(f"{job_id} 成片和中英文配音同时开始，解说稿 {len(script)} 句")
    replay_src = Path("/tmp/replay-source.mp4")
    replay_args = (source, [], replay_src, render_args[3], render_args[4], render_args[5], [])
    langs = [lang for lang in os.environ.get("VOICE_LANGS", "en").split(",") if lang in {"zh", "en"}]
    with ThreadPoolExecutor(4) as pool:
        render = pool.submit(_render, *render_args)
        replay = pool.submit(_render, *replay_args)
        voices = {
            lang: pool.submit(_voice_track, mod, source, work, script, lang, voice, code, duration)
            for lang, voice, code in (("zh", mod.VOICE_ID, "cmn-CN"), ("en", mod.EN_VOICE_ID, "en-US")) if lang in langs
        }
        render.result()
        try:
            replay.result()
        except Exception as exc:  # noqa: BLE001 — 回放源没做出来就用成片截
            print(f"回放源片没做出来：{exc}")
        linked["events"] = (linked.get("events") or []) + _highlights(job_id, film, board, linked, replay_src)
        try:
            tracks = {lang: future.result() for lang, future in voices.items()}
        except Exception as exc:  # noqa: BLE001 — 配音失败就退回转写原解说，仍然三支一起发
            print(f"解说稿配音失败，改用原解说转写：{exc}")
            return None
    outs = {lang: work / f"{lang}.mp4" for lang in tracks}
    procs = {
        lang: subprocess.Popen([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(film), "-i", str(track),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
            "-movflags", "+faststart", str(outs[lang]),
        ])
        for lang, track in tracks.items()
    }
    failed = [lang for lang, proc in procs.items() if proc.wait() != 0 or not outs[lang].is_file()]
    if failed:
        raise RuntimeError(f"带配音的成片没封装出来：{failed}")
    return {
        "scriptEn": " ".join((row.get("en") or "").strip() for row in script),
        "scriptZh": "".join((row.get("zh") or "").strip() for row in script),
        "tracks": {lang: f"assets/{job_id}-{lang}.mp4" for lang in outs},
        "voice": "中文和英文解说按事件时间轴写稿，Amazon Polly 配音，原片声音压低只留现场环境声。",
        "files": outs,
    }


def _voice_track(mod, source: Path, work: Path, script: list, lang: str, voice: str, code: str, duration: float) -> Path:
    polly = boto3.client("polly", region_name=REGION)
    placed = []
    for index, line in enumerate(script):
        text = (line.get(lang) or "").strip()
        if not text:
            continue
        speech = work / f"{lang}-line-{index}.mp3"
        audio = polly.synthesize_speech(Engine=mod.ENGINE, LanguageCode=code, VoiceId=voice,
                                        OutputFormat="mp3", Text=text, TextType="text")
        speech.write_bytes(audio["AudioStream"].read())
        nxt = next((float(row["t"]) for row in script[index + 1:] if (row.get(lang) or "").strip()), duration)
        placed.append((speech, float(line["t"]), max(0.6, min(nxt, duration) - float(line["t"]) - 0.05)))
    track = work / f"{lang}.m4a"
    mod._mix_over(source, placed, duration, track)
    return track


def _preview(job_id: str, film: Path, spoken: dict, linked: dict, bucket: str) -> None:
    """只预览：成片、两条音轨和数据写进媒体桶 identity/，不碰网页桶。"""
    s3 = boto3.client("s3", region_name=REGION)
    keys = {"broadcast": f"identity/{job_id}-broadcast.mp4"}
    s3.upload_file(str(film), bucket, keys["broadcast"], ExtraArgs={"ContentType": "video/mp4"})
    for lang, path in ((spoken or {}).get("files") or {}).items():
        keys[lang] = f"identity/{job_id}-{lang}.mp4"
        s3.upload_file(str(path), bucket, keys[lang], ExtraArgs={"ContentType": "video/mp4"})
    print(json.dumps({
        "jobId": job_id,
        "preview": keys,
        "events": [(row.get("t"), row.get("label")) for row in linked.get("events") or []],
        "scriptZh": (spoken or {}).get("scriptZh"),
    }, ensure_ascii=False))


def _publish(job_id: str, film: Path, board: dict, web_bucket: str, distribution: str, spoken: dict | None = None, linked: dict | None = None) -> None:
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
    clips = _merge_clips(s3, web_bucket, job_id, video_key, board, spoken, linked)
    body = _clips_js(job_id, clips)
    _verify_upload(s3, web_bucket, {video_key: film, **{f"assets/{job_id}-{lang}.mp4": path for lang, path in ((spoken or {}).get("files") or {}).items()}})
    s3.put_object(
        Bucket=web_bucket,
        Key="replay/clips.js",
        Body=body.encode("utf-8"),
        ContentType="application/javascript; charset=utf-8",
    )
    if s3.get_object(Bucket=web_bucket, Key="replay/clips.js")["Body"].read() != body.encode("utf-8"):
        raise RuntimeError("replay/clips.js 下载回来和本地不一致")
    print(f"已核对 replay/clips.js：{len(body.encode('utf-8'))} 字节一致")
    # 只发成片和片单。页面文件由仓库部署，作业里的副本可能是旧的，不能覆盖线上。
    if distribution:
        items = [f"/{video_key}", "/replay/clips.js"]
        items.extend(f"/{key}" for key in track_keys)
        boto3.client("cloudfront", region_name=REGION).create_invalidation(
            DistributionId=distribution,
            InvalidationBatch={
                "Paths": {"Quantity": len(items), "Items": items},
                "CallerReference": f"{job_id}-{int(film.stat().st_mtime)}",
            },
        )


def _verify_upload(s3, bucket: str, files: dict) -> None:
    """传完下载回来逐字节比对，对不上就报错，不往下发片单。"""
    import filecmp

    for key, path in files.items():
        check = Path("/tmp/verify") / Path(key).name
        check.parent.mkdir(parents=True, exist_ok=True)
        s3.download_file(bucket, key, str(check))
        if not filecmp.cmp(str(check), str(path), shallow=False):
            raise RuntimeError(f"{key} 下载回来和本地不一致")
        print(f"已核对 {key}：{check.stat().st_size} 字节一致")


def _merge_clips(s3, web_bucket: str, job_id: str, video_key: str, board: dict, spoken: dict | None = None, linked: dict | None = None) -> dict:
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
    data[job_id] = _pack(job_id, video_key, board, spoken, linked)
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


def _pack(job_id: str, video_key: str, board: dict, spoken: dict | None = None, linked: dict | None = None) -> dict:
    import cv2

    cap = cv2.VideoCapture("/tmp/broadcast.mp4" if Path("/tmp/broadcast.mp4").is_file() else "/tmp/video.mp4")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    cap.release()
    duration = round(frames / fps, 3) if fps else 0
    away = board.get("away") or os.environ.get("AWAY", "")
    home = board.get("home") or os.environ.get("HOME_TEAM", "")
    scorebug = _scorebug(board.get("reads") or [], away, home)
    events = (linked or {}).get("events") or _events(board.get("ticks") or [])
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
        "gameId": (linked or {}).get("gameId") or os.environ.get("GAME_ID", ""),
        "away": away,
        "home": home,
        "scorebug": scorebug,
        "tags": (linked or {}).get("tags") or [],
        "events": events,
        "shots": [row for row in events if row.get("eventType") == "shot"],
        "anchor": anchor,
        "voice": (spoken or {}).get("voice") or "记分牌、球衣号码和官方回合都在这台云上机器读完。",
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
