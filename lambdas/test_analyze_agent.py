"""analyze_job 的重试、兜底，以及 /agent/ask。不连 AWS。"""

from __future__ import annotations

import json
import os
import unittest

os.environ.setdefault("JOBS_TABLE", "jobs")
os.environ.setdefault("EVENTS_TABLE", "events")
os.environ.setdefault("MEDIA_BUCKET", "media")
os.environ.setdefault("AGENT_RUNTIME_ARN", "arn:aws:bedrock-agentcore:us-east-1:1:runtime/DeepCourtAnalyst")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")

import agent_client
from analyze_job import handler as analyze
from ask_agent import handler as ask


class RuntimeClientTest(unittest.TestCase):
    def test_session_id_is_long_enough(self):
        pipeline = agent_client.session_id("job-1", "pipeline")
        question = agent_client.session_id("job-1", "qa")
        self.assertGreaterEqual(len(pipeline), 33)
        self.assertGreaterEqual(len(question), 33)
        self.assertNotEqual(pipeline, question)
        self.assertTrue(pipeline[0].isalnum())

    def test_init_timeout_is_retried(self):
        class Runtime:
            def __init__(self):
                self.calls = 0

            def invoke_agent_runtime(self, **kwargs):
                self.calls += 1
                self.kwargs = kwargs
                if self.calls == 1:
                    raise RuntimeError(
                        "Runtime initialization time exceeded. "
                        "Please make sure that initialization completes in 30s."
                    )
                return {"response": json.dumps({"ok": True, "stream": {"possessions": []}}).encode()}

        runtime = Runtime()
        data, error = agent_client.invoke_runtime(
            "arn:aws:bedrock-agentcore:us-east-1:1:runtime/DeepCourtAnalyst",
            {"jobId": "job-1"},
            job_id="job-1",
            attempts=3,
            client_factory=lambda timeout: runtime,
            sleep=lambda seconds: None,
        )
        self.assertEqual(error, "")
        self.assertTrue(data["ok"])
        self.assertEqual(runtime.calls, 2)
        self.assertGreaterEqual(len(runtime.kwargs["runtimeSessionId"]), 33)

    def test_access_denied_is_not_retried(self):
        class AccessDeniedException(Exception):
            pass

        class Runtime:
            def __init__(self):
                self.calls = 0

            def invoke_agent_runtime(self, **kwargs):
                del kwargs
                self.calls += 1
                raise AccessDeniedException("denied")

        runtime = Runtime()
        data, error = agent_client.invoke_runtime(
            "arn",
            {"jobId": "job-1"},
            job_id="job-1",
            attempts=3,
            client_factory=lambda timeout: runtime,
            sleep=lambda seconds: None,
        )
        self.assertIsNone(data)
        self.assertIn("denied", error)
        self.assertEqual(runtime.calls, 1)


class _Body:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode()


class _S3:
    def __init__(self, payload):
        self.payload = payload

    def get_object(self, Bucket, Key):
        del Bucket
        self.key = Key
        return {"Body": _Body(self.payload)}


class _Table:
    def __init__(self):
        self.items = []
        self.updates = []

    def query(self, **kwargs):
        del kwargs
        return {"Items": []}

    def batch_writer(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def delete_item(self, **kwargs):
        del kwargs

    def put_item(self, Item):
        self.items.append(Item)

    def update_item(self, **kwargs):
        self.updates.append(kwargs)


def _run(stream, agent_result):
    events = _Table()
    jobs = _Table()
    analyze._s3 = _S3(stream)
    analyze._events = events
    analyze._jobs = jobs
    original = agent_client.invoke_runtime
    agent_client.invoke_runtime = lambda *args, **kwargs: agent_result
    try:
        result = analyze.handler({
            "Records": [{
                "s3": {
                    "bucket": {"name": "media"},
                    "object": {"key": "structured/job-9.json"},
                },
            }],
        }, None)
    finally:
        agent_client.invoke_runtime = original
    return result, events, jobs


def _valid_stream():
    return {
        "version": "0.1",
        "possessions": [{
            "possession_id": "p0",
            "offense_team": "HOU",
            "events": [{
                "t": 3,
                "type": "shot",
                "actor": {"team": "HOU", "number": "1", "name": "Amen"},
                "confidence": 0.8,
                "source": "cv",
            }],
        }],
    }


class AnalyzeFallbackTest(unittest.TestCase):
    def test_cold_start_falls_back_to_the_manifest(self):
        result, events, jobs = _run(_valid_stream(), (None, "Runtime initialization time exceeded"))
        self.assertTrue(result["ok"])
        self.assertEqual(len(events.items), 1)
        models = [
            item["ExpressionAttributeValues"][":modelUsed"]
            for item in jobs.updates
            if ":modelUsed" in item["ExpressionAttributeValues"]
        ]
        self.assertEqual(models, ["lambda"])

    def test_other_agent_failure_also_falls_back(self):
        result, events, _jobs = _run(_valid_stream(), ({"ok": False, "error": "模型超时"}, ""))
        self.assertTrue(result["ok"])
        self.assertEqual(len(events.items), 1)

    def test_invalid_manifest_fails_the_job(self):
        result, events, jobs = _run(
            {"version": "0.9", "possessions": []},
            ({"ok": False, "reason": "invalid_manifest", "error": "version 应为 0.1", "stream": None}, ""),
        )
        self.assertFalse(result["ok"])
        self.assertEqual(events.items, [])
        self.assertTrue(any("清单校验" in str(item) for item in jobs.updates))

    def test_invalid_manifest_fails_when_agent_is_down(self):
        result, events, _jobs = _run({"version": "0.9", "possessions": []}, (None, "timeout"))
        self.assertFalse(result["ok"])
        self.assertIn("清单校验", result["error"])
        self.assertEqual(events.items, [])


class AskHandlerTest(unittest.TestCase):
    def test_missing_question_is_rejected(self):
        response = ask.handler({"httpMethod": "POST", "body": json.dumps({"jobId": "job-1"})}, None)
        self.assertEqual(response["statusCode"], 400)

    def test_passes_question_to_runtime(self):
        captured = {}

        def fake_invoke(arn, payload, **kwargs):
            captured["payload"] = payload
            captured["kind"] = kwargs.get("kind")
            captured["attempts"] = kwargs.get("attempts")
            return {"ok": True, "mode": "qa", "answer": {"zh": "好的", "en": "ok", "sources": []}}, ""

        original = agent_client.invoke_runtime
        agent_client.invoke_runtime = fake_invoke
        try:
            response = ask.handler({
                "httpMethod": "POST",
                "body": json.dumps({"jobId": "job-1", "question": "谁出手了？"}),
            }, None)
        finally:
            agent_client.invoke_runtime = original
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(captured["payload"]["question"], "谁出手了？")
        self.assertEqual(captured["payload"]["bucket"], "media")
        self.assertEqual(captured["kind"], "qa")
        self.assertGreaterEqual(captured["attempts"], 2)


if __name__ == "__main__":
    unittest.main()
