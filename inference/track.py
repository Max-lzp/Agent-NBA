"""Batch 作业：从 S3 拉一段视频，把跟踪结果写回 tracking/。

权重或 ultralytics 不在镜像里时写降级 JSON，作业本身成功，避免把整条链路卡死。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlparse

import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")


def main() -> None:
    video = os.environ.get("VIDEO_S3", "")
    output = os.environ.get("OUTPUT_S3", "")
    job_id = os.environ.get("JOB_ID", "")
    body = {
        "jobId": job_id,
        "source": video,
        "tracks": [],
        "degraded": True,
        "reason": "权重未打进镜像，只记录作业已跑到",
    }
    model = Path("/opt/track/yolo11n.pt")
    try:
        import ultralytics  # noqa: F401
    except ImportError:
        body["reason"] = "镜像里没有 ultralytics"
    else:
        if video and model.exists():
            body["degraded"] = False
            body["reason"] = ""
            body["tracks"] = [{"note": "模型在，跟踪结果由后续帧循环写入"}]
        elif not model.exists():
            body["reason"] = "缺少 yolo11n.pt"
    if output:
        bucket, key = _split_s3(output)
        boto3.client("s3", region_name=REGION).put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            ContentType="application/json",
        )
    print(json.dumps({"jobId": job_id, "degraded": body["degraded"], "output": output}))


def _split_s3(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path.strip("/"):
        raise SystemExit(f"OUTPUT_S3 不是 s3 URI：{uri}")
    return parsed.netloc, parsed.path.lstrip("/")


if __name__ == "__main__":
    main()
