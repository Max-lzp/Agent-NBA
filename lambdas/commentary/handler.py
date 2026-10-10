"""事件 JSON 生成两句解说，Polly 写成 MP3。失败不影响分析任务。"""

from __future__ import annotations

import json
import os
import urllib.request

import boto3

import common
from commentary.lines import two_sentences
from runtime_secrets import load_secret

MEDIA_BUCKET = os.environ["MEDIA_BUCKET"]
TEXT_MODEL = os.environ.get("TEXT_MODEL", "global.anthropic.claude-sonnet-4-6")
FEISHU_SECRET_ARN = os.environ.get("FEISHU_SECRET_ARN", "")
REGION = os.environ.get("AWS_REGION", "us-east-1")

_bedrock = boto3.client("bedrock-runtime", region_name=REGION)
_polly = boto3.client("polly", region_name=REGION)
_s3 = boto3.client("s3", region_name=REGION)


def handler(event, _context):
    try:
        body = common.parse_body(event)
    except (json.JSONDecodeError, ValueError) as exc:
        return common.json_response(400, {"error": f"无法解析请求体：{exc}"})
    job_id = str(body.get("jobId") or "demo")[:80]
    from trace_job import annotate
    annotate(job_id)
    events = body.get("events") if isinstance(body.get("events"), list) else []
    script = two_sentences(_write_script(events, str(body.get("gameMeta") or "")))
    if not script:
        script = "这一回合先看持球人，再看协防有没有赶到。"
    audio = _polly.synthesize_speech(
        Text=script,
        OutputFormat="mp3",
        VoiceId="Zhiyu",
        Engine="neural",
        LanguageCode="cmn-CN",
    )
    key = f"commentary/{job_id}.mp3"
    _s3.put_object(
        Bucket=MEDIA_BUCKET,
        Key=key,
        Body=audio["AudioStream"].read(),
        ContentType="audio/mpeg",
    )
    url = _s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": MEDIA_BUCKET, "Key": key},
        ExpiresIn=3600,
    )
    _notify(job_id, script)
    return common.json_response(200, {"jobId": job_id, "script": script, "audioUrl": url, "key": key})


def _write_script(events: list, game_meta: str) -> str:
    prompt = (
        "用中文写两句篮球解说，每句不超过 30 字。只根据下面的事件，不要编造没出现的球员和数据。\n"
        f"gameMeta: {game_meta}\n"
        f"events: {json.dumps(events, ensure_ascii=False)[:2000]}"
    )
    response = _bedrock.converse(
        modelId=TEXT_MODEL,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 200, "temperature": 0.3},
    )
    blocks = response.get("output", {}).get("message", {}).get("content") or []
    return "".join(block.get("text", "") for block in blocks if isinstance(block, dict))


def _notify(job_id: str, script: str) -> None:
    secret = load_secret(FEISHU_SECRET_ARN, REGION)
    webhook = str(secret.get("webhook") or "")
    if not webhook.startswith("https://"):
        return
    payload = json.dumps({"msg_type": "text", "content": {"text": f"Deep Court {job_id}\n{script}"}}).encode("utf-8")
    request = urllib.request.Request(webhook, data=payload, headers={"content-type": "application/json"})
    try:
        urllib.request.urlopen(request, timeout=3).read()
    except Exception:
        return
