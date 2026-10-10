"""提交 Batch 跟踪作业。不由分析函数调用，P0 闭环不依赖这里。"""

from __future__ import annotations

import json
import os
import re

import boto3

JOB_QUEUE = os.environ["JOB_QUEUE"]
JOB_DEFINITION = os.environ["JOB_DEFINITION"]
MEDIA_BUCKET = os.environ["MEDIA_BUCKET"]
REGION = os.environ.get("AWS_REGION", "us-east-1")

_batch = boto3.client("batch", region_name=REGION)


def handler(event, _context):
    job_id = str(event.get("jobId") or "")
    key = str(event.get("key") or "")
    if not job_id or not key.startswith("raw/"):
        return {"ok": False, "reason": "需要 jobId，且 key 以 raw/ 开头"}
    name = re.sub(r"[^A-Za-z0-9_-]", "", f"track-{job_id}")[:128] or "track"
    output = f"s3://{MEDIA_BUCKET}/tracking/{job_id}/tracks.json"
    video = f"s3://{MEDIA_BUCKET}/{key}"
    result = _batch.submit_job(
        jobName=name,
        jobQueue=JOB_QUEUE,
        jobDefinition=JOB_DEFINITION,
        containerOverrides={
            "environment": [
                {"name": "JOB_ID", "value": job_id},
                {"name": "VIDEO_S3", "value": video},
                {"name": "OUTPUT_S3", "value": output},
            ],
        },
    )
    print(json.dumps({"jobId": job_id, "batchJobId": result.get("jobId"), "output": output}))
    return {"ok": True, "jobId": job_id, "batchJobId": result.get("jobId"), "output": output}
