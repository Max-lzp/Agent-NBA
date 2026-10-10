"""Deep Court 分析师。由 Bedrock AgentCore Runtime 托管。

入口只用标准库，先在 8080 响应 /ping。boto3 留到第一次真正调用再导入。
上次初始化在 30 秒内失败，是因为代码包没带上 bedrock-agentcore，进程导入即崩，
平台把这个错误报成 Runtime initialization time exceeded。
"""

from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from converse_loop import answer, pipeline
from tools import Ctx, Media

REGION = os.environ.get("AWS_REGION", "us-east-1")
RUNTIME = "bedrock-agentcore"


def parse_payload(payload) -> dict:
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        if not payload.strip():
            return {}
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        return {}
    return payload


def resolve(payload: dict) -> tuple[str, str, str]:
    bucket = str(payload.get("bucket") or os.environ.get("MEDIA_BUCKET") or "")
    key = str(payload.get("key") or "")
    job_id = str(payload.get("jobId") or payload.get("job_id") or "")
    if not job_id and key.startswith("structured/") and key.endswith(".json"):
        job_id = key.rsplit("/", 1)[-1][: -len(".json")]
    if not key and job_id:
        key = f"structured/{job_id}.json"
    return bucket, job_id, key


def _s3():
    import boto3

    return boto3.client("s3", region_name=REGION)


def invoke(payload, context=None, s3=None, converse=None):
    del context
    try:
        data = parse_payload(payload)
    except json.JSONDecodeError:
        return {"ok": False, "error": "payload 不是 JSON", "runtime": RUNTIME}
    bucket, job_id, key = resolve(data)
    question = str(data.get("question") or "").strip()
    if not bucket or not job_id:
        return {"ok": False, "error": "缺少 bucket 或 jobId", "runtime": RUNTIME}
    if any(token in job_id for token in ("/", "\\", "..")):
        return {"ok": False, "error": "jobId 不合法", "runtime": RUNTIME}
    ctx = Ctx(media=Media(s3 or _s3(), bucket), bucket=bucket, job_id=job_id, key=key)
    data = dict(data)
    data["jobId"] = job_id
    data["bucket"] = bucket
    data["key"] = key
    if question:
        data["question"] = question
        return answer(data, ctx, converse)
    return pipeline(data, ctx, converse)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        if self.path.split("?", 1)[0] != "/ping":
            self._send(404, {"error": "not found"})
            return
        self._send(200, {"status": "Healthy", "time_of_last_update": int(time.time())})

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/invocations":
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            self._send(200, invoke(raw))
        except Exception as exc:  # noqa: BLE001 — 运行时要收到 JSON，而不是断连接
            self._send(500, {"ok": False, "error": str(exc)[:500], "runtime": RUNTIME})

    def log_message(self, fmt, *args):
        return

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve() -> None:
    port = int(os.environ.get("PORT", "8080"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    serve()
