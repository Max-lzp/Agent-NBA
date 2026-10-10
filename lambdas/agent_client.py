"""调用 Bedrock AgentCore。冷启动和限流在这里重试，调用方决定要不要自己读清单。"""

from __future__ import annotations

import json
import re
import time

RETRYABLE = (
    "initialization time exceeded",
    "runtime initialization",
    "not ready",
    "throttl",
    "too many requests",
    "service unavailable",
    "timeout",
    "timed out",
    "connection reset",
    "connection aborted",
    "endpoint request",
    "dependencyfailed",
    "modelnotready",
    "internalserver",
    "serviceexception",
)


def session_id(job_id: str, kind: str = "pipeline") -> str:
    base = re.sub(r"[^A-Za-z0-9]", "", job_id or "") or "0"
    if kind == "qa":
        base = "q" + base
    if len(base) < 33:
        base = (base + ("0" * 40))[:40]
    return base[:40]


def retryable(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    if "validation" in name or "accessdenied" in name:
        return False
    text = f"{name} {exc}".lower()
    return any(mark in text for mark in RETRYABLE)


def parse_response(resp: dict) -> dict:
    raw = resp.get("response")
    if hasattr(raw, "read"):
        raw = raw.read()
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        raise ValueError("agent response is not a JSON object")
    return raw


def _client(region: str, read_timeout: int):
    import boto3
    from botocore.config import Config

    return boto3.client(
        "bedrock-agentcore",
        region_name=region,
        config=Config(
            connect_timeout=3,
            read_timeout=read_timeout,
            retries={"max_attempts": 1},
        ),
    )


def invoke_runtime(
    arn: str,
    payload: dict,
    *,
    job_id: str,
    kind: str = "pipeline",
    region: str = "us-east-1",
    read_timeout: int = 70,
    attempts: int = 3,
    budget: float | None = None,
    client_factory=None,
    sleep=time.sleep,
) -> tuple[dict | None, str]:
    """成功时返回解析后的 JSON。多次失败返回 (None, 最后的错误)。

    budget 是这一次调用还能用的秒数。问分析师要卡在 API Gateway 的 29 秒里，
    管线模式要卡在 Lambda 超时里。剩下的时间不够，就不再开下一次。
    """
    if not arn:
        return None, "AGENT_RUNTIME_ARN 为空"
    factory = client_factory or (lambda timeout: _client(region, timeout))
    started = time.monotonic()
    last = ""
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            pause = 2 if attempt == 2 else 5
            if budget is not None and time.monotonic() - started + pause + 4 > budget:
                return None, last
            sleep(pause)
        timeout = read_timeout
        if budget is not None:
            left = budget - (time.monotonic() - started)
            timeout = int(min(read_timeout, max(1, left - 1)))
            if timeout < 4:
                return None, last or "没有剩余时间再试"
        try:
            resp = factory(timeout).invoke_agent_runtime(
                agentRuntimeArn=arn,
                runtimeSessionId=session_id(job_id, kind),
                payload=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                qualifier="DEFAULT",
                contentType="application/json",
                accept="application/json",
            )
        except Exception as exc:  # noqa: BLE001 — 冷启动和限流要重试，权限错误立刻停
            last = str(exc)
            print(json.dumps({
                "jobId": job_id,
                "agentAttempt": attempt,
                "agentError": last[:400],
            }, ensure_ascii=False))
            if not retryable(exc) or attempt == attempts:
                return None, last
            continue
        try:
            return parse_response(resp), ""
        except (json.JSONDecodeError, ValueError) as exc:
            return None, str(exc)
    return None, last
