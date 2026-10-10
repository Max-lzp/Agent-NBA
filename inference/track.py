"""Batch 作业：在 CPU 大机器上跑 YOLO，把轨迹写回 S3。

权重放在媒体桶 bootstrap/models/。G/VT 配额为 0，这里固定用 CPU，靠核数。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlparse

import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")
SAMPLE_FPS = 8
BATCH = 32
MODELS = ("player_detector.pt", "ball_detector_model.pt")


def main() -> None:
    video = os.environ.get("VIDEO_S3", "")
    output = os.environ.get("OUTPUT_S3", "")
    job_id = os.environ.get("JOB_ID", "")
    bucket_name = os.environ.get("MEDIA_BUCKET", "")
    threads = int(os.environ.get("OMP_NUM_THREADS", "8"))
    body: dict = {
        "jobId": job_id,
        "source": video,
        "device": "cpu",
        "threads": threads,
        "sampled_fps": SAMPLE_FPS,
        "tracks": [],
        "degraded": True,
        "reason": "",
    }
    try:
        frames = _track(bucket_name, video, threads)
    except Exception as exc:  # noqa: BLE001 — 写明原因再退出，避免队列里只看到容器挂了
        body["reason"] = str(exc)
        _put(output, body)
        raise
    body["degraded"] = False
    body["tracks"] = frames
    body["frame_count"] = len(frames)
    _put(output, body)
    print(json.dumps({"jobId": job_id, "frames": len(frames), "output": output}))


def _track(bucket_name: str, video: str, threads: int) -> list[dict]:
    if not bucket_name or not video:
        raise RuntimeError("缺少 MEDIA_BUCKET 或 VIDEO_S3")
    s3 = boto3.client("s3", region_name=REGION)
    model_dir = Path("/tmp/models")
    model_dir.mkdir(parents=True, exist_ok=True)
    for name in MODELS:
        dest = model_dir / name
        s3.download_file(bucket_name, f"bootstrap/models/{name}", str(dest))
    local_video = Path("/tmp/video.mp4")
    video_bucket, video_key = _split_s3(video)
    s3.download_file(video_bucket, video_key, str(local_video))

    import cv2
    import torch
    from ultralytics import YOLO

    torch.set_num_threads(threads)
    cv2.setNumThreads(threads)
    player = YOLO(str(model_dir / "player_detector.pt"))
    ball = YOLO(str(model_dir / "ball_detector_model.pt"))
    cap = cv2.VideoCapture(str(local_video))
    if not cap.isOpened():
        raise RuntimeError("打不开视频")
    source_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, round(source_fps / SAMPLE_FPS))
    batch: list = []
    stamps: list[float] = []
    out: list[dict] = []
    index = 0

    def flush() -> None:
        if not batch:
            return
        people = player.predict(batch, device="cpu", verbose=False, conf=0.25)
        balls = ball.predict(batch, device="cpu", verbose=False, conf=0.25)
        for stamp, person, ball_result in zip(stamps, people, balls):
            out.append({
                "t": stamp,
                "players": _boxes(person),
                "ball": (_boxes(ball_result) or [None])[0],
            })
        batch.clear()
        stamps.clear()
        print(f"tracked {len(out)} frames", flush=True)

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if index % step == 0:
            batch.append(frame)
            stamps.append(round(index / source_fps, 3))
            if len(batch) == BATCH:
                flush()
        index += 1
    cap.release()
    flush()
    return out


def _boxes(result) -> list[list[float]]:
    boxes = []
    if result.boxes is None:
        return boxes
    for row in result.boxes.data.tolist():
        x1, y1, x2, y2 = row[:4]
        conf = row[4] if len(row) > 4 else 0
        cls = row[5] if len(row) > 5 else -1
        boxes.append([round(x1), round(y1), round(x2 - x1), round(y2 - y1), round(conf, 3), int(cls)])
    return boxes


def _put(output: str, body: dict) -> None:
    if not output:
        print(json.dumps(body, ensure_ascii=False))
        return
    bucket, key = _split_s3(output)
    boto3.client("s3", region_name=REGION).put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )


def _split_s3(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path.strip("/"):
        raise RuntimeError(f"不是 s3 URI：{uri}")
    return parsed.netloc, parsed.path.lstrip("/")


if __name__ == "__main__":
    main()
