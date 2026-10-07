"""Deep Court 分析 Agent。由 Bedrock AgentCore Runtime 托管，内部调用 Bedrock。"""

from __future__ import annotations

import base64
import json
import os

import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()
REGION = os.environ.get("AWS_REGION", "us-east-1")
TEXT_MODEL = os.environ.get("TEXT_MODEL", "global.anthropic.claude-sonnet-4-6")
VISION_MODEL = os.environ.get("VISION_MODEL", "qwen.qwen3-vl-235b-a22b")

SYSTEM_PROMPT = (
    "你是 Deep Court 的比赛事件分析器，跑在 Amazon Bedrock AgentCore 上。"
    "只输出一个 JSON 对象，不要 Markdown。"
    "格式："
    '{"events":[{"eventType":"shot|rebound|turnover|pass|screen|hold",'
    '"timecode":"MM:SS","players":[],"confidence":0.0,"label":"短标签","detail":""}]}'
    "没有画面时 confidence 不得超过 0.4，detail 以「未抽帧」开头。"
    "不要编造输入里没有的球员数据。事件 3 到 8 条。"
)


@app.entrypoint
def invoke(payload, context=None):
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        payload = {}
    text = (
        f"jobId={payload.get('jobId', '')}\n"
        f"gameMeta={payload.get('gameMeta', '')}\n"
        f"filename={payload.get('filename', '')}\n"
        f"sourceUrl={payload.get('sourceUrl', '')}\n"
        f"frameCount={payload.get('frameCount', 0)}"
    )
    blocks: list[dict] = []
    for image in payload.get("images") or []:
        encoded = image.get("base64")
        if not encoded:
            continue
        blocks.append(
            {
                "image": {
                    "format": image.get("format") or "jpeg",
                    "source": {"bytes": base64.b64decode(encoded)},
                }
            }
        )
    blocks.append({"text": text})
    model_used, raw = _converse(blocks, bool(blocks[:-1]))
    start = raw.find("{")
    end = raw.rfind("}")
    data = json.loads(raw[start : end + 1]) if start >= 0 and end > start else {"events": []}
    data["modelUsed"] = model_used
    data["runtime"] = "bedrock-agentcore"
    return data


def _converse(blocks: list[dict], has_frames: bool) -> tuple[str, str]:
    client = boto3.client("bedrock-runtime", region_name=REGION)
    order = [VISION_MODEL, TEXT_MODEL] if has_frames else [TEXT_MODEL, VISION_MODEL]
    last_error = "no model"
    for model_id in order:
        try:
            resp = client.converse(
                modelId=model_id,
                system=[{"text": SYSTEM_PROMPT}],
                messages=[{"role": "user", "content": blocks}],
                inferenceConfig={"maxTokens": 1800, "temperature": 0.2},
            )
            parts = resp["output"]["message"]["content"]
            return model_id, "".join(part.get("text", "") for part in parts)
        except Exception as exc:  # noqa: BLE001 — 视觉模型不可用时回退文本模型
            last_error = f"{model_id}: {exc}"
    raise RuntimeError(last_error)


if __name__ == "__main__":
    app.run()
