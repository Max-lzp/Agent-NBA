"""GET /jobs/{id}/events：按时间顺序读出事件。"""

from __future__ import annotations

import json
import os

import boto3
from boto3.dynamodb.conditions import Key

import common

EVENTS_TABLE = os.environ["EVENTS_TABLE"]
REGION = os.environ.get("AWS_REGION", "us-east-1")

_table = boto3.resource("dynamodb", region_name=REGION).Table(EVENTS_TABLE)


def handler(event, _context):
    job_id = ((event.get("pathParameters") or {}).get("id") or "").strip()
    if not job_id:
        return common.json_response(400, {"error": "缺少 jobId"})
    result = _table.query(KeyConditionExpression=Key("jobId").eq(job_id), ScanIndexForward=True)
    events = []
    for item in result.get("Items", []):
        payload = item.get("payload") or "{}"
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            parsed = {
                "eventType": item.get("eventType", ""),
                "timecode": item.get("timecode", ""),
                "label": item.get("label", ""),
            }
        events.append(parsed)
    return common.json_response(200, {"jobId": job_id, "events": events})
