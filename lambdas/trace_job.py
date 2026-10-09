"""有 X-Ray SDK 时给当前片段打上 jobId。没有 SDK 就跳过，不打断任务。"""

from __future__ import annotations


def annotate(job_id: str) -> None:
    if not job_id:
        return
    try:
        from aws_xray_sdk.core import xray_recorder
    except ImportError:
        return
    try:
        xray_recorder.put_annotation("jobId", job_id[:200])
    except Exception:
        return
