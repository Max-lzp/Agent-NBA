"""POST /jobs：写入 DynamoDB，并返回 S3 预签名上传地址。"""

from __future__ import annotations

import json
import os

import boto3

import common

JOBS_TABLE = os.environ["JOBS_TABLE"]
MEDIA_BUCKET = os.environ["MEDIA_BUCKET"]
ANALYZE_FUNCTION_NAME = os.environ.get("ANALYZE_FUNCTION_NAME", "")
REGION = os.environ.get("AWS_REGION", "us-east-1")

_dynamodb = boto3.resource("dynamodb", region_name=REGION)
_s3 = boto3.client("s3", region_name=REGION)
_lambda = boto3.client("lambda", region_name=REGION)


def handler(event, _context):
    try:
        body = common.parse_body(event)
    except (json.JSONDecodeError, ValueError) as exc:
        return common.json_response(400, {"error": f"无法解析请求体：{exc}"})

    filename = str(body.get("filename") or "").strip()
    source_url = str(body.get("sourceUrl") or "").strip()
    game_meta = str(body.get("gameMeta") or "unknown").strip()[:120]
    content_type = str(body.get("contentType") or "application/octet-stream").strip()[:80]
    if not filename and not source_url:
        return common.json_response(400, {"error": "需要 filename 或 sourceUrl"})
    if source_url and not source_url.startswith(("http://", "https://")):
        return common.json_response(400, {"error": "sourceUrl 必须是 http(s) 链接"})

    job_id = common.new_job_id()
    now = common.iso_now()
    key = common.object_key(job_id, filename) if filename else ""
    item = {
        "jobId": job_id,
        "status": "PENDING",
        "gameMeta": game_meta,
        "filename": common.safe_filename(filename) if filename else "",
        "sourceUrl": source_url,
        "s3Key": key,
        "contentType": content_type if filename else "",
        "createdAt": now,
        "updatedAt": now,
    }
    _dynamodb.Table(JOBS_TABLE).put_item(Item=item)

    upload_url = ""
    upload_headers: dict[str, str] = {}
    if key:
        upload_url = _s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": MEDIA_BUCKET, "Key": key, "ContentType": content_type},
            ExpiresIn=3600,
        )
        upload_headers = {"Content-Type": content_type}
    elif ANALYZE_FUNCTION_NAME:
        _lambda.invoke(
            FunctionName=ANALYZE_FUNCTION_NAME,
            InvocationType="Event",
            Payload=json.dumps({"jobId": job_id, "mode": "url"}).encode("utf-8"),
        )

    print(json.dumps({"jobId": job_id, "msg": "job created", "hasUpload": bool(upload_url)}))
    return common.json_response(
        201,
        {
            "jobId": job_id,
            "status": "PENDING",
            "uploadUrl": upload_url,
            "uploadHeaders": upload_headers,
            "key": key,
            "gameMeta": game_meta,
        },
    )
