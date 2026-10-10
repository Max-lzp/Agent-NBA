"""记分牌 OCR。Textract 失败或解析不出数字时，置信度记 0，不把任务打成失败。"""

from __future__ import annotations

import json
import os

import boto3

from ocr_scoreboard.parse import parse_scoreboard

JOBS_TABLE = os.environ["JOBS_TABLE"]
EVENTS_TABLE = os.environ.get("EVENTS_TABLE", "")
MEDIA_BUCKET = os.environ.get("MEDIA_BUCKET", "")
REGION = os.environ.get("AWS_REGION", "us-east-1")

_dynamodb = boto3.resource("dynamodb", region_name=REGION)
_textract = boto3.client("textract", region_name=REGION)


def handler(event, _context):
    job_id = str(event.get("jobId") or "")
    bucket = str(event.get("bucket") or MEDIA_BUCKET)
    key = str(event.get("key") or "")
    event_sk = str(event.get("eventSk") or "")
    if not job_id or not bucket or not key:
        return {"ok": False, "reason": "需要 jobId、bucket、key"}

    try:
        detected = _textract.detect_document_text(
            Document={"S3Object": {"Bucket": bucket, "Name": key}},
        )
        parsed = parse_scoreboard(detected.get("Blocks") or [])
    except Exception as exc:  # noqa: BLE001 — OCR 是附加信息，不能拖垮主链路
        print(json.dumps({"jobId": job_id, "msg": "ocr failed", "error": str(exc)}))
        parsed = {
            "home": None,
            "away": None,
            "quarter": "",
            "clock": "",
            "ocr_confidence": 0,
        }

    _dynamodb.Table(JOBS_TABLE).update_item(
        Key={"jobId": job_id},
        UpdateExpression="SET scoreboard = :scoreboard",
        ExpressionAttributeValues={":scoreboard": parsed},
    )
    if event_sk and EVENTS_TABLE:
        _dynamodb.Table(EVENTS_TABLE).update_item(
            Key={"jobId": job_id, "sk": event_sk},
            UpdateExpression="SET scoreboard = :scoreboard",
            ExpressionAttributeValues={":scoreboard": parsed},
        )
    print(json.dumps({"jobId": job_id, "msg": "ocr", "confidence": parsed["ocr_confidence"]}))
    return {"ok": True, "jobId": job_id, "scoreboard": parsed}
