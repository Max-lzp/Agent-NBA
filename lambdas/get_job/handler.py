"""GET /jobs/{id}：读任务状态，并给出原始视频的短时播放地址。"""

from __future__ import annotations

import os

import boto3

import common

JOBS_TABLE = os.environ["JOBS_TABLE"]
MEDIA_BUCKET = os.environ["MEDIA_BUCKET"]
REGION = os.environ.get("AWS_REGION", "us-east-1")

_dynamodb = boto3.resource("dynamodb", region_name=REGION)
_s3 = boto3.client("s3", region_name=REGION)


def handler(event, _context):
    job_id = ((event.get("pathParameters") or {}).get("id") or "").strip()
    if not job_id:
        return common.json_response(400, {"error": "缺少 jobId"})
    item = _dynamodb.Table(JOBS_TABLE).get_item(Key={"jobId": job_id}).get("Item")
    if not item:
        return common.json_response(404, {"error": "任务不存在", "jobId": job_id})

    playback_url = ""
    key = item.get("s3Key") or ""
    if key:
        playback_url = _s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": MEDIA_BUCKET, "Key": key},
            ExpiresIn=3600,
        )
    return common.json_response(
        200,
        {
            "jobId": item.get("jobId"),
            "status": item.get("status"),
            "gameMeta": item.get("gameMeta", ""),
            "filename": item.get("filename", ""),
            "sourceUrl": item.get("sourceUrl", ""),
            "analysisMode": item.get("analysisMode", ""),
            "modelUsed": item.get("modelUsed", ""),
            "eventCount": int(item.get("eventCount") or 0),
            "error": item.get("error", ""),
            "createdAt": item.get("createdAt", ""),
            "updatedAt": item.get("updatedAt", ""),
            "playbackUrl": playback_url,
        },
    )
