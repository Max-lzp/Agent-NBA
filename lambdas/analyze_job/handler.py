"""视频落地后的分析：优先交给 AgentCore，失败则 Lambda 直接调 Bedrock。"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path
from urllib.parse import unquote_plus

import boto3
from boto3.dynamodb.conditions import Key

import common

JOBS_TABLE = os.environ["JOBS_TABLE"]
EVENTS_TABLE = os.environ["EVENTS_TABLE"]
MEDIA_BUCKET = os.environ["MEDIA_BUCKET"]
AGENT_RUNTIME_ARN = os.environ.get("AGENT_RUNTIME_ARN", "")
VISION_MODEL = os.environ.get("VISION_MODEL", "qwen.qwen3-vl-235b-a22b")
TEXT_MODEL = os.environ.get("TEXT_MODEL", "global.anthropic.claude-sonnet-4-6")
REGION = os.environ.get("AWS_REGION", "us-east-1")
MAX_DOWNLOAD = 40 * 1024 * 1024

_dynamodb = boto3.resource("dynamodb", region_name=REGION)
_s3 = boto3.client("s3", region_name=REGION)
_jobs = _dynamodb.Table(JOBS_TABLE)
_events = _dynamodb.Table(EVENTS_TABLE)


def handler(event, _context):
    job_id, key = _resolve_target(event)
    if not job_id:
        print(json.dumps({"msg": "ignore event", "keys": list(event)[:8]}))
        return {"ok": False, "reason": "no job"}

    job = _jobs.get_item(Key={"jobId": job_id}).get("Item") or {"jobId": job_id}
    _update_job(job_id, status="ANALYZING", error="")
    try:
        images, mode = _load_frames(key or job.get("s3Key") or "")
        payload = {
            "jobId": job_id,
            "gameMeta": job.get("gameMeta") or "",
            "filename": job.get("filename") or "",
            "sourceUrl": job.get("sourceUrl") or "",
            "images": images,
        }
        result = _call_agent(payload) or _call_bedrock(payload)
        events = common.normalize_events(result)
        _replace_events(job_id, events)
        _update_job(
            job_id,
            status="DONE",
            analysisMode=mode,
            modelUsed=str(result.get("modelUsed") or ""),
            eventCount=len(events),
            error="",
        )
        print(json.dumps({"jobId": job_id, "msg": "done", "events": len(events), "mode": mode}))
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
    return ""


def _load_frames(key: str) -> tuple[list[dict], str]:
    if not key:
        return [], "metadata"
    head = _s3.head_object(Bucket=MEDIA_BUCKET, Key=key)
    size = int(head.get("ContentLength") or 0)
    if size > MAX_DOWNLOAD:
        print(json.dumps({"msg": "skip download", "bytes": size}))
        return [], "metadata"
    dest = Path("/tmp") / "source" / Path(key).name
    dest.parent.mkdir(parents=True, exist_ok=True)
    _s3.download_file(MEDIA_BUCKET, key, str(dest))
    frames = _extract_jpegs(dest)
    dest.unlink(missing_ok=True)
    if not frames:
        return [], "metadata"
    return frames, "frames"


def _extract_jpegs(video: Path) -> list[dict]:
    ffmpeg = shutil.which("ffmpeg") or "/opt/bin/ffmpeg"
    if not Path(ffmpeg).exists():
        return []
    out_dir = Path("/tmp/frames")
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    proc = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(video),
            "-frames:v",
            "3",
            "-vf",
            "fps=1/2,scale=960:-2",
            str(out_dir / "frame-%02d.jpg"),
        ],
        check=False,
        capture_output=True,
    )
    if proc.returncode != 0:
        print(json.dumps({"msg": "ffmpeg failed", "err": proc.stderr.decode("utf-8", "replace")[:300]}))
        return []
    images = []
    for path in sorted(out_dir.glob("*.jpg"))[:3]:
        blob = path.read_bytes()
        if len(blob) > 2_500_000:
            continue
        images.append({"format": "jpeg", "bytes": blob})
    return images


def _call_agent(payload: dict) -> dict | None:
    if not AGENT_RUNTIME_ARN:
        return None
    client = boto3.client("bedrock-agentcore", region_name=REGION)
    session_id = (payload["jobId"].replace("-", "") + ("0" * 40))[:40]
    images = []
    total = 0
    for image in payload["images"]:
        if total + len(image["bytes"]) > 3_000_000:
            break
        images.append(
            {
                "format": image["format"],
                "base64": base64.b64encode(image["bytes"]).decode("ascii"),
            }
        )
        total += len(image["bytes"])
    body = {
        "jobId": payload["jobId"],
        "gameMeta": payload["gameMeta"],
        "filename": payload["filename"],
        "sourceUrl": payload["sourceUrl"],
        "frameCount": len(images),
        "images": images,
    }
    try:
        resp = client.invoke_agent_runtime(
            agentRuntimeArn=AGENT_RUNTIME_ARN,
            runtimeSessionId=session_id,
            payload=json.dumps(body).encode("utf-8"),
            qualifier="DEFAULT",
        )
    except Exception as exc:  # noqa: BLE001 — Agent 未就绪时走 Bedrock 直连
        print(json.dumps({"jobId": payload["jobId"], "agentError": str(exc)[:400]}))
        return None
    raw = resp.get("response")
    if hasattr(raw, "read"):
        raw = raw.read()
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    data = json.loads(raw)
    if isinstance(data, str):
        data = common.extract_json_object(data)
    if not isinstance(data, dict):
        return None
    data.setdefault("modelUsed", data.get("modelUsed") or "agentcore")
    return data


def _call_bedrock(payload: dict) -> dict:
    client = boto3.client("bedrock-runtime", region_name=REGION)
    text = (
        f"gameMeta={payload['gameMeta']}\nfilename={payload['filename']}\n"
        f"sourceUrl={payload['sourceUrl']}\nframes={len(payload['images'])}"
    )
    blocks: list[dict] = []
    for image in payload["images"]:
        blocks.append({"image": {"format": image["format"], "source": {"bytes": image["bytes"]}}})
    blocks.append({"text": text})
    order = [VISION_MODEL, TEXT_MODEL] if payload["images"] else [TEXT_MODEL, VISION_MODEL]
    last_error = "no model"
    for model_id in order:
        try:
            resp = client.converse(
                modelId=model_id,
                system=[{"text": common.SYSTEM_PROMPT}],
                messages=[{"role": "user", "content": blocks if payload["images"] else [{"text": text}]}],
                inferenceConfig={"maxTokens": 1800, "temperature": 0.2},
            )
            parts = resp["output"]["message"]["content"]
            raw = "".join(part.get("text", "") for part in parts)
            data = common.extract_json_object(raw)
            data["modelUsed"] = model_id
            return data
        except Exception as exc:  # noqa: BLE001 — 按方案回退另一个模型
            last_error = f"{model_id}: {exc}"
            print(json.dumps({"jobId": payload["jobId"], "bedrockError": last_error[:400]}))
    raise RuntimeError(last_error)


def _replace_events(job_id: str, events: list[dict]) -> None:
    existing = _events.query(
        KeyConditionExpression=Key("jobId").eq(job_id),
        ProjectionExpression="jobId, sk",
    )
    with _events.batch_writer() as batch:
        for item in existing.get("Items", []):
            batch.delete_item(Key={"jobId": item["jobId"], "sk": item["sk"]})
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
