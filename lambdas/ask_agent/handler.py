"""POST /agent/ask 把观众的问题交给 AgentCore。GET /agent/analysis/{jobId} 读已经写好的解说。"""

from __future__ import annotations

import json
import os

import boto3

import agent_client
import common

MEDIA_BUCKET = os.environ["MEDIA_BUCKET"]
AGENT_RUNTIME_ARN = os.environ.get("AGENT_RUNTIME_ARN", "")
REGION = os.environ.get("AWS_REGION", "us-east-1")

_s3 = boto3.client("s3", region_name=REGION)


def handler(event, _context):
    method = str(event.get("httpMethod") or "POST").upper()
    if method == "GET":
        return _analysis(event)
    try:
        body = common.parse_body(event)
    except (json.JSONDecodeError, ValueError) as exc:
        return common.json_response(400, {"error": f"无法解析请求体：{exc}"})
    job_id = str(body.get("jobId") or "").strip()
    question = str(body.get("question") or "").strip()
    if not job_id or not question:
        return common.json_response(400, {"error": "需要 jobId 和 question"})
    if any(token in job_id for token in ("/", "\\", "..")):
        return common.json_response(400, {"error": "jobId 不合法"})
    data, error = agent_client.invoke_runtime(
        AGENT_RUNTIME_ARN,
        {"jobId": job_id, "bucket": MEDIA_BUCKET, "question": question},
        job_id=job_id,
        kind="qa",
        region=REGION,
        read_timeout=18,
        attempts=2,
        budget=22,
    )
    if data is None:
        return common.json_response(503, {
            "ok": False,
            "error": "分析师暂时没有应答，请再试一次。",
            "detail": error[:300],
        })
    status = 200 if data.get("ok", True) else 502
    return common.json_response(status, data)


def _analysis(event: dict):
    job_id = str((event.get("pathParameters") or {}).get("jobId") or "").strip()
    if not job_id or any(token in job_id for token in ("/", "\\", "..")):
        return common.json_response(400, {"error": "jobId 不合法"})
    try:
        body = _s3.get_object(Bucket=MEDIA_BUCKET, Key=f"analysis/{job_id}.json")["Body"].read()
    except Exception as exc:  # noqa: BLE001
        code = str((getattr(exc, "response", {}) or {}).get("Error", {}).get("Code") or "")
        if code in {"NoSuchKey", "NotFound", "404"} or "NoSuchKey" in str(exc):
            return common.json_response(404, {"ok": False, "error": "这场还没有分析师解说", "jobId": job_id})
        return common.json_response(502, {"ok": False, "error": "读取解说失败"})
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return common.json_response(502, {"ok": False, "error": "解说文件不是 JSON"})
    return common.json_response(200, {"ok": True, "analysis": payload})
