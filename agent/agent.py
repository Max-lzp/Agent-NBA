"""Deep Court 编排 Agent，由 Bedrock AgentCore Runtime 托管。

事件来自本地 CV 管线产出的结构化清单（pipeline/events，schema 0.1），
Agent 只负责取回、校验并交还，不让任何模型看图生成事件。
"""

from __future__ import annotations

import json
import os

import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()
REGION = os.environ.get("AWS_REGION", "us-east-1")
SCHEMA_VERSION = "0.1"
TEAMS = {"HOU", "DAL"}


@app.entrypoint
def invoke(payload, context=None):
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        payload = {}
    bucket, key = payload.get("bucket") or "", payload.get("key") or ""
    if not bucket or not key:
        return {"ok": False, "error": "缺少 bucket/key", "runtime": "bedrock-agentcore"}
    body = boto3.client("s3", region_name=REGION).get_object(Bucket=bucket, Key=key)["Body"].read()
    stream = json.loads(body)
    problems = check(stream)
    return {
        "ok": not problems,
        "error": "；".join(problems),
        "stream": stream if not problems else None,
        "runtime": "bedrock-agentcore",
    }


def check(stream) -> list[str]:
    if not isinstance(stream, dict):
        return ["清单不是 JSON 对象"]
    problems = []
    if stream.get("version") != SCHEMA_VERSION:
        problems.append(f"version 应为 {SCHEMA_VERSION}")
    possessions = stream.get("possessions")
    if not isinstance(possessions, list) or not possessions:
        return problems + ["possessions 为空"]
    for poss in possessions:
        if poss.get("offense_team") not in TEAMS:
            problems.append(f"{poss.get('possession_id')} 进攻方不是 HOU/DAL")
        if not isinstance(poss.get("events"), list):
            problems.append(f"{poss.get('possession_id')} 缺少 events")
    return problems


if __name__ == "__main__":
    app.run()
