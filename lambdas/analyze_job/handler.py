"""结构化清单落到 structured/{jobId}.json 后，交给 AgentCore 取回校验，再写入事件表。

原始视频上传只把任务标成「等待本地追踪」；事件只来自本地 CV 管线的清单，不再让模型看图生成。
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from urllib.parse import unquote_plus

import boto3
from boto3.dynamodb.conditions import Key

import common

JOBS_TABLE = os.environ["JOBS_TABLE"]
EVENTS_TABLE = os.environ["EVENTS_TABLE"]
MEDIA_BUCKET = os.environ["MEDIA_BUCKET"]
AGENT_RUNTIME_ARN = os.environ.get("AGENT_RUNTIME_ARN", "")
REGION = os.environ.get("AWS_REGION", "us-east-1")

_dynamodb = boto3.resource("dynamodb", region_name=REGION)
_s3 = boto3.client("s3", region_name=REGION)
_jobs = _dynamodb.Table(JOBS_TABLE)
_events = _dynamodb.Table(EVENTS_TABLE)


def handler(event, _context):
    job_id, key = _resolve_target(event)
    from trace_job import annotate
    annotate(job_id)
    if not job_id:
        print(json.dumps({"msg": "ignore event", "keys": list(event)[:8]}))
        return {"ok": False, "reason": "no job"}

    if not key.startswith("structured/"):
        _update_job(job_id, status="TRACKING", error="")
        print(json.dumps({"jobId": job_id, "msg": "waiting for structured stream", "key": key}))
        return {"ok": True, "jobId": job_id, "status": "TRACKING"}

    _update_job(job_id, status="ANALYZING", error="")
    try:
        stream, runtime = _call_agent(job_id, key)
        if stream is None:
            stream, runtime = _read_stream(key), "lambda"
        events = common.timeline_from_stream(stream)
        _replace_events(job_id, events)
        _update_job(
            job_id,
            status="DONE",
            analysisMode="structured",
            modelUsed=runtime,
            eventCount=len(events),
            error="",
        )
        print(json.dumps({"jobId": job_id, "msg": "done", "events": len(events), "runtime": runtime}))
        return {"ok": True, "jobId": job_id, "events": len(events)}
    except Exception as exc:  # noqa: BLE001 — 任务状态必须落 FAILED，不能让 S3 重试把页面卡在 ANALYZING
        print(json.dumps({"jobId": job_id, "msg": "failed", "error": str(exc)}))
        _update_job(job_id, status="FAILED", error=str(exc)[:500])
        return {"ok": False, "jobId": job_id, "error": str(exc)}


def _resolve_target(event: dict) -> tuple[str, str]:
    if event.get("jobId"):
        return str(event["jobId"]), str(event.get("key") or "")
    records = event.get("Records") or []
    if records and records[0].get("s3"):
        bucket = records[0]["s3"]["bucket"]["name"]
        key = unquote_plus(records[0]["s3"]["object"]["key"])
        if bucket != MEDIA_BUCKET:
            return "", ""
        return _job_id_from_key(key), key
    detail = event.get("detail") or {}
    obj = (detail.get("object") or {}).get("key")
    if obj:
        key = unquote_plus(obj)
        return _job_id_from_key(key), key
    return "", ""


def _job_id_from_key(key: str) -> str:
    parts = key.split("/")
    if len(parts) >= 3 and parts[0] == "raw":
        return parts[1]
    if len(parts) == 2 and parts[0] == "structured" and parts[1].endswith(".json"):
        return parts[1][: -len(".json")]
    return ""


def _read_stream(key: str) -> dict:
    return json.loads(_s3.get_object(Bucket=MEDIA_BUCKET, Key=key)["Body"].read())


def _call_agent(job_id: str, key: str) -> tuple[dict | None, str]:
    if not AGENT_RUNTIME_ARN:
        return None, ""
    client = boto3.client("bedrock-agentcore", region_name=REGION)
    session_id = (job_id.replace("-", "") + ("0" * 40))[:40]
    try:
        resp = client.invoke_agent_runtime(
            agentRuntimeArn=AGENT_RUNTIME_ARN,
            runtimeSessionId=session_id,
            payload=json.dumps({"jobId": job_id, "bucket": MEDIA_BUCKET, "key": key}).encode("utf-8"),
            qualifier="DEFAULT",
        )
    except Exception as exc:  # noqa: BLE001 — Agent 未就绪时由 Lambda 直接读清单
        print(json.dumps({"jobId": job_id, "agentError": str(exc)[:400]}))
        return None, ""
    raw = resp.get("response")
    if hasattr(raw, "read"):
        raw = raw.read()
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        return None, ""
    if not data.get("ok"):
        raise ValueError(f"清单校验未通过：{data.get('error') or '未知原因'}")
    return data.get("stream"), str(data.get("runtime") or "bedrock-agentcore")


def _replace_events(job_id: str, events: list[dict]) -> None:
    existing = _events.query(
        KeyConditionExpression=Key("jobId").eq(job_id),
        ProjectionExpression="jobId, sk",
    )
    # 同一把钥匙不能在一批里又删又写，DynamoDB 会直接拒绝。
    with _events.batch_writer() as batch:
        for item in existing.get("Items", []):
            batch.delete_item(Key={"jobId": item["jobId"], "sk": item["sk"]})
    with _events.batch_writer() as batch:
        for index, ev in enumerate(events):
            batch.put_item(
                Item={
                    "jobId": job_id,
                    "sk": f"TS#{index:04d}",
                    "eventType": ev["eventType"],
                    "timecode": ev["timecode"],
                    "label": ev["label"],
                    "confidence": Decimal(str(ev["confidence"])),
                    "payload": json.dumps(ev, ensure_ascii=False),
                }
            )


def _update_job(job_id: str, **fields) -> None:
    names = {"#updatedAt": "updatedAt"}
    values = {":updatedAt": common.iso_now()}
    sets = ["#updatedAt = :updatedAt"]
    for key, value in fields.items():
        names[f"#{key}"] = key
        values[f":{key}"] = value if not isinstance(value, int) else Decimal(value)
        sets.append(f"#{key} = :{key}")
    _jobs.update_item(
        Key={"jobId": job_id},
        UpdateExpression="SET " + ", ".join(sets),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )
